"""Agent inventory tool tests: list_agents / get_agent / upload_agent (PRD-22084).

No network calls: a stub client stands in for AgentClient and records what each
tool sent, same style as tests/test_sops.py. These three wrap GET /api/agents,
GET /api/agents/:id and POST /api/agents -- the platform side (tenant
isolation, draft-not-running, safe permission default) is unit-tested in
mb-platform-agent itself; this file only tests what this wrapper does with
the response it gets back: project the documented fields, reject bad input
before a request goes out, and surface the NO_TOKEN path.
"""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from mspbots_agent_mcp.tools.agents import register


class _StubClient:
    def __init__(self, get_response=None, post_response=None):
        self._get_response = get_response
        self._post_response = post_response
        self.gets: list[tuple[str, dict | None]] = []
        self.posts: list[tuple[str, dict]] = []

    async def get(self, path, params=None):
        self.gets.append((path, params))
        return self._get_response

    async def post(self, path, json_body=None, *, read_timeout=None, retries=None):
        self.posts.append((path, json_body))
        return self._post_response


def _mcp(client):
    mcp = FastMCP(name="test")
    register(mcp, lambda: client)
    return mcp


async def _call(client, tool, args):
    result = await _mcp(client).call_tool(tool, args)
    return json.loads(result[0][0].text)


def _agent_row(**overrides):
    row = {
        "id": "agt_01HX0001",
        "name": "Onboarding Agent",
        "status": "ready",
        "active": False,
        "owners": [],
        # UI-only fields a real GET /api/agents response also carries -- must
        # NOT leak into list_agents' projection.
        "running": 1,
        "canEdit": True,
        "canChat": True,
        "canConfigure": True,
    }
    row.update(overrides)
    return row


# TC-AGTMCP-001 / AC1
@pytest.mark.asyncio
async def test_list_agents_projects_documented_fields_only():
    client = _StubClient(
        get_response={
            "success": True,
            "data": {
                "list": [_agent_row()],
                "total": 1,
                "running": 1,
                "page": 1,
                "pageSize": 20,
            },
        }
    )
    payload = await _call(client, "mspbotsagent_list_agents", {})

    assert payload["list"] == [
        {
            "id": "agt_01HX0001",
            "name": "Onboarding Agent",
            "status": "ready",
            "active": False,
            "owners": [],
        }
    ]
    assert "running" not in payload["list"][0]
    assert "canEdit" not in payload["list"][0]
    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["pageSize"] == 20
    # UI-only running/canEdit/canChat/canConfigure dropped at the top level too.
    assert set(payload) == {"list", "total", "page", "pageSize"}


@pytest.mark.asyncio
async def test_list_agents_clamps_page_size_to_100():
    client = _StubClient(get_response={"success": True, "data": {"list": [], "total": 0, "page": 1, "pageSize": 100}})
    await _call(client, "mspbotsagent_list_agents", {"page_size": 500})

    assert client.gets[0][1]["pageSize"] == 100


@pytest.mark.asyncio
async def test_list_agents_tolerates_a_missing_or_empty_list():
    client = _StubClient(get_response={"success": True, "data": {"total": 0, "page": 1, "pageSize": 20}})
    payload = await _call(client, "mspbotsagent_list_agents", {})
    assert payload["list"] == []


# TC-AGTMCP-003 / AC2
@pytest.mark.asyncio
async def test_get_agent_projects_full_definition():
    client = _StubClient(
        get_response={
            "success": True,
            "data": {
                "id": "agt_01HX0001",
                "name": "Onboarding Agent",
                "description": "Handles new client intake",
                "model": "azure/gpt-5.4",
                "prompt": "You are...",
                "status": "ready",
                "active": False,
                "permission": {},
                "interruptOn": {},
                "review": None,
                "approval": [],
                "steps": None,
                "toolCallLimit": None,
                "recursionLimit": None,
                "owners": [],
                "policyError": False,
                # Fields that must NOT leak: channel config, BYO-gateway, UI bookkeeping.
                "msteams": {"app_id": "secret"},
                "twilio": {"enabled": True},
                "hasApiKey": True,
                "baseUrl": "https://byo.example.com",
                "canEdit": True,
                "canChat": True,
                "canConfigure": True,
                "autonomyAudit": {"level": "full"},
                "updatedByLabel": "Someone",
                "onDisk": True,
            },
        }
    )
    payload = await _call(client, "mspbotsagent_get_agent", {"agent_id": "agt_01HX0001"})

    assert payload == {
        "id": "agt_01HX0001",
        "name": "Onboarding Agent",
        "description": "Handles new client intake",
        "model": "azure/gpt-5.4",
        "prompt": "You are...",
        "status": "ready",
        "active": False,
        "permission": {},
        "interruptOn": {},
        "review": None,
        "approval": [],
        "steps": None,
        "toolCallLimit": None,
        "recursionLimit": None,
        "owners": [],
        "policyError": False,
    }
    assert client.gets == [("/api/agents/agt_01HX0001", None)]


# AC5: reject before any request is sent.
@pytest.mark.asyncio
async def test_upload_agent_rejects_missing_name_before_calling_api():
    client = _StubClient()
    payload = await _call(client, "mspbotsagent_upload_agent", {"name": "", "model": "azure/gpt-5.4"})

    assert payload["error"]["code"] == "invalid_argument"
    assert client.posts == []


