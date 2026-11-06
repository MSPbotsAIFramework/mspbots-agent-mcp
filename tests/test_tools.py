"""tools/list snapshot + error-envelope mapping tests.

No network calls: tool enumeration goes through FastMCP's in-process
list_tools(), and the error-code mapping is tested directly against
AgentError, independent of any real HTTP request.
"""

import pytest
from mcp.server.fastmcp import FastMCP

from mspbots_agent_mcp.api_client import AgentError
from mspbots_agent_mcp.config import Settings
from mspbots_agent_mcp.server import create_mcp_server
from mspbots_agent_mcp.tools.agents import _invalid_bare_tool_ids, _normalize_legacy_tool_keys

# name -> (required params, expected annotation hint set to True)
EXPECTED_TOOLS = {
    # connectors
    "mspbotsagent_get_connectors": (set(), {"readOnlyHint"}),
    # triggers
    "mspbotsagent_list_triggers": ({"agent_id"}, {"readOnlyHint"}),
    "mspbotsagent_upsert_trigger": (set(), set()),
    "mspbotsagent_delete_trigger": ({"task_id"}, {"destructiveHint"}),
    "mspbotsagent_get_trigger_catalog": (set(), {"readOnlyHint"}),
    "mspbotsagent_run_trigger": ({"task_id"}, set()),
    # agent policy: permissions / evaluation / approval
    "mspbotsagent_get_agent_permissions": ({"agent_id"}, {"readOnlyHint"}),
    "mspbotsagent_upsert_agent_permissions": ({"agent_id"}, {"idempotentHint"}),
    "mspbotsagent_get_agent_evaluation": ({"agent_id"}, {"readOnlyHint"}),
    "mspbotsagent_upsert_agent_evaluation": ({"agent_id", "rules"}, {"idempotentHint"}),
    "mspbotsagent_get_agent_approval": ({"agent_id"}, {"readOnlyHint"}),
    "mspbotsagent_upsert_agent_approval": ({"agent_id", "rules"}, {"idempotentHint"}),
    # SOP author
    "mspbotsagent_get_sop_name": ({"agent_id"}, {"readOnlyHint"}),
    "mspbotsagent_set_sop_name": ({"agent_id", "value"}, {"idempotentHint"}),
    "mspbotsagent_get_sop_source": ({"agent_id"}, {"readOnlyHint"}),
    "mspbotsagent_set_sop_source": ({"agent_id", "value"}, {"idempotentHint"}),
    "mspbotsagent_get_sop_purpose": ({"agent_id"}, {"readOnlyHint"}),
    "mspbotsagent_set_sop_purpose": ({"agent_id", "value"}, {"idempotentHint"}),
    "mspbotsagent_get_sop_data_sources": ({"agent_id"}, {"readOnlyHint"}),
    "mspbotsagent_set_sop_data_sources": ({"agent_id", "value"}, {"idempotentHint"}),
    "mspbotsagent_get_sop_procedure": ({"agent_id"}, {"readOnlyHint"}),
    "mspbotsagent_set_sop_procedure": ({"agent_id", "value"}, {"idempotentHint"}),
    "mspbotsagent_get_sop_section_visibility": ({"agent_id"}, {"readOnlyHint"}),
    "mspbotsagent_set_sop_section_visibility": ({"agent_id"}, {"idempotentHint"}),
    "mspbotsagent_clear_sop_section": (
        {"agent_id", "section"},
        {"destructiveHint", "idempotentHint"},
    ),
    # Twilio tenant config
    "mspbotsagent_get_agent_twilio_tenant_config": ({"agent_id"}, {"readOnlyHint"}),
    "mspbotsagent_set_agent_twilio_tenant_config": ({"agent_id"}, {"idempotentHint"}),
    # skills
    "mspbotsagent_list_agent_skills": ({"agent_id"}, {"readOnlyHint"}),
    "mspbotsagent_create_agent_skill": ({"agent_id", "name", "files"}, set()),
    "mspbotsagent_update_agent_skill_files": (
        {"agent_id", "capability_id", "files"},
        {"idempotentHint"},
    ),
    "mspbotsagent_delete_agent_skill": (
        {"agent_id", "capability_id"},
        {"destructiveHint"},
    ),
}


