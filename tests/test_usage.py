"""Usage-overview shaping tests (PRD-18216).

No network calls: the wrapper's only own logic is `_shape_overview`, a pure
function over an already-fetched payload, so it is tested directly. The
no-credentials path is exercised through the registered tool with a
client_factory that returns None.

Why the truncation tests carry the weight here: a full page of `breakdown`
at the API's maximum pageSize is more than a model can absorb, so this tool
trims it — and trimming it silently would let a model read a short breakdown
next to a full `breakdownTotal` and conclude it had seen every agent. AC7
forbids that state. `_BREAKDOWN_BUDGET` is this tool's own budget; `_json`
caps nothing any more, so this is the only place output is bounded.
"""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from mspbots_agent_mcp.tools.usage import (
    _BREAKDOWN_BUDGET as MAX_CHARS,
    _shape_overview,
    register,
)


def _row(i: int) -> dict:
    return {
        "sopId": 4700 + i,
        "agentId": f"agt_01HX{i:04d}",
        "name": f"Onboarding Agent {i}",
        "model": "azure/gpt-5.4",
        "cost": 4.2,
        "runs": 31,
        "createdBy": "ken.lee@mspbots.ai",
        "createdAt": "2026-08-12T09:14:03.000Z",
        "deleted": False,
    }


def _payload(rows: int = 3, **overrides) -> dict:
    payload = {
        "range": "7d",
        "startDate": "2026-08-28",
        "endDate": "2026-09-03",
        "totalSpend": 12.34,
        "threads": 57,
        "runs": 143,
        "agents": 9,
        "daily": [{"date": "2026-08-28", "spend": 1.02}],
        "breakdown": [_row(i) for i in range(rows)],
        "breakdownTotal": rows,
        "page": 1,
        "pageSize": 10,
        "sortBy": "cost",
        "sortOrder": "desc",
        "timeZone": "America/Chicago",
        "spendUnavailable": False,
        "spendTruncated": False,
        "debug": {"totalMs": 812, "phases": {}, "facts": {}},
    }
    payload.update(overrides)
    return payload


# TC2 / AC8
def test_shape_overview_drops_debug_and_keeps_everything_else():
    payload = _payload()
    shaped = _shape_overview(payload)

    assert "debug" not in shaped
    for key, value in payload.items():
        if key == "debug":
            continue
        assert shaped[key] == value, key
    # The input is not mutated: the caller may still want the raw payload.
    assert "debug" in payload


# TC4 / AC6
@pytest.mark.parametrize("unavailable,truncated", [(True, False), (False, True), (True, True)])
def test_spend_flags_pass_through_untouched(unavailable, truncated):
    shaped = _shape_overview(
        _payload(spendUnavailable=unavailable, spendTruncated=truncated)
    )
    assert shaped["spendUnavailable"] is unavailable
    assert shaped["spendTruncated"] is truncated


# TC3 / AC7
def test_oversized_breakdown_is_marked_not_silently_dropped():
    payload = _payload(rows=1000)
    payload["breakdownTotal"] = 1000
    assert len(json.dumps(payload, separators=(",", ":"))) > MAX_CHARS

    shaped = _shape_overview(payload)

    assert shaped["breakdownTruncatedByToolOutput"] is True
    assert shaped["breakdownReturned"] == len(shaped["breakdown"])
    assert shaped["breakdownReturned"] < 1000
    assert len(json.dumps(shaped, separators=(",", ":"), ensure_ascii=False)) <= MAX_CHARS


# TC3 / AC7 — the half that makes the marker meaningful
def test_breakdown_total_still_reports_upstream_value_when_truncated():
    payload = _payload(rows=1000)
    payload["breakdownTotal"] = 1000

    shaped = _shape_overview(payload)

    assert shaped["breakdownTotal"] == 1000
    assert shaped["breakdownReturned"] != shaped["breakdownTotal"]


def test_small_payload_carries_no_truncation_markers():
    shaped = _shape_overview(_payload(rows=3))
    assert "breakdownTruncatedByToolOutput" not in shaped
    assert "breakdownReturned" not in shaped


def test_shape_overview_tolerates_a_missing_or_odd_breakdown():
    # An older deployment, or an error-shaped body, must not raise here.
    assert _shape_overview({"totalSpend": 1.0}) == {"totalSpend": 1.0}
    assert _shape_overview({"breakdown": None}) == {"breakdown": None}
    assert _shape_overview([1, 2, 3]) == [1, 2, 3]


# TC5 / AC4
@pytest.mark.asyncio
async def test_missing_credentials_returns_not_configured_envelope():
    mcp = FastMCP("test")
    register(mcp, lambda: None)
    tools = {t.name: t for t in await mcp.list_tools()}
    name = "mspbotsagent_get_usage_overview"
    assert name in tools

    # Unwrapping follows tests/test_tools.py's idiom for this MCP version.
    result = await mcp.call_tool(name, {})
    payload = json.loads(result[0][0].text)
    assert payload["error"]["code"] == "not_configured"
    assert payload["error"]["retryable"] is False
