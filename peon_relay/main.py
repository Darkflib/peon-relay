from __future__ import annotations

import time
from contextlib import asynccontextmanager
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
from peon_relay.hooks import process_hook
from peon_relay.queue import EventQueue, PeonEvent

logger = structlog.get_logger()

# Module-level references set during lifespan
_queue: EventQueue | None = None
_cesp: CESPManager | None = None
_config: Settings | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _queue, _cesp, _config

    config = Settings.load()
    _config = config

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            structlog.get_level_from_name(config.log.level)
        ),
    )

    cesp = load_packs(config.audio.pack_dir, config.audio.active_pack)
    _cesp = cesp

    audio_tool = detect_audio_tool() if config.audio.enabled else None
    audio_handler = AudioHandler(
        tool=audio_tool,
        volume=config.audio.volume,
        mute=config.audio.mute,
        disabled_categories=config.audio.disabled_categories,
    )
    log_handler = LogHandler(default_pack=config.audio.active_pack)

    registry = HandlerRegistry([log_handler, audio_handler])

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
