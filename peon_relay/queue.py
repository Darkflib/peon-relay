from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from peon_relay.cesp import CESPManager
    from peon_relay.handlers import HandlerRegistry

logger = structlog.get_logger()


@dataclass
class PeonEvent:
    category: str
    session_id: str
    timestamp: float


class EventQueue:
    def __init__(
        self,
        cesp: CESPManager,
        registry: HandlerRegistry,
        debounce_ms: int = 500,
    ) -> None:
        self._queue: asyncio.Queue[PeonEvent] = asyncio.Queue()
        self._cesp = cesp
        self._registry = registry
        self._debounce_ms = debounce_ms
        self._last_dispatched: dict[str, float] = {}
        self._task: asyncio.Task | None = None

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    def enqueue(self, event: PeonEvent) -> None:
        self._queue.put_nowait(event)

    def start(self) -> None:
        self._task = asyncio.create_task(self._drain())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _drain(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                now = time.monotonic()
                last = self._last_dispatched.get(event.category)
                if last is not None:
                    elapsed_ms = (now - last) * 1000
                    if elapsed_ms < self._debounce_ms:
                        logger.debug(
                            "event_debounced",
                            category=event.category,
                            elapsed_ms=int(elapsed_ms),
                        )
                        continue

                sound_path = self._cesp.pick_sound(event.category)
                await self._registry.dispatch(event, sound_path)
                self._last_dispatched[event.category] = time.monotonic()

            except Exception:
                logger.exception("drain_error", category=event.category)
            finally:
                self._queue.task_done()