@pytest.mark.asyncio
async def test_tools_list_snapshot():
    mcp = create_mcp_server(Settings())
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == set(EXPECTED_TOOLS), f"unexpected tool set: {names}"

    by_name = {t.name: t for t in tools}
    # These two tools are deliberate exceptions to the SOP's 500-char
    # description guideline (§2.2, a "should" not a hard rule): their
    # docstrings document real, load-bearing usage guidance (trigger types
    # including "manual"; the permission/interrupt_on relationship and
    # decision values — approval moved out to its own tool per PRD-17564)
    # added upstream after this repo's SOP refactor
    # started. Trimming them to fit 500 chars would drop guidance an agent
    # needs to call these tools correctly — confirmed by cross-checking
    # origin/main's content during a rebase conflict.
    # mspbotsagent_clear_sop_section joins this list for the same reason:
    # it's a destructive, irreversible, multi-branch tool (5 section
    # values, each with a different real-world effect, plus the excluded-
    # sections note) where an agent picking the wrong one can't be undone —
    # the per-section effect table is load-bearing, not decorative
    # (PRD-17514, scope narrowed to 5 sections per Leo Yang's later comment).
    # mspbotsagent_set_agent_twilio_tenant_config joins for the same reason:
    # the three-state (omit/null/value) semantics, the system-key rejection
    # (an agent that doesn't know this exists will send a credential key and
    # get a confusing whole-call rejection), and the intake_playbook-not-
    # intake_prompt distinction are each independently load-bearing for
    # calling it correctly (PRD-17557).
    # mspbotsagent_set_sop_section_visibility joins for the same reason:
    # the explicit "show/hide however phrased, in whatever language" intent-
    # matching guidance is the fix for a real tool-selection accuracy problem
    # (an agent reading only "show" as a query verb would misroute to the
    # read-only sibling tool) — verbatim text requested by the user, not
    # decorative.
    # mspbotsagent_get_agent_permissions and mspbotsagent_upsert_agent_permissions
    # (the latter already listed above) both carry the sparse-map / whole-
    # record-replace semantics spelled out in full: permission's missing-key-
    # means-allowed convention, `tools` being a legacy off-switch table (not
    # the permission map, never write it), and the upsert's omit-a-key-and-
    # you-erase-it behavior. These are non-obvious, silent-data-loss traps —
    # trimming them would reintroduce exactly the bug this text documents.
    _LONG_DESCRIPTION_EXCEPTIONS = {
        "mspbotsagent_upsert_trigger",
        "mspbotsagent_get_agent_permissions",
        "mspbotsagent_upsert_agent_permissions",
        "mspbotsagent_clear_sop_section",
        "mspbotsagent_set_sop_section_visibility",
        "mspbotsagent_set_agent_twilio_tenant_config",
    }
    for name, (expected_required, expected_hints) in EXPECTED_TOOLS.items():
        tool = by_name[name]
        required = set(tool.inputSchema.get("required", []))
        assert required == expected_required, f"{name}: required={required}"

        description = tool.description or ""
        if name not in _LONG_DESCRIPTION_EXCEPTIONS:
            assert len(description) <= 500, f"{name}: description too long ({len(description)})"
        first_line = description.strip().splitlines()[0] if description.strip() else ""
        assert len(first_line) <= 100, f"{name}: first line too long: {first_line!r}"
        assert "API:" not in description, f"{name}: leaked implementation detail"
        assert "GET /" not in description and "POST /" not in description, (
            f"{name}: leaked implementation detail"
        )

        annotations = tool.annotations
        actual_hints = set()
        if annotations is not None:
            for hint in ("readOnlyHint", "destructiveHint", "idempotentHint"):
                if getattr(annotations, hint, None) is True:
                    actual_hints.add(hint)
        assert actual_hints == expected_hints, f"{name}: hints={actual_hints}"


@pytest.mark.asyncio
async def test_service_instructions_present_and_bounded():
    mcp = create_mcp_server(Settings())
    assert mcp.instructions
    assert len(mcp.instructions) <= 1500


