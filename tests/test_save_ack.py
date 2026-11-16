"""PRD-22238: the three save tools return an acknowledgement, not the whole agent."""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from mspbots_agent_mcp.api_client import AgentError
from mspbots_agent_mcp.tools import agents

PROMPT = "SYSTEM PROMPT " * 1500  # ~21 KB
BIG = {
    "success": True,
    "data": {
        "id": "a1",
        "prompt": PROMPT,
        "updatedAt": "T",
        "permissionEffective": {"x": 1},
        "steps": [1, 2],
    },
}
ACK = {"success": True, "id": "a1", "updatedAt": "T"}
RULES = [{"rubric": "checks the math", "name": "math"}]
CALLS = {
    "mspbotsagent_upsert_agent_approval": {"agent_id": "a1", "rules": [{"name": "r"}]},
    "mspbotsagent_upsert_agent_permissions": {"agent_id": "a1", "permission": {"execute": "ask"}},
    "mspbotsagent_upsert_agent_evaluation": {"agent_id": "a1", "rules": RULES, "max_iterations": 2},
}


class _Stub:
    def __init__(self, answer=BIG, exc=None):
        self.answer, self.exc, self.puts = answer, exc, []

    async def get(self, path):
        return {"data": {}}

    async def put(self, path, json_body):
        self.puts.append((path, json_body))
        if self.exc:
            raise self.exc
        return self.answer


async def _call(name, args, stub):
    mcp = FastMCP(name="test")
    agents.register(mcp, lambda: stub)
    return (await mcp.call_tool(name, args))[0][0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("name", CALLS)
async def test_returns_ack_only(name):
    text = await _call(name, CALLS[name], _Stub())
    assert json.loads(text) == ACK
    assert "SYSTEM PROMPT" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("name", CALLS)
async def test_ack_under_1kb(name):
    assert len((await _call(name, CALLS[name], _Stub())).encode()) < 1024


@pytest.mark.asyncio
async def test_put_bodies_unchanged():
    bodies = {}
    for name, args in CALLS.items():
        stub = _Stub()
        await _call(name, args, stub)
        bodies[name] = stub.puts[0]
    assert bodies["mspbotsagent_upsert_agent_approval"] == ("/api/agents/a1", {"approval": {"rules": [{"name": "r"}]}})
    assert bodies["mspbotsagent_upsert_agent_permissions"] == ("/api/agents/a1", {"permission": {"execute": "ask"}})
    assert bodies["mspbotsagent_upsert_agent_evaluation"] == (
        "/api/agents/a1",
        {"review": {"rules": RULES, "max_iterations": 2}},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("name", CALLS)
async def test_errors_unchanged(name):
    err = AgentError(400, "boom")
    assert await _call(name, CALLS[name], _Stub(exc=err)) == err.to_envelope()
    refusal = {"success": False, "error": "Agent not found"}
    assert json.loads(await _call(name, CALLS[name], _Stub(answer=refusal))) == refusal


@pytest.mark.asyncio
async def test_missing_updated_at_and_id_fallback():
    stub = _Stub(answer={"success": True, "data": {}})
    text = await _call("mspbotsagent_upsert_agent_approval", CALLS["mspbotsagent_upsert_agent_approval"], stub)
    assert json.loads(text) == {"success": True, "id": "a1", "updatedAt": None}


@pytest.mark.asyncio
async def test_rubric_error_unchanged():
    text = await _call(
        "mspbotsagent_upsert_agent_evaluation", {"agent_id": "a1", "rules": [{"name": "x"}]}, _Stub()
    )
    assert "invalid_argument" in text and "rules[0]" in text
