"""SOP library tool tests.

No network calls: a stub client stands in for AgentClient and records what
each tool sent, so the assertions are about this wrapper's own decisions.

Why these cases carry the weight:

- The run endpoint answers HTTP 200 with `{"success": false}` for a failed
  run, so a failure never reaches AgentError and would otherwise be reported
  to the model as a successful turn with an empty reply.
- A run is NOT idempotent. Retrying a lost response starts a SECOND agent
  run rather than recovering the first, so the chat call must go out with
  retries disabled and a read timeout long enough for a real turn.
- A paused (approval-gated) run legitimately produces no assistant message.
  Reporting that as an ordinary empty answer hides the reason.
"""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from mspbots_agent_mcp.tools.sops import _CHAT_READ_TIMEOUT, register


class _StubClient:
    def __init__(self, sop=None, run=None):
        self._sop = sop if sop is not None else {"id": 7, "name": "Onboarding", "agent_id": "agt_1"}
        self._run = run
        self.gets: list[tuple[str, dict | None]] = []
        self.posts: list[dict] = []

    async def get(self, path, params=None):
        self.gets.append((path, params))
        if path.startswith("/api/sops/"):
            return self._sop
        return self._sop

    async def post(self, path, json_body=None, *, read_timeout=None, retries=None):
        self.posts.append(
            {"path": path, "body": json_body, "read_timeout": read_timeout, "retries": retries}
        )
        return self._run


def _mcp(client):
    mcp = FastMCP(name="test")
    register(mcp, lambda: client)
    return mcp


async def _call(client, tool, args):
    result = await _mcp(client).call_tool(tool, args)
    return json.loads(result[0][0].text)


def _run_ok(messages, thread_id="th_1", extra=None):
    output = {"messages": messages}
    if extra:
        output.update(extra)
    return {"success": True, "data": {"threadId": thread_id, "result": output}}


_CHAT_HIDDEN_REASON = (
    "mspbotsagent_chat_with_sop is temporarily hidden from external clients "
    "(see the commented-out @mcp.tool decorator in tools/sops.py); un-skip "
    "these together with restoring that decorator."
)


@pytest.mark.skip(reason=_CHAT_HIDDEN_REASON)
@pytest.mark.asyncio
async def test_chat_returns_the_last_visible_assistant_message():
    client = _StubClient(
        run=_run_ok(
            [
                {"type": "human", "content": "hi"},
                {"type": "ai", "content": "first pass"},
                {"type": "ai", "content": "graded", "name": "rubric_grader"},
                {"type": "ai", "content": [{"type": "text", "text": "Done: 3 tickets closed."}]},
                {
                    "type": "ai",
                    "content": "internal",
                    "additional_kwargs": {"lc_source": "compact_summary"},
                },
            ]
        )
    )
    payload = await _call(client, "mspbotsagent_chat_with_sop", {"sop_id": 7, "message": "go"})

    assert payload["reply"] == "Done: 3 tickets closed."
    assert payload["status"] == "completed"
    assert payload["threadId"] == "th_1"
    assert payload["agentId"] == "agt_1"
    assert payload["sopName"] == "Onboarding"


@pytest.mark.skip(reason=_CHAT_HIDDEN_REASON)
@pytest.mark.asyncio
async def test_chat_disables_retries_and_waits_longer_than_the_default():
    client = _StubClient(run=_run_ok([{"type": "ai", "content": "ok"}]))
    await _call(client, "mspbotsagent_chat_with_sop", {"sop_id": 7, "message": "go"})

    sent = client.posts[0]
    assert sent["path"] == "/api/agents/agt_1/run/wait"
    assert sent["retries"] == 0, "a retried run starts a second agent run"
    assert sent["read_timeout"] == _CHAT_READ_TIMEOUT > 30


@pytest.mark.skip(reason=_CHAT_HIDDEN_REASON)
@pytest.mark.asyncio
async def test_chat_sends_only_the_thread_fields_it_was_given():
    client = _StubClient(run=_run_ok([{"type": "ai", "content": "ok"}]))
    await _call(client, "mspbotsagent_chat_with_sop", {"sop_id": 7, "message": "go"})
    assert client.posts[0]["body"] == {"message": "go"}

    client = _StubClient(run=_run_ok([{"type": "ai", "content": "ok"}]))
    await _call(
        client,
        "mspbotsagent_chat_with_sop",
        {"sop_id": 7, "message": "go", "thread_id": "th_9", "new_thread": True},
    )
    assert client.posts[0]["body"] == {"message": "go", "threadId": "th_9", "newThread": True}


@pytest.mark.skip(reason=_CHAT_HIDDEN_REASON)
@pytest.mark.asyncio
async def test_chat_reports_a_failed_run_as_an_error_not_an_empty_reply():
    client = _StubClient(
        run={"success": False, "error": "Agent run failed", "details": "502: upstream down"}
    )
    payload = await _call(client, "mspbotsagent_chat_with_sop", {"sop_id": 7, "message": "go"})

    assert "error" in payload
    assert "upstream down" in payload["error"]["message"]


@pytest.mark.skip(reason=_CHAT_HIDDEN_REASON)
@pytest.mark.asyncio
async def test_chat_reports_an_aegra_error_envelope_as_an_error():
    client = _StubClient(
        run={
            "success": True,
            "data": {"threadId": "th_1", "result": {"__error__": {"message": "tool crashed"}}},
        }
    )
    payload = await _call(client, "mspbotsagent_chat_with_sop", {"sop_id": 7, "message": "go"})

    assert payload["error"]["message"] == "tool crashed"


