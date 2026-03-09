from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from peon_relay.handlers import BaseHandler
from peon_relay.queue import PeonEvent

logger = structlog.get_logger()

CATEGORY_MESSAGES: dict[str, tuple[str, str]] = {
    "session.start": ("Session Started", "A new Claude Code session has begun."),
    "session.end": ("Session Ended", "The Claude Code session has finished."),
    "task.acknowledge": ("Task Acknowledged", "Claude has picked up your task."),
    "task.complete": ("Task Complete", "Claude has finished the task."),
    "task.error": ("Task Error", "An error occurred during the task."),
    "task.progress": ("Task Progress", "Task is still in progress."),
    "input.required": ("Input Required", "Claude is waiting for your input."),
    "resource.limit": ("Resource Limit", "A resource limit has been reached."),
    "user.spam": ("Rate Limited", "Too many requests detected."),
}


class NotificationSink:
    """Base class for notification delivery backends."""

    async def send(self, title: str, body: str, category: str) -> None:
        raise NotImplementedError


class DesktopSink(NotificationSink):
    """OS-native desktop notifications via notify-py."""

    async def send(self, title: str, body: str, category: str) -> None:
        try:
            await asyncio.to_thread(self._send_sync, title, body)
        except Exception:
            logger.exception(
                "desktop_notification_error", category=category
            )

    @staticmethod
    def _send_sync(title: str, body: str) -> None:
        from notifypy import Notify

        n = Notify()
        n.title = title
        n.message = body
        n.send(block=True)


class NotificationHandler(BaseHandler):
    def __init__(
        self,
        sinks: list[NotificationSink],
        disabled_categories: list[str],
    ) -> None:
        self._sinks = sinks
        self._disabled_categories = set(disabled_categories)

    async def handle(self, event: PeonEvent, sound_path: Path | None) -> None:
        if not self._sinks:
            return
        if event.category in self._disabled_categories:
            return

        title, body = CATEGORY_MESSAGES.get(
            event.category, ("Peon Event", event.category)
        )

        await asyncio.gather(
            *(sink.send(title, body, event.category) for sink in self._sinks)
        )
