"""PRD-22059: Teams channel preview / send wrappers -- stub client + stubbed caller, no network."""

import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP

from mspbots_agent_mcp.api_client import AgentError
from mspbots_agent_mcp.server import _DedentedDocs
from mspbots_agent_mcp.tools import teams_channel


class StubClient:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    async def post(self, path, body, retries=None):
        self.calls.append((path, body, retries))
        if self.error:
            raise self.error
        return self.result


def _tools(client, caller=("t1", "a1")):
    mcp = FastMCP("t")
    teams_channel.register(_DedentedDocs(mcp), lambda: client, lambda: caller)
    tools = mcp._tool_manager._tools
    return (
        tools["mspbotsagent_teams_channel_preview"].fn,
        tools["mspbotsagent_teams_channel_send"].fn,
    )


def run(coro):
    return asyncio.run(coro)


ARGS = dict(team_id="T", channel_id="C", body="hello")
SENT = {"status": "sent", "message_id": "m1", "log_id": "1"}


def test_send_posts_to_gate_with_run_id_and_never_retries():
    c = StubClient(SENT)
    _, send = _tools(c)
    out = run(send(**ARGS, mention_user_ids=["a@b.c"]))
    path, body, retries = c.calls[0]
    assert path == "/api/agents/a1/teams-channel/send" and retries == 0
    assert body == {
        "team_id": "T", "channel_id": "C", "body": "hello", "body_content_type": "text",
        "importance": "normal", "mention_user_ids": ["a@b.c"], "run_id": "t1",
    }
    assert json.loads(out) == SENT


def test_preview_posts_to_preview_path_with_default_retries():
    c = StubClient({"allowed": True})
    preview, _ = _tools(c)
    run(preview(**ARGS, subject="S"))
    path, body, retries = c.calls[0]
    assert path == "/api/agents/a1/teams-channel/preview" and retries is None
    assert body["subject"] == "S" and body["run_id"] == "t1"


def test_caller_identity_comes_from_the_run_not_the_arguments():
    c = StubClient(SENT)
    _, send = _tools(c, caller=("thread-9", "agent-9"))
    run(send(**ARGS))
    assert c.calls[0][0] == "/api/agents/agent-9/teams-channel/send"
    assert c.calls[0][1]["run_id"] == "thread-9"


def test_outside_an_agent_run_nothing_is_called():
    c = StubClient(SENT)
    _, send = _tools(c, caller=None)
    out = json.loads(run(send(**ARGS)))
    assert out["error"]["code"] == "not_configured" and c.calls == []


def test_missing_credentials_returns_no_token():
    _, send = _tools(None)
    assert json.loads(run(send(**ARGS)))["error"]["code"] == "not_configured"


@pytest.mark.parametrize("field", ["team_id", "channel_id", "body"])
def test_blank_required_field_is_refused_before_any_call(field):
    c = StubClient(SENT)
    _, send = _tools(c)
    out = json.loads(run(send(**{**ARGS, field: "  "})))
    assert out["error"]["code"] == "invalid_argument" and c.calls == []


@pytest.mark.parametrize(
    "status,message,code",
    [
        (403, "channel_not_allowlisted: This channel is not on the agent's list.", "channel_not_allowlisted"),
        (403, "channel_send_off: Posting is off.", "channel_send_off"),
        (429, "rate_limited: 20 posts in the last 60 minutes; try again in 90 seconds.", "rate_limited"),
    ],
)
def test_refusal_keeps_the_gate_reason_and_is_not_retryable(status, message, code):
    _, send = _tools(StubClient(error=AgentError(status, message)))
    out = json.loads(run(send(**ARGS)))["error"]
    assert out["code"] == code and out["retryable"] is False
    assert ": " not in out["message"].split(" ")[0]


def test_bare_404_maps_to_not_available():
    _, send = _tools(StubClient(error=AgentError(404, "Not found")))
    assert json.loads(run(send(**ARGS)))["error"]["code"] == "not_available"


def test_agent_not_found_stays_not_found():
    _, send = _tools(StubClient(error=AgentError(404, "Agent not found")))
    assert json.loads(run(send(**ARGS)))["error"]["code"] == "not_found"


def test_failed_post_keeps_the_tool_error_code_and_is_not_retryable():
    _, send = _tools(StubClient(error=AgentError(502, "invalid_argument: Message body is too big")))
    out = json.loads(run(send(**ARGS)))["error"]
    assert out["code"] == "invalid_argument" and out["retryable"] is False


def test_unexplained_502_is_an_upstream_error():
    _, send = _tools(StubClient(error=AgentError(502, "bad gateway")))
    assert json.loads(run(send(**ARGS)))["error"]["code"] == "upstream_error"
