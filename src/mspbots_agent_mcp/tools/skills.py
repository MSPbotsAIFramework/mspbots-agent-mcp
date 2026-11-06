from collections.abc import Callable
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json, error_envelope
from ..api_client import AgentClient, AgentError
from ._common import NO_TOKEN

# `files` items ("SkillFile") are assumed to be {"path": str, "content": str}
# — a relative file path within the skill package (e.g. "SKILL.md",
# "scripts/helper.py") plus its raw text content. This wasn't included in the
# API spec handed to us (only referenced via a $ref with no definitions
# block attached) — it's inferred from how SKILL.md-based skill packages
# conventionally work elsewhere. Verify against the real API before relying
# on this for anything beyond a first draft.


def register(mcp: FastMCP, client_factory: Callable[[], AgentClient | None]) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def mspbotsagent_list_agent_skills(
        agent_id: Annotated[str, Field(description="Agent whose skills to list.")],
    ) -> str:
        """Check what packaged SKILL.md skills an agent has, e.g. "what skills does it have".

        This is about installed skill packages, not what the agent is
        allowed/permitted to do — for "what is it allowed to do" or
        "what actions can it take" use mspbotsagent_get_agent_permissions
        instead. Covers org-shared, that agent's own private skills, and
        platform (mspbots) skills. selected=true means not opted out.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(f"/api/agents/{agent_id}/skills")
            return dump_json(result)
        except AgentError as e:
            return e.to_envelope()

    @mcp.tool()
    async def mspbotsagent_create_agent_skill(
        agent_id: Annotated[str, Field(description="Agent to create the skill for.")],
        name: Annotated[
            str, Field(description="Skill name, used to derive its slug/package name.")
        ],
        files: Annotated[
            list[dict],
            Field(
                description=(
                    'Non-empty list of {"path", "content"} file objects; must '
                    "include a SKILL.md or the API rejects the call."
                )
            ),
        ],
    ) -> str:
        """Package a brand-new custom skill for this agent and install it.

        Use when the user wants to teach the agent a new capability that
        doesn't exist yet, e.g. "give this agent a skill for drafting
        renewal emails". `files` must include exactly one SKILL.md — its
        frontmatter (name/description/trigger/allowed-tools) is parsed
        server-side.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        if not files:
            return "Error: files must include at least one file (a SKILL.md is required)"
        try:
            result = await client.post(
                f"/api/agents/{agent_id}/skills/create",
                {"name": name, "files": files},
            )
            return dump_json(result)
        except AgentError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
    async def mspbotsagent_update_agent_skill_files(
        agent_id: Annotated[str, Field(description="Owning agent.")],
        capability_id: Annotated[str, Field(description="Skill (capability) to update.")],
        files: Annotated[
            list[dict],
            Field(
                description=(
                    'The new complete file set as {"path", "content"} objects, '
                    "including SKILL.md."
                )
            ),
        ],
        note: Annotated[
            str | None, Field(description='Version note. Defaults to "Edit" if omitted.')
        ] = None,
    ) -> str:
        """Edit an existing custom skill's content and publish a new version.

        Use when the user wants to change how one of this agent's own
        skills works, e.g. "update its renewal-email skill to mention the
        new discount". Only works on a private skill owned by this agent —
        the server rejects org/mspbots skills.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        if not files:
            return "Error: files must include at least one file (a SKILL.md is required)"
        body: dict = {"files": files}
        if note is not None:
            body["note"] = note
        try:
            result = await client.put(
                f"/api/agents/{agent_id}/skills/{capability_id}/files", body
            )
            return dump_json(result)
        except AgentError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def mspbotsagent_delete_agent_skill(
        agent_id: Annotated[str, Field(description="Owning agent.")],
        capability_id: Annotated[str, Field(description="Private skill to delete.")],
    ) -> str:
        """Remove a custom skill from this agent, e.g. "delete its renewal-email skill".

        Soft-deletes and uninstalls it. Only works on a private skill owned
        by this agent — org/mspbots skills cannot be deleted this way.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.delete_with_body(
                f"/api/agents/{agent_id}/skills", {"id": capability_id}
            )
            return dump_json(result)
        except AgentError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
    async def mspbotsagent_set_agent_skill_enabled(
        agent_id: Annotated[str, Field(description="Agent to change.")],
        ref: Annotated[
            str, Field(description="Skill ref, from mspbotsagent_list_agent_skills.")
        ],
        enabled: Annotated[
            bool, Field(description="False turns the skill off for this agent.")
        ] = True,
        skill_type: Annotated[
            Literal["mspbots", "org", "agent"],
            Field(description="The skill's scope, from the same list entry as ref."),
        ] = "org",
    ) -> str:
        """Turn one skill on or off for this agent, e.g. "disable the renewal-email skill".

        Take ref and skill_type from mspbotsagent_list_agent_skills, where
        selected=true means on. This only flips the switch: the skill's
        files are not changed and nothing is deleted. pending=true in the
        reply means the flag was saved but installing/uninstalling it in
        the runtime failed, so it is not in effect yet and will be retried.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        body = {"ref": ref, "enabled": enabled, "type": skill_type}
        try:
            result = await client.put(f"/api/agents/{agent_id}/skills", body)
        except AgentError as e:
            return e.to_envelope()
        # A missing agent or an unresolvable ref comes back as HTTP 200 with
        # success=false, so it never reaches AgentError above.
        if isinstance(result, dict) and result.get("success") is False:
            return error_envelope(
                "invalid_argument",
                str(result.get("error") or "Could not set the skill's enabled state"),
                False,
            )
        return dump_json(result)