@pytest.mark.asyncio
async def test_upload_agent_rejects_missing_model_before_calling_api():
    client = _StubClient()
    payload = await _call(client, "mspbotsagent_upload_agent", {"name": "qa-agent", "model": "   "})

    assert payload["error"]["code"] == "invalid_argument"
    assert client.posts == []


# TC-AGTMCP-005 / AC3, AC4
@pytest.mark.asyncio
async def test_upload_agent_minimal_definition_succeeds_and_projects_response():
    client = _StubClient(
        post_response={
            "success": True,
            "data": {
                "id": "agt_new01",
                "name": "qa-PRD-22084-upload-1",
                "description": "",
                "model": "azure/gpt-5.4",
                "prompt": "",
                "status": "ready",
                "active": False,
                "steps": None,
                "toolCallLimit": None,
                "recursionLimit": None,
                # Not part of createAgent's own response shape, but included here to prove
                # the tool does not echo back anything beyond its own documented fields.
                "graphId": "orchestrator",
                "createdBy": "api-key",
            },
        }
    )
    payload = await _call(
        client, "mspbotsagent_upload_agent", {"name": "qa-PRD-22084-upload-1", "model": "azure/gpt-5.4"}
    )

    assert payload == {
        "id": "agt_new01",
        "name": "qa-PRD-22084-upload-1",
        "description": "",
        "model": "azure/gpt-5.4",
        "prompt": "",
        "status": "ready",
        "active": False,
        "steps": None,
        "toolCallLimit": None,
        "recursionLimit": None,
    }
    assert client.posts == [("/api/agents", {"name": "qa-PRD-22084-upload-1", "model": "azure/gpt-5.4"})]


# AC6: validated client-side exactly like mspbotsagent_upsert_agent_permissions.
@pytest.mark.asyncio
async def test_upload_agent_rejects_unknown_bare_tool_id_in_permission():
    client = _StubClient()
    payload = await _call(
        client,
        "mspbotsagent_upload_agent",
        {"name": "qa-agent", "model": "azure/gpt-5.4", "permission": {"send_email": "deny"}},
    )

    assert payload["error"]["code"] == "invalid_argument"
    assert client.posts == []


@pytest.mark.asyncio
async def test_upload_agent_normalizes_legacy_tool_keys_before_sending():
    client = _StubClient(post_response={"success": True, "data": {"id": "agt_x", "name": "qa", "model": "m"}})
    await _call(
        client,
        "mspbotsagent_upload_agent",
        {"name": "qa-agent", "model": "azure/gpt-5.4", "permission": {"download": "ask"}},
    )

    assert client.posts[0][1]["permission"] == {"artifact": "ask"}


@pytest.mark.asyncio
async def test_upload_agent_rejects_review_rule_missing_rubric_before_sending():
    client = _StubClient()
    payload = await _call(
        client,
        "mspbotsagent_upload_agent",
        {"name": "qa-agent", "model": "azure/gpt-5.4", "review": {"rules": [{"name": "no rubric here"}]}},
    )

    assert payload["error"]["code"] == "invalid_argument"
    assert client.posts == []


@pytest.mark.asyncio
async def test_upload_agent_camel_cases_the_limit_fields():
    client = _StubClient(post_response={"success": True, "data": {"id": "agt_x", "name": "qa", "model": "m"}})
    await _call(
        client,
        "mspbotsagent_upload_agent",
        {
            "name": "qa-agent",
            "model": "azure/gpt-5.4",
            "tool_call_limit": 10,
            "recursion_limit": 5,
        },
    )

    body = client.posts[0][1]
    assert body["toolCallLimit"] == 10
    assert body["recursionLimit"] == 5
    assert "tool_call_limit" not in body
    assert "recursion_limit" not in body


# The platform's fail() path: HTTP 200, {"success": false, "error": "..."} -- a duplicate
# name or similar rejection that AgentError's status-code mapping never sees.
@pytest.mark.asyncio
async def test_upload_agent_surfaces_a_200_success_false_rejection():
    client = _StubClient(post_response={"success": False, "error": 'Agent "qa-agent" already exists'})
    payload = await _call(client, "mspbotsagent_upload_agent", {"name": "qa-agent", "model": "azure/gpt-5.4"})

    assert payload["error"]["code"] == "invalid_argument"
    assert "already exists" in payload["error"]["message"]


# TC5-equivalent / AC4 (no-credentials path, same idiom as test_usage.py)
@pytest.mark.asyncio
async def test_missing_credentials_returns_not_configured_envelope_for_all_three_tools():
    mcp = FastMCP(name="test")
    register(mcp, lambda: None)
    for name, args in (
        ("mspbotsagent_list_agents", {}),
        ("mspbotsagent_get_agent", {"agent_id": "agt_1"}),
        ("mspbotsagent_upload_agent", {"name": "qa-agent", "model": "m"}),
    ):
        result = await mcp.call_tool(name, args)
        payload = json.loads(result[0][0].text)
        assert payload["error"]["code"] == "not_configured", name
