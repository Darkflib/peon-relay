from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import structlog

from peon_relay.handlers import BaseHandler
from peon_relay.queue import PeonEvent

logger = structlog.get_logger()


TOOL_DETECTION_ORDER = {
    "darwin": ["afplay"],
    "linux": ["pw-play", "paplay", "ffplay", "mpv", "play", "aplay"],
    "win32": ["powershell"],
}


def _build_command(tool: str, path: Path, volume: float) -> list[str]:
    s = str(path)
    match tool:
        case "afplay":
            return ["afplay", "-v", str(volume), s]
        case "paplay":
            return ["paplay", f"--volume={int(volume * 65536)}", s]
        case "pw-play":
            return ["pw-play", f"--volume={volume}", s]
        case "ffplay":
            return [
                "ffplay", "-nodisp", "-autoexit",
                "-volume", str(int(volume * 100)), s,
            ]
        case "mpv":
            return ["mpv", "--no-video", f"--volume={int(volume * 100)}", s]
        case "play":
            return ["play", "-v", str(volume), s]
        case "aplay":
            return ["aplay", s]
        case "powershell":
            script = (
                f'$p = New-Object System.Windows.Media.MediaPlayer;'
                f'$p.Open("{s}");'
                f'$p.Volume = {volume};'
                f'$p.Play();'
                f'Start-Sleep -Seconds 10'
            )
            return ["powershell", "-Command", script]
        case _:
            return [tool, s]


def detect_audio_tool() -> str | None:
    platform = sys.platform
    candidates = TOOL_DETECTION_ORDER.get(platform, [])

    for tool in candidates:
        if shutil.which(tool):
            logger.info("audio_tool_detected", tool=tool, platform=platform)
            return tool

    logger.warning("no_audio_tool_found", platform=platform)
    return None


class AudioHandler(BaseHandler):
    def __init__(
        self,
        tool: str | None,
        volume: float,
        mute: bool,
        disabled_categories: list[str],
    ) -> None:
        self._tool = tool
        self._volume = volume
        self._mute = mute
        self._disabled_categories = set(disabled_categories)

    async def handle(self, event: PeonEvent, sound_path: Path | None) -> None:
        if sound_path is None:
            return
        if self._mute:
            return
        if self._tool is None:
            return
        if event.category in self._disabled_categories:
            return

        cmd = _build_command(self._tool, sound_path, self._volume)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            returncode = await proc.wait()
            if returncode != 0:
                logger.warning(
                    "audio_play_failed",
                    category=event.category,
                    path=str(sound_path),
                    returncode=returncode,
                )
        except Exception:
            logger.exception(
                "audio_play_error",
                category=event.category,
                path=str(sound_path),
            )