@pytest.mark.skip(reason=_CHAT_HIDDEN_REASON)
@pytest.mark.asyncio
async def test_chat_explains_a_run_paused_for_approval():
    client = _StubClient(
        run=_run_ok(
            [{"type": "ai", "content": "About to email the client."}],
            extra={
                "__interrupt__": [
                    {
                        "value": {
                            "action_requests": [
                                {
                                    "name": "email.send",
                                    "description": "Send the summary",
                                    "args": {"to": "ops@example.com"},
                                }
                            ]
                        }
                    }
                ]
            },
        )
    )
    payload = await _call(client, "mspbotsagent_chat_with_sop", {"sop_id": 7, "message": "go"})

    assert payload["status"] == "interrupted"
    assert payload["pausedActions"] == [
        {"action": "email.send", "reason": "Send the summary", "args": {"to": "ops@example.com"}}
    ]
    assert "approval" in payload["note"]


@pytest.mark.skip(reason=_CHAT_HIDDEN_REASON)
@pytest.mark.asyncio
async def test_chat_refuses_a_sop_with_no_agent_before_running_anything():
    client = _StubClient(sop={"id": 7, "name": "Draft", "agent_id": None})
    payload = await _call(client, "mspbotsagent_chat_with_sop", {"sop_id": 7, "message": "go"})

    assert payload["error"]["code"] == "invalid_argument"
    assert client.posts == [], "must not start a run"


@pytest.mark.skip(reason=_CHAT_HIDDEN_REASON)
@pytest.mark.asyncio
async def test_chat_marks_a_reply_less_run_rather_than_looking_successful():
    client = _StubClient(run=_run_ok([{"type": "human", "content": "go"}]))
    payload = await _call(client, "mspbotsagent_chat_with_sop", {"sop_id": 7, "message": "go"})

    assert payload["status"] == "no_reply"
    assert payload["reply"] == ""
    assert payload["note"]


@pytest.mark.asyncio
async def test_list_passes_rows_through_and_clamps_page_size():
    class _ListClient(_StubClient):
        async def get(self, path, params=None):
            self.gets.append((path, params))
            return self.response

    response = {
        "total": 2,
        "list": [
            {
                "id": 7,
                "name": "Onboarding",
                "description": "New client setup",
                "status": "published",
                "source": "manual",
                "tags": ["ops"],
                "agent_id": "agt_1",
                "agent_live": True,
                "updated_at": "2026-09-01T00:00:00.000Z",
                "owning_seat_id": 42,
                "body_generated": True,
            }
        ],
    }

    client = _ListClient()
    client.response = response
    payload = await _call(
        client, "mspbotsagent_list_sops", {"page_size": 5000, "search": "onb", "status": "published"}
    )

    assert client.gets[0] == (
        "/api/sops",
        {"search": "onb", "status": "published", "page": 1, "pageSize": 100},
    )
    # Rows go out exactly as the backend sent them: no projection, no renaming,
    # so fields like owning_seat_id are not dropped on the way through.
    assert payload == response


@pytest.mark.asyncio
async def test_no_credentials_short_circuits_list_sops():
    # Originally covered both tools in this module; mspbotsagent_chat_with_sop
    # is temporarily hidden (see tools/sops.py), so its case is skipped rather
    # than deleted — restore it together with un-hiding that tool.
    mcp = FastMCP(name="test")
    register(mcp, lambda: None)

    for tool, args in (
        ("mspbotsagent_list_sops", {}),
        # ("mspbotsagent_chat_with_sop", {"sop_id": 7, "message": "go"}),
    ):
        result = await mcp.call_tool(tool, args)
        assert json.loads(result[0][0].text)["error"]["code"] == "not_configured"


@pytest.mark.asyncio
async def test_search_param_warns_that_a_miss_is_not_an_absence():
    # `search` is a name-only ILIKE substring on the backend (service/sop/db.ts:88):
    # no description, no tags, no body, no fuzzy or cross-language matching. The
    # failure that costs a real answer is a model taking the user's own wording
    # ("the refund one"), searching a SOP actually named "Refund Handling",
    # getting zero rows, and reporting that no such SOP exists. The param text is
    # the only place that trap is visible at call time, so guard it here rather
    # than leaving it to survive on good intentions.
    from mspbots_agent_mcp.config import Settings
    from mspbots_agent_mcp.server import create_mcp_server

    tools = {t.name: t for t in await create_mcp_server(Settings()).list_tools()}

    search = tools["mspbotsagent_list_sops"].inputSchema["properties"]["search"]["description"]
    assert "not evidence" in search.lower(), "must say an empty result is not an absence"
    assert "description" in search, "must say the description is not matched"
    assert "leave this out" in search, "must give the list-and-pick alternative"

    # The reverse lookup: agent_id -> sop_id exists (every list row carries agent_id),
    # but only this text tells a model that, so it must not quietly disappear.
    # mspbotsagent_chat_with_sop is temporarily hidden (see tools/sops.py) and so
    # is absent from `tools` here — restore this assertion together with it.
    # sop_id = tools["mspbotsagent_chat_with_sop"].inputSchema["properties"]["sop_id"]["description"]
    # assert "agent_id" in sop_id