@pytest.mark.parametrize(
    "status_code,expected_code,expected_retryable",
    [
        (0, "upstream_error", True),
        (400, "invalid_argument", False),
        (401, "unauthorized", False),
        (403, "unauthorized", False),
        (404, "not_found", False),
        (422, "invalid_argument", False),
        (429, "rate_limited", True),
        (500, "upstream_error", True),
        (503, "upstream_error", True),
    ],
)
def test_error_envelope_mapping(status_code, expected_code, expected_retryable):
    import json

    err = AgentError(status_code, "boom")
    envelope = json.loads(err.to_envelope())
    assert envelope["error"]["code"] == expected_code
    assert envelope["error"]["retryable"] is expected_retryable
    assert envelope["error"]["message"] == "boom"


@pytest.mark.parametrize(
    "keys,expected_bad",
    [
        ({"execute": "deny"}, []),
        ({"read_file": "ask", "write_file": "allow"}, []),
        ({"qbo.createInvoice": "ask"}, []),
        ({"send_email": "deny"}, ["send_email"]),
        ({"shell.exec": "deny"}, []),  # dotted — assumed to be a real connector id, unverifiable statically
        ({"execute": "deny", "send_message": "ask"}, ["send_message"]),
        ({"artifact": "allow"}, []),
        ({"show": "deny"}, []),
        (None, []),
        ({}, []),
    ],
    ids=[
        "execute-ok",
        "read-write-ok",
        "connector-dotted-ok",
        "unknown-bare-bad",
        "dotted-assumed-ok",
        "mixed-one-bad",
        "artifact-accepted",
        "show-accepted",
        "none-ok",
        "empty-ok",
    ],
)
def test_invalid_bare_tool_ids(keys, expected_bad):
    assert sorted(_invalid_bare_tool_ids(keys)) == sorted(expected_bad)


def test_normalize_legacy_tool_keys():
    """PRD-21381: `download` and the already-retired `upload_artifact` both
    normalize to the current `artifact`; an already-current key wins if both
    are present; non-dict input passes through unchanged."""
    assert _normalize_legacy_tool_keys({"download": "ask"}) == {"artifact": "ask"}
    assert _normalize_legacy_tool_keys({"upload_artifact": "deny"}) == {"artifact": "deny"}
    assert _normalize_legacy_tool_keys({"download": "ask", "artifact": "deny"}) == {"artifact": "deny"}
    assert _normalize_legacy_tool_keys(None) is None
    assert _normalize_legacy_tool_keys("not-a-dict") == "not-a-dict"


@pytest.mark.parametrize(
    "legacy_key",
    ["download", "upload_artifact"],
    ids=["download-accepted", "upload_artifact-accepted"],
)
def test_legacy_tool_keys_pass_validation_after_normalize(legacy_key):
    """PRD-21381 AC4: a bare legacy key, run through the same
    normalize-then-validate sequence mspbotsagent_upsert_agent_permissions
    uses, is accepted (not rejected) and lands on `artifact`."""
    normalized = _normalize_legacy_tool_keys({legacy_key: "ask"})
    assert normalized == {"artifact": "ask"}
    assert _invalid_bare_tool_ids(normalized) == []


@pytest.mark.asyncio
async def test_clear_sop_section_has_real_enum_constraint():
    # SOP §3 (02-开发阶段SOP.md): enum-shaped params must carry a real JSON
    # Schema `enum`, not just enum values mentioned in prose. section not
    # only affects tool_use accuracy — it's a destructive action, so a
    # client that skips the enum entirely would only find out it guessed
    # wrong after the (irreversible) API call.
    mcp = create_mcp_server(Settings())
    tools = await mcp.list_tools()
    tool = next(t for t in tools if t.name == "mspbotsagent_clear_sop_section")
    section_schema = tool.inputSchema["properties"]["section"]
    assert set(section_schema.get("enum", [])) == {
        "evaluation",
        "humanInLoop",
        "teams",
        "twilio",
        "orgChart",
    }


