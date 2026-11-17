"""PRD-22270: AgentClient._handle error-text extraction (TC11)."""

import httpx
import pytest

from mspbots_agent_mcp.api_client import AgentClient, AgentError


def _handle(status, body):
    c = AgentClient.__new__(AgentClient)
    return c._handle(httpx.Response(status, json=body))


def _err(status, body):
    with pytest.raises(AgentError) as e:
        _handle(status, body)
    return e.value


def test_error_string_is_used():
    assert "date has no time" in str(_err(422, {"success": False, "error": "date has no time"}))


def test_message_fallback():
    assert "boom" in str(_err(500, {"message": "boom"}))


def test_error_wins_over_message():
    assert "from error" in str(_err(422, {"error": "from error", "message": "from message"}))


def test_error_object_message():
    assert "nested" in str(_err(422, {"error": {"message": "nested"}}))


def test_neither_is_unknown():
    assert "unknown error" in str(_err(500, {"success": False}))


def test_errors_details_appended():
    assert "errors=[" in str(_err(422, {"error": "bad", "errors": ["x"]}))


def test_success_body_unchanged():
    assert _handle(200, {"ok": 1}) == {"ok": 1}
