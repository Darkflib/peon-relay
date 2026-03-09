from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from peon_relay.cesp import CESPManager, load_packs
from peon_relay.config import Settings
from peon_relay.handlers import HandlerRegistry
from peon_relay.handlers.audio import AudioHandler, detect_audio_tool
from peon_relay.handlers.log import LogHandler
from peon_relay.handlers.notification import (
    DesktopSink,
    NotificationHandler,
    NotificationSink,
)
from peon_relay.hooks import process_hook
from peon_relay.queue import EventQueue, PeonEvent
from peon_relay.registry import RegistryClient

logger = structlog.get_logger()

# Module-level references set during lifespan
_queue: EventQueue | None = None
_cesp: CESPManager | None = None
_config: Settings | None = None
_registry: RegistryClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _queue, _cesp, _config, _registry

    config = Settings.load()
    _config = config

    import logging

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(config.log.level)
        ),
    )

    cesp = load_packs(config.audio.pack_dir, config.audio.active_pack, port=config.server.port)
    _cesp = cesp

    _registry = RegistryClient(config.registry, config.audio.pack_dir)

    audio_tool = detect_audio_tool() if config.audio.enabled else None
    audio_handler = AudioHandler(
        tool=audio_tool,
        volume=config.audio.volume,
        mute=config.audio.mute,
        disabled_categories=config.audio.disabled_categories,
    )
    log_handler = LogHandler(default_pack=config.audio.active_pack)

    notification_sinks: list[NotificationSink] = []
    if config.notification.enabled:
        if config.notification.desktop.enabled:
            notification_sinks.append(DesktopSink())
    notification_handler = NotificationHandler(
        sinks=notification_sinks,
        disabled_categories=config.notification.disabled_categories,
    )

    registry = HandlerRegistry([log_handler, audio_handler, notification_handler])

    queue = EventQueue(
        cesp=cesp,
        registry=registry,
        debounce_ms=config.audio.debounce_ms,
    )
    _queue = queue
    queue.start()

    logger.info(
        "relay_started",
        host=config.server.host,
        port=config.server.port,
        active_pack=config.audio.active_pack,
        audio_tool=audio_tool,
    )

    yield

    await queue.stop()
    logger.info("relay_stopped")


app = FastAPI(title="peon-relay", lifespan=lifespan)


def _resolve_pack(request: Request) -> str | None:
    """Resolve pack: X-Peon-Pack header > client IP mapping > None (default)."""
    header = request.headers.get("x-peon-pack")
    if header:
        return header
    if _config and _config.audio.client_packs:
        client_ip = request.client.host if request.client else None
        if client_ip and client_ip in _config.audio.client_packs:
            return _config.audio.client_packs[client_ip]
    return None


@app.post("/hook")
async def hook_endpoint(request: Request) -> JSONResponse:
    payload: dict[str, Any] = await request.json()
    pack = _resolve_pack(request)
    result = process_hook(payload, _queue, pack=pack)
    return JSONResponse(result)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "queue_depth": _queue.depth if _queue else 0,
        "active_pack": _config.audio.active_pack if _config else "",
    }


@app.get("/packs")
async def packs() -> list[dict]:
    if _cesp is None:
        return []
    return [
        {
            "name": p.name,
            "display_name": p.display_name,
            "categories": p.categories,
            "sound_count": p.sound_count,
            "active": p.active,
        }
        for p in _cesp.list_packs()
    ]


KNOWN_CATEGORIES = {
    "session.start",
    "session.end",
    "task.acknowledge",
    "task.complete",
    "task.error",
    "task.progress",
    "input.required",
    "resource.limit",
    "user.spam",
}


@app.post("/test/{category}")
async def test_category(category: str) -> JSONResponse:
    if category not in KNOWN_CATEGORIES:
        return JSONResponse(
            {"status": "error", "message": f"Unknown category: {category}"},
            status_code=404,
        )
    event = PeonEvent(
        category=category,
        session_id="test",
        timestamp=time.monotonic(),
    )
    _queue.enqueue(event)
    return JSONResponse({"status": "queued", "category": category})


@app.get("/registry/packs")
async def registry_packs(
    search: str | None = None,
    category: str | None = None,
    trust_tier: str | None = None,
) -> list[dict]:
    if _registry is None:
        return []
    available = await _registry.list_available(
        search=search, category=category, trust_tier=trust_tier
    )
    installed = _registry.installed_packs()
    return [
        {**pack.model_dump(), "installed": pack.name in installed}
        for pack in available
    ]


@app.post("/registry/install/{pack_name}")
async def install_pack(pack_name: str) -> JSONResponse:
    if _registry is None or _cesp is None:
        return JSONResponse(
            {"status": "error", "message": "Registry not initialized"},
            status_code=503,
        )

    result = await _registry.install_pack(pack_name)
    if not result.success:
        return JSONResponse(
            {
                "status": "error",
                "pack": result.pack_name,
                "message": result.message,
            },
            status_code=400,
        )

    # Hot-reload the pack into CESPManager
    pack_path = Path(_config.audio.pack_dir) / pack_name if _config else None
    if pack_path:
        _cesp.load_single_pack(pack_path)

    return JSONResponse(
        {
            "status": "installed",
            "pack": result.pack_name,
            "version": result.version,
            "message": result.message,
        }
    )


@app.delete("/registry/packs/{pack_name}")
async def uninstall_pack(pack_name: str) -> JSONResponse:
    if _registry is None or _cesp is None:
        return JSONResponse(
            {"status": "error", "message": "Registry not initialized"},
            status_code=503,
        )

    if _config and pack_name == _config.audio.active_pack:
        return JSONResponse(
            {
                "status": "error",
                "message": f"Cannot uninstall active pack '{pack_name}'",
            },
            status_code=409,
        )

    if not _registry.uninstall_pack(pack_name):
        return JSONResponse(
            {
                "status": "error",
                "message": f"Pack '{pack_name}' is not installed",
            },
            status_code=404,
        )

    _cesp.remove_pack(pack_name)
    return JSONResponse({"status": "uninstalled", "pack": pack_name})


def run() -> None:
    config = Settings.load()
    uvicorn.run(
        "peon_relay.main:app",
        host=config.server.host,
        port=config.server.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
