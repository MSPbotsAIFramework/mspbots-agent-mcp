"""SOP library: list the tenant's SOPs, and hold a synchronous turn with one.

A SOP (standard operating procedure) is a tenant-level record that owns its
own agent, provisioned for it when the SOP is created. That agent is what
mspbotsagent_chat_with_sop talks to, which is why the chat tool takes a
sop_id and never an agent_id.

Not to be confused with tools/sop_author.py: those edit the SOP *draft
sections* hanging off an agent (name / source / purpose / data sources /
procedure). This module reads the library and runs a conversation.
"""

from collections.abc import Callable
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json, error_envelope
from ..api_client import AgentClient, AgentError
from ._common import NO_TOKEN

_MAX_PAGE_SIZE = 100

# A turn runs a real agent: tool calls, LLM latency, possibly a sub-agent.
# Aegra's own safety net is an hour; waiting that long inside a tool call is
# useless, so we give up earlier and tell the caller the run is still going.
_CHAT_READ_TIMEOUT = 300.0

# Mirrors components/assistant-ui/aegra/messages.ts in mb-platform-agent:
# these are internal scaffolding messages the web UI hides, and reading one
# back as "the agent's reply" would be wrong.
_HIDDEN_SOURCES = {
    "rubric_grader",
    "sop_context",
    "sop_suffix",
    "compact_summary",
    "task_notification",
}


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        str(part.get("text") or "")
        for part in content
        if isinstance(part, dict) and part.get("text") is not None
    ]
    return "\n".join(part for part in parts if part)


def _is_hidden(message: dict) -> bool:
    if message.get("name") == "rubric_grader":
        return True
    kwargs = message.get("additional_kwargs")
    source = kwargs.get("lc_source") if isinstance(kwargs, dict) else None
    return str(source or "") in _HIDDEN_SOURCES


def _last_assistant_text(run_output: Any) -> str:
    messages = run_output.get("messages") if isinstance(run_output, dict) else None
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if message.get("type") != "ai" and message.get("role") != "assistant":
            continue
        if _is_hidden(message):
            continue
        text = _text_of(message.get("content")).strip()
        if text:
            return text
    return ""


def _paused_actions(run_output: Any) -> list[dict]:
    interrupts = run_output.get("__interrupt__") if isinstance(run_output, dict) else None
    first = interrupts[0] if isinstance(interrupts, list) and interrupts else None
    value = first.get("value") if isinstance(first, dict) else None
    requests = value.get("action_requests") if isinstance(value, dict) else None
    if not isinstance(requests, list):
        return []
    return [
        {
            "action": str(request.get("name") or ""),
            "reason": str(request.get("description") or ""),
            "args": request.get("args") if isinstance(request.get("args"), dict) else None,
        }
        for request in requests
        if isinstance(request, dict)
    ]


