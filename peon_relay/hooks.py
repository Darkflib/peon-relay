from __future__ import annotations

import re
import time
from typing import Any

import structlog

from peon_relay.queue import EventQueue, PeonEvent

logger = structlog.get_logger()

# Session tracking: session_id -> last_seen monotonic timestamp
_sessions: dict[str, float] = {}
_SESSION_TTL = 4 * 60 * 60  # 4 hours


def _prune_sessions() -> None:
    now = time.monotonic()
    expired = [
        sid for sid, ts in _sessions.items() if (now - ts) > _SESSION_TTL
    ]
    for sid in expired:
        del _sessions[sid]


def _detect_error(payload: dict[str, Any]) -> bool:
    tool_response = payload.get("tool_response")
    if isinstance(tool_response, dict):
        if tool_response.get("is_error") is True:
            return True
    if isinstance(tool_response, str):
        if re.search(r"error|failed", tool_response, re.IGNORECASE):
            return True
    return False


def map_hook_to_category(payload: dict[str, Any]) -> str | None:
    hook_event_name = payload.get("hook_event_name")
    session_id = payload.get("session_id", "unknown")

    if not hook_event_name:
        logger.warning("missing_hook_event_name", payload_keys=list(payload.keys()))
        return None

    _prune_sessions()

    match hook_event_name:
        case "PreToolUse":
            if session_id not in _sessions:
                _sessions[session_id] = time.monotonic()
                return "session.start"
            _sessions[session_id] = time.monotonic()
            return None  # skip

        case "PostToolUse":
            _sessions[session_id] = time.monotonic()
            if _detect_error(payload):
                return "task.error"
            return "task.complete"

        case "Notification":
            message = payload.get("message", "")
            if not isinstance(message, str):
                message = str(message)
            lower = message.lower()
            if "input" in lower or "approval" in lower:
                return "input.required"
            if "limit" in lower or "quota" in lower:
                return "resource.limit"
            return None  # skip other notifications

        case "Stop":
            return "session.end"

        case _:
            logger.warning(
                "unrecognised_hook_event",
                hook_event_name=hook_event_name,
            )
            return None


def process_hook(payload: dict[str, Any], queue: EventQueue) -> dict[str, str]:
    logger.debug("hook_payload", payload=payload)

    category = map_hook_to_category(payload)

    if category is None:
        hook_event_name = payload.get("hook_event_name")
        if not hook_event_name or hook_event_name not in (
            "PreToolUse", "PostToolUse", "Notification", "Stop"
        ):
            return {"status": "ignored"}
        return {"status": "skipped", "category": None}

    session_id = payload.get("session_id", "unknown")
    event = PeonEvent(
        category=category,
        session_id=session_id,
        timestamp=time.monotonic(),
    )
    queue.enqueue(event)
    return {"status": "queued", "category": category}
