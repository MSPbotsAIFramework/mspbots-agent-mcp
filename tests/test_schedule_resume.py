"""PRD-22270: schedule_resume wrapper -- stub client + stubbed caller context, no network."""

import asyncio
import json

import pytest

from mspbots_agent_mcp.api_client import AgentError
from mspbots_agent_mcp.config import Settings
from mspbots_agent_mcp.server import (
    GatewayTokenMiddleware,
    _DedentedDocs,
    get_caller_from_context,
)
from mcp.server.fastmcp import FastMCP
from mspbots_agent_mcp.tools import schedule


class StubClient:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    async def post(self, path, body, retries=None):
        self.calls.append((path, body, retries))
        if self.error:
            raise self.error
        return self.result


def _tool(client, caller=("t1", "a1")):
    mcp = FastMCP("t")
    schedule.register(_DedentedDocs(mcp), lambda: client, lambda: caller)
    return mcp._tool_manager._tools["schedule_resume"].fn


def run(coro):
    return asyncio.run(coro)


ANSWER = {"success": True, "data": {"wait_id": "w1", "status": "pending", "resume_at": "x"}}


def test_tc2_posts_to_run_thread_and_returns_answer_unchanged():
    c = StubClient(ANSWER)
    out = run(_tool(c)(key="k", reason="r", resume_in="3m"))
    assert c.calls == [
        ("/api/agents/a1/threads/t1/schedule-resume", {"resume_in": "3m", "key": "k", "reason": "r"}, 0)
    ]
    assert json.loads(out) == ANSWER


def test_tc2_optional_fields_are_forwarded():
    c = StubClient(ANSWER)
    run(
        _tool(c)(
            key="k", reason="r", resume_at="2026-12-01T09:00:00+00:00", timezone="UTC",
            wake_on=["note"], cancel_on=["closed"],
            ticket=schedule.TicketRef(integration="halopsa", id="1"),
        )
    )
    body = c.calls[0][1]
    assert body["wake_on"] == ["note"] and body["cancel_on"] == ["closed"]
    assert body["ticket"] == {"integration": "halopsa", "id": "1"} and body["timezone"] == "UTC"


def test_tc3_same_key_twice_same_path_and_key():
    c = StubClient(ANSWER)
    tool = _tool(c)
    run(tool(key="k", reason="r", resume_in="3m"))
    out = run(tool(key="k", reason="r", resume_in="10m"))
    assert c.calls[0][0] == c.calls[1][0] and c.calls[1][1]["key"] == "k"
    assert json.loads(out) == ANSWER


@pytest.mark.parametrize(
    "status,msg,code,retryable",
    [
        (422, "date has no time", "invalid_argument", False),
        (422, "beyond 90 days", "invalid_argument", False),
        (429, "cap reached", "limit_reached", False),
        (403, "no access", "unauthorized", False),
    ],
)
def test_tc4_endpoint_refusals_are_readable(status, msg, code, retryable):
    out = json.loads(run(_tool(StubClient(error=AgentError(status, msg)))(key="k", reason="r", resume_in="3m")))
    assert out["error"] == {"code": code, "message": msg, "retryable": retryable}


def test_tc5_flag_off_404_is_not_available_but_agent_404_stays():
    off = json.loads(run(_tool(StubClient(error=AgentError(404, "Not found")))(key="k", reason="r", resume_in="3m")))
    assert off["error"]["code"] == "not_available" and "not available" in off["error"]["message"]
    other = json.loads(run(_tool(StubClient(error=AgentError(404, "Agent not found")))(key="k", reason="r", resume_in="3m")))
    assert other["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "kwargs,caller",
    [
        (dict(key="k", reason="r"), ("t1", "a1")),
        (dict(key="k", reason="r", resume_at="x", resume_in="3m"), ("t1", "a1")),
        (dict(key=" ", reason="r", resume_in="3m"), ("t1", "a1")),
        (dict(key="k", reason="", resume_in="3m"), ("t1", "a1")),
        (dict(key="k", reason="r", resume_in="3m"), None),
    ],
)
def test_tc6_bad_input_never_calls_platform(kwargs, caller):
    c = StubClient(ANSWER)
    out = json.loads(run(_tool(c, caller)(**kwargs)))
    assert "error" in out and c.calls == []


def test_no_credentials_returns_no_token():
    mcp = FastMCP("t")
    schedule.register(_DedentedDocs(mcp), lambda: None, lambda: ("t1", "a1"))
    fn = mcp._tool_manager._tools["schedule_resume"].fn
    assert "not_configured" in run(fn(key="k", reason="r", resume_in="3m"))


def _call_middleware(extra_headers):
    seen = {}

    async def fake_app(scope, receive, send):
        seen["caller"] = get_caller_from_context()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = GatewayTokenMiddleware(fake_app, Settings())
    sent = []

    async def go():
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [
                (b"x-api-key", b"tok"),
                (b"x-msp-tenant-id", b"ten"),
                (b"x-msp-host", b"https://agent.mspbots.ai"),
                *extra_headers,
            ],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(m):
            sent.append(m)

        await mw(scope, receive, send)

    asyncio.run(go())
    return seen, sent


def test_tc7_caller_headers_reach_context_and_absence_is_not_401():
    seen, sent = _call_middleware([(b"x-channel-thread-id", b"t9"), (b"x-channel-agent-id", b"a9")])
    assert seen["caller"] == ("t9", "a9")
    assert get_caller_from_context() is None  # reset after the request
    seen, sent = _call_middleware([])
    assert seen["caller"] is None and sent[0]["status"] == 200


class _RealHandleClient:
    """Runs the real AgentClient response handling on a canned HTTP response (PRD-22270 TC10/TC11)."""

    def __init__(self, status, body):
        import httpx
        from mspbots_agent_mcp.api_client import AgentClient

        self._c, self._r = AgentClient.__new__(AgentClient), httpx.Response(status, json=body)

    async def post(self, path, body, retries=None):
        return self._c._handle(self._r)


def _real(status, body):
    return json.loads(run(_tool(_RealHandleClient(status, body))(key="k", reason="r", resume_in="3m")))


def test_tc10_real_flag_off_body_is_not_available_agent_404_stays():
    off = _real(404, {"success": False, "error": "Not found"})
    assert off["error"]["code"] == "not_available"
    other = _real(404, {"success": False, "error": "Agent not found"})
    assert other["error"]["code"] == "not_found" and "Agent not found" in other["error"]["message"]


@pytest.mark.parametrize(
    "status,text,code",
    [
        (422, "date has no time", "invalid_argument"),
        (422, "resume time is beyond the 90 day horizon", "invalid_argument"),
        (429, "cap reached", "limit_reached"),
        (403, "no access", "unauthorized"),
    ],
)
def test_tc11_real_error_bodies_keep_text(status, text, code):
    out = _real(status, {"success": False, "error": text})["error"]
    assert out["code"] == code and text in out["message"] and "unknown error" not in out["message"]


def test_tc11_message_field_still_works():
    assert "legacy text" in _real(422, {"message": "legacy text"})["error"]["message"]