def register(mcp: FastMCP, client_factory: Callable[[], AgentClient | None]) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def mspbotsagent_list_sops(
        search: Annotated[
            str | None,
            Field(
                description=(
                    "Case-insensitive SUBSTRING of the SOP name. Nothing else is matched: "
                    "not the description, not the tags, not the body, and there is no "
                    "fuzzy, synonym or cross-language matching. So an empty result is NOT "
                    "evidence that no such SOP exists -- it usually means the wording "
                    "differs. To find the SOP a user described in their own words, leave "
                    "this out, read each row's name AND description, and pick the match "
                    "yourself; `total` tells you whether more pages remain. Use search "
                    "only for a literal fragment of the name you already know."
                )
            ),
        ] = None,
        status: Annotated[
            Literal["draft", "published"] | None,
            Field(description="Keep only SOPs in this state."),
        ] = None,
        page: Annotated[int, Field(ge=1, description="1-based page number.")] = 1,
        page_size: Annotated[int, Field(ge=1, description="Rows per page (max 100).")] = 20,
    ) -> str:
        """List the tenant's SOPs, the procedures its agents follow.

        Use to find a SOP by name, or to answer "what SOPs do we have",
        "which are still drafts".

        Rows are the backend's as-is. `agent_id` is the agent the SOP
        owns; `agent_live` false means that agent is gone.

        Not the SOP draft sections on an agent — those are
        mspbotsagent_get_sop_purpose and its siblings.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(
                "/api/sops",
                params={
                    "search": search,
                    "status": status,
                    "page": page,
                    "pageSize": min(page_size, _MAX_PAGE_SIZE),
                },
            )
            return dump_json(result)
        except AgentError as e:
            return e.to_envelope()

    # NOTE: mspbotsagent_chat_with_sop is temporarily hidden from external
    # clients: its @mcp.tool decorator below is commented out, so it no
    # longer appears in tools/list and cannot be called. The implementation
    # is left untouched; re-expose by uncommenting the decorator (and the
    # matching entry in tests/test_tools.py, and the skipped tests in
    # tests/test_sops.py).

    # @mcp.tool()
    async def mspbotsagent_chat_with_sop(
        sop_id: Annotated[
            int,
            Field(
                description=(
                    "SOP to talk to. Ids come from mspbotsagent_list_sops -- including "
                    "when all you have is an agent_id: list the SOPs and match on the "
                    "`agent_id` each row carries. Never guess an id."
                )
            ),
        ],
        message: Annotated[str, Field(description="What to say to the SOP's agent this turn.")],
        thread_id: Annotated[
            str | None,
            Field(
                description=(
                    "Continue this conversation. Use the threadId a previous call returned."
                )
            ),
        ] = None,
        new_thread: Annotated[
            bool | None,
            Field(description="True starts a fresh conversation instead of continuing one."),
        ] = None,
    ) -> str:
        """Ask a SOP's agent something and wait for its whole reply.

        One call is one full turn: blocking, not streamed, and it can take
        minutes. If the wait runs out the run keeps going — never re-send;
        call again on the same conversation to see how it ended.

        Omit thread_id and new_thread to continue that agent's default
        conversation, the one the web UI shows; send the returned threadId
        back to stay in this one. `status` explains an empty reply.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        try:
            sop = await client.get(f"/api/sops/{sop_id}")
        except AgentError as e:
            return e.to_envelope()

        agent_id = (sop or {}).get("agent_id")
        if not agent_id:
            return error_envelope(
                "invalid_argument",
                f"SOP {sop_id} has no agent, so there is nothing to talk to. Open it once in "
                "the Agent Platform to provision one.",
                False,
            )

        body: dict = {"message": message}
        if thread_id:
            body["threadId"] = thread_id
        if new_thread is not None:
            body["newThread"] = new_thread

        try:
            result = await client.post(
                f"/api/agents/{agent_id}/run/wait",
                body,
                read_timeout=_CHAT_READ_TIMEOUT,
                retries=0,
            )
        except AgentError as e:
            if e.status_code == 0:
                return error_envelope(
                    "upstream_error",
                    f"No reply within {int(_CHAT_READ_TIMEOUT)}s ({e.message}). The run is "
                    "probably still executing. Do not re-send this message; call again on the "
                    "same conversation to read the result once it lands.",
                    False,
                )
            return e.to_envelope()

        # The backend answers 200 with success=false for a failed run, so a
        # bad run never reaches AgentError above.
        if isinstance(result, dict) and result.get("success") is False:
            parts = (result.get("error"), result.get("details"))
            return error_envelope(
                "upstream_error",
                " — ".join(str(p) for p in parts if p) or "Agent run failed",
                False,
            )

        data = (result or {}).get("data") or {}
        run_output = data.get("result") or {}
        run_error = run_output.get("__error__") if isinstance(run_output, dict) else None
        if isinstance(run_error, dict):
            return error_envelope(
                "upstream_error",
                str(run_error.get("message") or run_error.get("error") or "Run failed"),
                False,
            )

        paused = _paused_actions(run_output)
        reply = _last_assistant_text(run_output)
        payload: dict = {
            "sopId": sop_id,
            "sopName": (sop or {}).get("name"),
            "agentId": agent_id,
            "threadId": data.get("threadId"),
            "status": "interrupted" if paused else ("completed" if reply else "no_reply"),
            "reply": reply,
        }
        if paused:
            payload["pausedActions"] = paused
            payload["note"] = (
                "The run paused for human approval and stays paused until someone decides in "
                "the Agent Platform. The reply is whatever the agent said before pausing."
            )
        elif not reply:
            payload["note"] = "The run finished without producing an assistant message."
        return dump_json(payload)