@pytest.mark.asyncio
async def test_clear_sop_section_rejects_unknown_section_before_calling_api():
    # FastMCP validates `section` against the Literal via pydantic before
    # the tool function ever runs — confirmed here by asserting the stub
    # client's delete() is never reached, and that the rejection names the
    # real enum (not this repo's own {"error": {...}} shape, which only
    # AgentError.to_envelope() produces — pydantic's ToolError is a
    # different, but equally real, structured rejection).
    from mcp.server.fastmcp.exceptions import ToolError

    from mspbots_agent_mcp.tools import sop_author

    captured = {}

    class _StubClient:
        async def delete(self, path):
            captured["called"] = path
            return {"ok": True}

    mcp = FastMCP(name="test")
    sop_author.register(mcp, lambda: _StubClient())
    with pytest.raises(ToolError) as exc_info:
        await mcp.call_tool(
            "mspbotsagent_clear_sop_section", {"agent_id": "a1", "section": "not_a_real_section"}
        )
    assert "evaluation" in str(exc_info.value)
    assert "called" not in captured, "must reject before ever calling the API"


@pytest.mark.asyncio
async def test_set_twilio_tenant_config_omit_null_value_are_distinguishable():
    # The whole point of the _UNSET sentinel default (see twilio_tenant_
    # config.py's module docstring) is that a plain `= None` default
    # couldn't tell "field absent from the call" apart from "field
    # explicitly set to null" — both parse to Python None. Exercise all
    # three states in one call and inspect the exact body sent upstream:
    # omitted keys must not appear at all (not even as None), an explicit
    # null must appear as None, and a real value must appear as itself.
    from mspbots_agent_mcp.tools import twilio_tenant_config

    captured = {}

    class _StubClient:
        async def patch(self, path, json_body):
            captured["path"] = path
            captured["body"] = json_body
            return {"success": True, "data": {"created": False, "twilio": {}}}

    mcp = FastMCP(name="test")
    twilio_tenant_config.register(mcp, lambda: _StubClient())
    await mcp.call_tool(
        "mspbotsagent_set_agent_twilio_tenant_config",
        {"agent_id": "a1", "welcome_greeting": None, "language": "en-US"},
        # idle_seconds, transfer_groups, etc. all omitted — must not appear in body
    )
    body = captured["body"]
    assert captured["path"] == "/api/agents/a1/twilio/tenant-config"
    assert body == {"welcome_greeting": None, "language": "en-US"}
    assert "idle_seconds" not in body
    assert "transfer_groups" not in body


@pytest.mark.asyncio
async def test_set_twilio_tenant_config_rejects_empty_call_before_calling_api():
    from mspbots_agent_mcp.tools import twilio_tenant_config

    captured = {}

    class _StubClient:
        async def patch(self, path, json_body):
            captured["called"] = path
            return {"success": True, "data": {}}

    mcp = FastMCP(name="test")
    twilio_tenant_config.register(mcp, lambda: _StubClient())
    result = await mcp.call_tool("mspbotsagent_set_agent_twilio_tenant_config", {"agent_id": "a1"})
    text = result[0][0].text
    assert "at least one field" in text
    assert "called" not in captured, "must reject before ever calling the API"


@pytest.mark.asyncio
async def test_twilio_voice_params_carry_the_real_preset_table():
    # voice/say_voice are NOT free text — an invented or mismatched id gets
    # the call disconnected by ConversationRelay (per PRD-17557's updated
    # API doc). There's no "list valid voices" tool for an agent to query,
    # so the preset table has to live in these two params' own descriptions
    # to be usable at call time. Guard the load-bearing values (not just
    # prose) against a future edit silently dropping them.
    mcp = create_mcp_server(Settings())
    tools = await mcp.list_tools()
    tool = next(t for t in tools if t.name == "mspbotsagent_set_agent_twilio_tenant_config")
    props = tool.inputSchema["properties"]
    voice_desc = props["voice"]["description"]
    say_voice_desc = props["say_voice"]["description"]

    # one real id per gender, from the actual PRD-17557 preset table
    assert "UgBBYS2sOqTuMpoF3BR0" in voice_desc  # male-young-friendly (default)
    assert "DXFkLCBUTmvXpp2QwZjA" in voice_desc  # female-customer-support (default)
    assert "Polly.Matthew-Generative" in voice_desc
    assert "say_voice" in voice_desc  # tells the caller the two must be paired

    assert "Polly." in say_voice_desc  # the required-prefix format
    assert "voice" in say_voice_desc  # points back at the pairing requirement
