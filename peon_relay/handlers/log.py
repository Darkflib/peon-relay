from __future__ import annotations

from pathlib import Path

import structlog

from peon_relay.handlers import BaseHandler
from peon_relay.queue import PeonEvent

logger = structlog.get_logger()


class LogHandler(BaseHandler):
    def __init__(self, active_pack: str) -> None:
        self._active_pack = active_pack

    async def handle(self, event: PeonEvent, sound_path: Path | None) -> None:
        if sound_path is not None:
            logger.info(
                "peon.fired",
                category=event.category,
                session_id=event.session_id,
                pack=self._active_pack,
                sound=sound_path.name,
            )
        else:
            logger.info(
                "peon.no_sound",
                category=event.category,
                reason="category_not_in_pack",
            )
