from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from peon_relay.cesp import CESPManager, Pack
from peon_relay.handlers import BaseHandler, HandlerRegistry
from peon_relay.queue import EventQueue, PeonEvent


class MockHandler(BaseHandler):
    def __init__(self):
        self.events: list[tuple[PeonEvent, Path | None]] = []

    async def handle(self, event: PeonEvent, sound_path: Path | None) -> None:
        self.events.append((event, sound_path))


class FailingHandler(BaseHandler):
    async def handle(self, event: PeonEvent, sound_path: Path | None) -> None:
        raise RuntimeError("handler exploded")


@pytest.fixture
def empty_cesp() -> CESPManager:
    return CESPManager(packs={}, active_pack_name="none")


@pytest.mark.asyncio
class TestEventQueue:
    async def test_enqueue_and_drain(self, empty_cesp: CESPManager):
        handler = MockHandler()
        registry = HandlerRegistry([handler])
        queue = EventQueue(cesp=empty_cesp, registry=registry, debounce_ms=0)
        queue.start()

        event = PeonEvent(
            category="session.start", session_id="s1", timestamp=time.monotonic()
        )
        queue.enqueue(event)

        # Give the drain loop time to process
        await asyncio.sleep(0.1)
        await queue.stop()

        assert len(handler.events) == 1
        assert handler.events[0][0].category == "session.start"

    async def test_debounce(self, empty_cesp: CESPManager):
        handler = MockHandler()
        registry = HandlerRegistry([handler])
        queue = EventQueue(
            cesp=empty_cesp, registry=registry, debounce_ms=1000
        )
        queue.start()

        for _ in range(5):
            queue.enqueue(
                PeonEvent(
                    category="task.complete",
                    session_id="s1",
                    timestamp=time.monotonic(),
                )
            )

        await asyncio.sleep(0.2)
        await queue.stop()

        # Only the first should get through; rest debounced
        assert len(handler.events) == 1

    async def test_handler_exception_isolation(self, empty_cesp: CESPManager):
        good_handler = MockHandler()
        bad_handler = FailingHandler()
        registry = HandlerRegistry([bad_handler, good_handler])
        queue = EventQueue(cesp=empty_cesp, registry=registry, debounce_ms=0)
        queue.start()

        queue.enqueue(
            PeonEvent(
                category="task.error",
                session_id="s1",
                timestamp=time.monotonic(),
            )
        )

        await asyncio.sleep(0.1)
        await queue.stop()

        # Good handler should still receive the event despite bad handler failing
        assert len(good_handler.events) == 1

    async def test_depth(self, empty_cesp: CESPManager):
        handler = MockHandler()
        registry = HandlerRegistry([handler])
        queue = EventQueue(cesp=empty_cesp, registry=registry, debounce_ms=0)
        # Don't start drain — just check depth
        queue.enqueue(
            PeonEvent(
                category="session.start",
                session_id="s1",
                timestamp=time.monotonic(),
            )
        )
        assert queue.depth == 1
