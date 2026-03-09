from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from peon_relay.queue import PeonEvent

logger = structlog.get_logger()


class BaseHandler:
    async def handle(self, event: PeonEvent, sound_path: Path | None) -> None:
        raise NotImplementedError


class HandlerRegistry:
    def __init__(self, handlers: list[BaseHandler]) -> None:
        self._handlers = handlers

    async def dispatch(
        self, event: PeonEvent, sound_path: Path | None
    ) -> None:
        tasks = []
        for handler in self._handlers:
            tasks.append(self._safe_handle(handler, event, sound_path))
        await asyncio.gather(*tasks)

    async def _safe_handle(
        self,
        handler: BaseHandler,
        event: PeonEvent,
        sound_path: Path | None,
    ) -> None:
        try:
            await asyncio.wait_for(
                handler.handle(event, sound_path), timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.error(
                "handler_timeout",
                handler=type(handler).__name__,
                category=event.category,
            )
        except Exception:
            logger.exception(
                "handler_error",
                handler=type(handler).__name__,
                category=event.category,
            )
