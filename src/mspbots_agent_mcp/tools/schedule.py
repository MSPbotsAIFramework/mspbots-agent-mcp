from collections.abc import Callable
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from .._json import dump_json, error_envelope
from ..api_client import AgentClient, AgentError
from ._common import NO_TOKEN


class TicketRef(BaseModel):
    integration: str
    id: str


def _invalid(message: str) -> str:
    return error_envelope("invalid_argument", message, False)


def register(
    mcp: FastMCP,
    client_factory: Callable[[], AgentClient | None],
    caller_factory: Callable[[], tuple[str, str] | None],
) -> None:

    # PRD-22270: the tool name must stay exactly "schedule_resume" -- the platform's
    # interruptOn entry (the pause itself) matches on it, so no mspbotsagent_ prefix.
    @mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
    async def schedule_resume(
        key: Annotated[
            str, Field(description="Names this wait within the run; the same key again moves it.")
        ],
        reason: Annotated[str, Field(description="Why the run waits.")],
        resume_at: Annotated[
            str | None,
            Field(description="Resume time: ISO 8601 with offset, or local date-time plus timezone."),
        ] = None,
        resume_in: Annotated[
            str | None, Field(description='Resume after this long from now, e.g. "3m", "2h", "14d".')
        ] = None,
        timezone: Annotated[str | None, Field(description="IANA timezone.")] = None,
        wake_on: Annotated[
            list[Literal["note", "updated", "closed", "deleted"]] | None,
            Field(description="Ticket events that wake the run early."),
        ] = None,
        cancel_on: Annotated[
            list[Literal["closed", "deleted"]] | None,
            Field(description="Ticket events that cancel the wait."),
        ] = None,
        ticket: Annotated[
            TicketRef | None, Field(description="Ticket {integration, id} the events refer to.")
        ] = None,
    ) -> str:
        """Pause this run until a time, then continue it in the same run.

        Use for "wait 3 minutes", "check again tomorrow 9am", "come back when
        the ticket is updated". Give exactly one of resume_at / resume_in. The
        same key again moves the wait instead of adding one. Only works inside
        an agent run. Returns the wait (wait_id, status, resume_at, timezone);
        refusals (date without time, too far ahead, wait limit reached) come
        back as errors and nothing is scheduled.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        caller = caller_factory()
        if caller is None:
            return error_envelope(
                "not_configured",
                "schedule_resume can only be called from inside an agent run "
                "(no run thread or agent id was sent).",
                False,
            )
        if bool(resume_at) == bool(resume_in):
            return _invalid("Give exactly one of resume_at or resume_in.")
        if not key.strip() or not reason.strip():
            return _invalid("key and reason are required.")

        thread_id, agent_id = caller
        body = {
            name: value
            for name, value in (
                ("resume_at", resume_at),
                ("resume_in", resume_in),
                ("timezone", timezone),
                ("key", key),
                ("reason", reason),
                ("wake_on", wake_on),
                ("cancel_on", cancel_on),
                ("ticket", ticket.model_dump() if ticket else None),
            )
            if value is not None
        }
        try:
            # retries=0: a 429 here means the wait cap is reached, waiting will not help;
            # the key makes a repeated call safe, so the model can simply call again.
            result = await client.post(
                f"/api/agents/{agent_id}/threads/{thread_id}/schedule-resume", body, retries=0
            )
            return dump_json(result)
        except AgentError as e:
            # The endpoint answers a bare 404 "Not found" when its feature flags are off
            # ("Agent not found" is a different, real 404).
            if e.status_code == 404 and e.message.strip().rstrip(".").lower() == "not found":
                return error_envelope(
                    "not_available",
                    "schedule_resume is not available on this platform yet "
                    "(the scheduled-actions feature is off).",
                    False,
                )
            if e.status_code == 429:
                return error_envelope("limit_reached", e.message, False)
            return e.to_envelope()
