"""mspbotsagent_update_agent behaviour against a fake platform client (PRD-22150)."""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from mspbots_agent_mcp.api_client import AgentError
from mspbots_agent_mcp.tools import agents


class FakeClient:
    def __init__(self, fail_main=None, skill_reply=None):
        self.calls = []
        self.fail_main = fail_main
        self.skill_reply = skill_reply or {"success": True}

    async def put(self, path, body):
        self.calls.append((path, body))
        if path.endswith("/skills"):
            return self.skill_reply
        if self.fail_main:
            raise self.fail_main
        return {"data": {"id": "a1", **body}}


async def call(client, **args):
    mcp = FastMCP("t")
    agents.register(mcp, lambda: client)
    _, structured = await mcp.call_tool("mspbotsagent_update_agent", args)
    return structured["result"]


@pytest.mark.asyncio
async def test_sends_one_patch_with_prompt_and_note_and_never_active():
    client = FakeClient()
    out = json.loads(
        await call(
            client,
            agent_id="a1",
            instructions="new sop",
            description="d",
            settings={"toolCallLimit": 30},
            version_note="n",
            active=False,
        )
    )
    assert client.calls == [
        (
            "/api/agents/a1",
            {"prompt": "new sop", "description": "d", "toolCallLimit": 30, "versionNote": "n"},
        )
    ]
    assert out["agent"]["data"]["toolCallLimit"] == 30


@pytest.mark.asyncio
async def test_rejects_forbidden_settings_before_any_write():
    client = FakeClient()
    out = json.loads(await call(client, agent_id="a1", settings={"toolCallLimit": 1, "active": False}))
    assert out["error"]["code"] == "invalid_argument"
    assert "platform" in out["error"]["message"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_requires_at_least_one_change():
    client = FakeClient()
    out = json.loads(await call(client, agent_id="a1"))
    assert out["error"]["code"] == "invalid_argument"
    assert client.calls == []


@pytest.mark.asyncio
async def test_skills_only_makes_no_agent_put_and_reports_each_skill():
    client = FakeClient(skill_reply={"success": False, "error": "unknown ref"})
    out = json.loads(
        await call(client, agent_id="a1", skills=[{"ref": "s1", "enabled": False, "type": "org"}])
    )
    assert client.calls == [("/api/agents/a1/skills", {"ref": "s1", "enabled": False, "type": "org"})]
    assert out["skills"] == [{"ref": "s1", "ok": False, "error": "unknown ref"}]


@pytest.mark.asyncio
async def test_platform_refusal_is_relayed_and_skills_are_not_attempted():
    client = FakeClient(fail_main=AgentError(400, "Agent not found"))
    out = json.loads(
        await call(
            client,
            agent_id="a1",
            description="x",
            skills=[{"ref": "s1", "enabled": True}],
        )
    )
    assert "Agent not found" in out["error"]["message"]
    assert [c[0] for c in client.calls] == ["/api/agents/a1"]
