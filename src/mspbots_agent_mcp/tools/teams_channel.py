"""PRD-22059: the two agent-facing Teams channel tools. Both are thin pass-throughs to the
platform's safety gate (mb-platform-agent `/api/agents/{id}/teams-channel/{preview,send}`): the
gate, not this file, decides whether a post is allowed, counts it and logs it.
"""

import re
from collections.abc import Callable
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json, error_envelope
from ..api_client import AgentClient, AgentError
from ._common import NO_TOKEN

# The gate puts its refusal as "<reason_code>: <message>" in the error text (the shared client
# only keeps the text of an error answer), so the stable reason survives to the model.
_REASON = re.compile(r"^([a-z][a-z0-9_]{2,}): (.+)$", re.DOTALL)


def _not_in_run(tool: str) -> str:
    return error_envelope(
        "not_configured",
        f"{tool} can only be called from inside an agent run (no run thread or agent id was sent).",
        False,
    )


def _from_error(tool: str, e: AgentError) -> str:
    # A bare 404 "Not found" is a platform without the gate; "Agent not found" is a real 404.
    if e.status_code == 404 and e.message.strip().rstrip(".").lower() == "not found":
        return error_envelope(
            "not_available", f"{tool} is not available on this platform yet.", False
        )
    match = _REASON.match(e.message or "")
    if e.status_code in (403, 429, 502) and match:
        # A refusal or a failed post is final for this call and must not be retried blindly
        # (a post is not idempotent); a rate-limited message names how long to wait.
        return error_envelope(match.group(1), match.group(2), False)
    return e.to_envelope()


def register(
    mcp: FastMCP,
    client_factory: Callable[[], AgentClient | None],
    caller_factory: Callable[[], tuple[str, str] | None],
) -> None:
    async def _call(
        tool: str,
        action: Literal["preview", "send"],
        team_id: str,
        channel_id: str,
        body: str,
        body_content_type: str,
        subject: str | None,
        importance: str,
        mention_user_ids: list[str] | None,
    ) -> str:
        client = client_factory()
        if client is None:
            return NO_TOKEN
        caller = caller_factory()
        if caller is None:
            return _not_in_run(tool)
        if not team_id.strip() or not channel_id.strip() or not body.strip():
            return error_envelope(
                "invalid_argument", "team_id, channel_id and body are required.", False
            )

        thread_id, agent_id = caller
        payload = {
            name: value
            for name, value in (
                ("team_id", team_id.strip()),
                ("channel_id", channel_id.strip()),
                ("body", body),
                ("body_content_type", body_content_type),
                ("subject", subject),
                ("importance", importance),
                ("mention_user_ids", mention_user_ids),
                ("run_id", thread_id),
            )
            if value is not None
        }
        try:
            # retries=0 on send: a post is not idempotent, so a lost answer must never be
            # retried into a second post. Preview changes nothing and keeps the default.
            kwargs = {"retries": 0} if action == "send" else {}
            result = await client.post(
                f"/api/agents/{agent_id}/teams-channel/{action}", payload, **kwargs
            )
            return dump_json(result)
        except AgentError as e:
            return _from_error(tool, e)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def mspbotsagent_teams_channel_preview(
        team_id: Annotated[str, Field(description="The team's id.")],
        channel_id: Annotated[str, Field(description="The channel's id.")],
        body: Annotated[str, Field(description="Message body, text or HTML.")],
        body_content_type: Annotated[
            Literal["text", "html"], Field(description='"text" or "html".')
        ] = "text",
        subject: Annotated[str | None, Field(description="Optional subject line.")] = None,
        importance: Annotated[
            Literal["normal", "high", "urgent"], Field(description="Post importance.")
        ] = "normal",
        mention_user_ids: Annotated[
            list[str] | None, Field(description="Users to @mention, at most 5.")
        ] = None,
    ) -> str:
        """Check a Teams channel post without posting it.

        Same inputs as mspbotsagent_teams_channel_send. Returns allowed (true/false),
        a reason code when denied, and the message that would be posted. Nothing is
        posted, logged or counted toward the rate limit. Only works inside an agent run.
        """
        return await _call(
            "mspbotsagent_teams_channel_preview", "preview", team_id, channel_id, body,
            body_content_type, subject, importance, mention_user_ids,
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
        )
    )
    async def mspbotsagent_teams_channel_send(
        team_id: Annotated[str, Field(description="The team's id.")],
        channel_id: Annotated[str, Field(description="The channel's id.")],
        body: Annotated[str, Field(description="Message body, text or HTML.")],
        body_content_type: Annotated[
            Literal["text", "html"], Field(description='"text" or "html".')
        ] = "text",
        subject: Annotated[str | None, Field(description="Optional subject line.")] = None,
        importance: Annotated[
            Literal["normal", "high", "urgent"], Field(description="Post importance.")
        ] = "normal",
        mention_user_ids: Annotated[
            list[str] | None, Field(description="Users to @mention, at most 5.")
        ] = None,
    ) -> str:
        """Post a new message to a Teams channel through the platform's safety gate.

        Allowed only when a person has switched channel posts on for this agent and
        the channel is on its list in the client's Microsoft 365 tenant, within
        20 posts per hour per channel. Otherwise refused with a reason code and nothing
        is posted. Irreversible once sent; no thread replies. Only works inside an agent run.
        """
        return await _call(
            "mspbotsagent_teams_channel_send", "send", team_id, channel_id, body,
            body_content_type, subject, importance, mention_user_ids,
        )
