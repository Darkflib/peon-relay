from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from peon_relay.cesp import CESPManager
from peon_relay.handlers import HandlerRegistry
from peon_relay.hooks import _sessions, map_hook_to_category
from peon_relay.main import app
from peon_relay.queue import EventQueue


@pytest.fixture(autouse=True)
def clear_sessions():
    _sessions.clear()
    yield
    _sessions.clear()


@pytest.fixture(autouse=True)
def _inject_queue():
    """Inject a minimal queue into main module so endpoints work without lifespan."""
    import peon_relay.main as main_mod

    cesp = CESPManager(packs={}, active_pack_name="test")
    registry = HandlerRegistry([])
    queue = EventQueue(cesp=cesp, registry=registry, debounce_ms=500)
    old_queue, old_cesp, old_config = main_mod._queue, main_mod._cesp, main_mod._config
    main_mod._queue = queue
    main_mod._cesp = cesp
    yield
    main_mod._queue, main_mod._cesp, main_mod._config = old_queue, old_cesp, old_config


class TestCategoryMapping:
    def test_pre_tool_use_new_session(self):
        payload = {"hook_event_name": "PreToolUse", "session_id": "s1"}
        assert map_hook_to_category(payload) == "session.start"

    def test_pre_tool_use_existing_session(self):
        payload = {"hook_event_name": "PreToolUse", "session_id": "s1"}
        map_hook_to_category(payload)  # first time -> session.start
        assert map_hook_to_category(payload) is None  # second time -> skip

    def test_post_tool_use_success(self):
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "tool_response": "File written successfully",
        }
        assert map_hook_to_category(payload) == "task.complete"

    def test_post_tool_use_error_is_error_field(self):
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "tool_response": {"is_error": True, "message": "something broke"},
        }
        assert map_hook_to_category(payload) == "task.error"

    def test_post_tool_use_error_string_match(self):
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "tool_response": "Command failed with exit code 1",
        }
        assert map_hook_to_category(payload) == "task.error"

    def test_notification_input_required(self):
        payload = {
            "hook_event_name": "Notification",
            "session_id": "s1",
            "message": "Waiting for user input",
        }
        assert map_hook_to_category(payload) == "input.required"

    def test_notification_approval(self):
        payload = {
            "hook_event_name": "Notification",
            "session_id": "s1",
            "message": "Needs approval to proceed",
        }
        assert map_hook_to_category(payload) == "input.required"

    def test_notification_limit(self):
        payload = {
            "hook_event_name": "Notification",
            "session_id": "s1",
            "message": "Rate limit exceeded",
        }
        assert map_hook_to_category(payload) == "resource.limit"

    def test_notification_quota(self):
        payload = {
            "hook_event_name": "Notification",
            "session_id": "s1",
            "message": "Token quota reached",
        }
        assert map_hook_to_category(payload) == "resource.limit"

    def test_notification_other_skipped(self):
        payload = {
            "hook_event_name": "Notification",
            "session_id": "s1",
            "message": "Something else happened",
        }
        assert map_hook_to_category(payload) is None

    def test_stop(self):
        payload = {"hook_event_name": "Stop", "session_id": "s1"}
        assert map_hook_to_category(payload) == "session.end"

    def test_missing_hook_event_name(self):
        payload = {"session_id": "s1"}
        assert map_hook_to_category(payload) is None

    def test_unknown_hook_event_name(self):
        payload = {"hook_event_name": "SomethingNew", "session_id": "s1"}
        assert map_hook_to_category(payload) is None


@pytest.mark.asyncio
class TestHookEndpoint:
    async def test_hook_queues_event(self):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/hook",
                json={"hook_event_name": "Stop", "session_id": "s1"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "queued"
            assert data["category"] == "session.end"

    async def test_hook_ignored_for_unknown(self):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/hook",
                json={"hook_event_name": "Unknown", "session_id": "s1"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "ignored"

    async def test_health_endpoint(self):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "queue_depth" in data
