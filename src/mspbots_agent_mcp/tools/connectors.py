from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json, error_envelope
from ..api_client import AgentClient, AgentError
from ._common import NO_TOKEN


def register(mcp: FastMCP, client_factory: Callable[[], AgentClient | None]) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def mspbotsagent_get_connectors() -> str:
        """TENANT-WIDE connector inventory. No agent_id — same for every agent.

        Use for tenant-level questions ("what integrations do we have", "is
        ConnectWise connected") or to look up a real `integration` key. For
        what ONE agent uses, call mspbotsagent_get_sop_data_sources.

        One row per connector: id/name/integration/scope/managed, org (false =
        platform, true = org-built), status (not_installed / connected /
        installed_disconnected). Discovery only — no credentials, not a way
        to connect to that server.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get("/api/capabilities/connectors/catalog")
        except AgentError as e:
            return e.to_envelope()

        items = (result or {}).get("data", []) or []
        rows = []
        for c in items:
            enabled = bool(c.get("enabled"))
            raw_connected = c.get("connected")
            if not enabled:
                status = "not_installed"
            elif raw_connected is False:
                status = "installed_disconnected"
            else:
                # True, or not-applicable (undefined, e.g. a non-gateway
                # platform connector with no per-tenant credential to
                # track) — both mean "ready".
                status = "connected"
            rows.append(
                {
                    "id": c.get("id"),
                    "org": c.get("org"),
                    "name": c.get("name"),
                    "integration": c.get("integration"),
                    "scope": c.get("scope"),
                    "managed": c.get("managed"),
                    "installed": enabled,
                    "connected": bool(raw_connected),
                    "status": status,
                }
            )
        return dump_json({"count": len(rows), "connectors": rows})

    # ----- per-connector tool switches -----------------------------------
    #
    # A connector's individual tools default to ON. The backend records only
    # the exceptions (an "off" list per agent+connector), so a tool with no
    # override reads enabled=true. Reading the switch state and flipping it
    # are the two tools below; the connector's `capabilityId` comes from
    # mspbotsagent_get_connectors' `id` (tenant-wide) or a
    # mspbotsagent_get_sop_data_sources entry's `id` (this agent's sources).

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def mspbotsagent_list_connector_tools(
        agent_id: Annotated[str, Field(description="Agent whose switches to read.")],
        capability_id: Annotated[
            str,
            Field(
                description=(
                    "Connector id (capabilityId): the `id` from "
                    "mspbotsagent_get_connectors, or from a "
                    "mspbotsagent_get_sop_data_sources entry."
                )
            ),
        ],
    ) -> str:
        """Per-tool ON/OFF switch state for one connector, for THIS agent.

        The tool names also come from mspbotsagent_get_sop_data_sources; what this
        adds is each tool's `enabled` flag. enabled=true = on (the agent can call
        it); false = this agent switched it off and can neither see nor call it.
        Every tool is on by default, so one with no override reads enabled=true. To
        flip a switch use mspbotsagent_set_connector_tools. Empty list = connector
        not connected or its tool list could not be read.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        if not agent_id or not capability_id:
            return error_envelope(
                "invalid_argument", "agent_id and capability_id are required", False
            )
        try:
            result = await client.get(
                f"/api/agents/{agent_id}/connectors/{capability_id}/tools"
            )
        except AgentError as e:
            return e.to_envelope()
        tools = ((result or {}).get("data") or {}).get("tools", []) or []
        return dump_json({"count": len(tools), "tools": tools})

    @mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
    async def mspbotsagent_set_connector_tools(
        agent_id: Annotated[str, Field(description="Agent to update.")],
        capability_id: Annotated[
            str,
            Field(
                description=(
                    "Connector id (capabilityId): the `id` from "
                    "mspbotsagent_get_connectors, or from a "
                    "mspbotsagent_get_sop_data_sources entry."
                )
            ),
        ],
        tools: Annotated[
            list[str],
            Field(
                description=(
                    "Non-empty list of tool `name`s to switch, from "
                    "mspbotsagent_list_connector_tools."
                )
            ),
        ],
        enabled: Annotated[
            bool, Field(description="true = switch the listed tools on, false = off.")
        ],
    ) -> str:
        """Turn a connector's tools on or off for one agent (single or batch).

        Every tool is on by default; only exceptions are recorded. enabled=false
        switches the listed tools off — the agent can't see or call them;
        enabled=true switches them back on. Only the names you pass change, and take
        effect on the agent's NEXT chat — no restart wait, no new thread needed.
        For "all on/off" pass the whole batch in ONE call (names from
        mspbotsagent_list_connector_tools). pending=true = saved but not yet synced.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        if not agent_id or not capability_id:
            return error_envelope(
                "invalid_argument", "agent_id and capability_id are required", False
            )
        if not tools:
            return error_envelope("invalid_argument", "tools is required", False)
        if not isinstance(enabled, bool):
            return error_envelope("invalid_argument", "enabled must be a boolean", False)
        try:
            result = await client.put(
                f"/api/agents/{agent_id}/connectors/{capability_id}/tools",
                {"tools": tools, "enabled": enabled},
            )
        except AgentError as e:
            return e.to_envelope()
        # This endpoint answers 200 with success=false for business failures
        # (agent not found, empty tools) rather than an HTTP error, so those
        # never reach AgentError above — surface them explicitly.
        if isinstance(result, dict) and result.get("success") is False:
            return error_envelope(
                "invalid_argument", result.get("error") or "Update failed", False
            )
        return dump_json((result or {}).get("data") or result)
