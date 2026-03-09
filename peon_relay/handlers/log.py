from __future__ import annotations

from pathlib import Path

import structlog

from peon_relay.handlers import BaseHandler
from peon_relay.queue import PeonEvent

logger = structlog.get_logger()


class LogHandler(BaseHandler):
    def __init__(self, default_pack: str) -> None:
        self._default_pack = default_pack

    async def handle(self, event: PeonEvent, sound_path: Path | None) -> None:
        pack = event.pack or self._default_pack
        if sound_path is not None:
            logger.info(
                "peon.fired",
                category=event.category,
                session_id=event.session_id,
                pack=pack,
                sound=sound_path.name,
            )
        else:
            logger.info(
                "peon.no_sound",
                category=event.category,
                pack=pack,
                reason="category_not_in_pack",
            )
