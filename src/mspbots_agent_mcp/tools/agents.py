from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json, error_envelope
from ..api_client import AgentClient, AgentError
from ._common import NO_TOKEN

# Permissions, evaluation, and human-in-loop approval all live on the SAME
# agent record. Reads share one GET /api/agents/:id; writes share one partial
# PUT /api/agents/:id. Because writes are partial patches, two concurrent
# PUTs to the same agent can clobber each other — callers must not update the
# same agent in parallel.
#
# These settings govern the agent's actual runtime behavior (what it is
# allowed to do, when it must pause, when a human must approve). That's
# distinct from the SOP-author tools (sop_author.py), which manage a
# documentation/planning artifact rather than enforced runtime policy.


_KNOWN_BUILTIN_TOOL_IDS = {
    "execute",
    "read_file",
    "write_file",
    "edit_file",
    "artifact",
    "show",
    "task",
    "start_async_task",
}

# PRD-21381: `download` was the pre-2026-09-09 name for the delivery tool, now
# `artifact`; `upload_artifact` was a short-lived, already-retired intermediate
# name (the platform's own catalog split delivery into `artifact`/`show` without
# ever updating this validator). Mirrors mb-platform-agent's withCurrentToolKeys().
_LEGACY_BARE_TOOL_ALIASES = {"download": "artifact", "upload_artifact": "artifact"}


def _normalize_legacy_tool_keys(keys: Any) -> Any:
    """Rename legacy bare tool ids (download, upload_artifact) to the current
    `artifact`, mirroring mb-platform-agent's withCurrentToolKeys(). A key
    already on the current name wins if both are present.
    """
    if not isinstance(keys, dict):
        return keys
    out = dict(keys)
    for legacy, current in _LEGACY_BARE_TOOL_ALIASES.items():
        if legacy not in out:
            continue
        if current not in out:
            out[current] = out[legacy]
        del out[legacy]
    return out


def _invalid_bare_tool_ids(keys: Any) -> list[str]:
    """Flag permission/interrupt_on keys that look like a built-in tool id
    (no "." — connector tool ids are always "vendor.action") but aren't one
    of the real built-ins. Catches a real failure mode: a model guessing a
    plausible-sounding id (e.g. "send_email") that doesn't exist, which
    otherwise gets forwarded to the backend silently rather than rejected.
    """
    if not isinstance(keys, dict):
        return []
    return [k for k in keys if isinstance(k, str) and "." not in k and k not in _KNOWN_BUILTIN_TOOL_IDS]


def _rules_missing_rubric(rules: Any) -> list[int]:
    """Indexes of evaluation rules with no usable `rubric`.

    The platform silently drops the whole `review` when a rule has no
    rubric, yet still answers the PUT with success — so the write looked
    fine and the read-back came back `review: null` (PRD-20168). Reject
    here instead, before anything is sent.
    """
    return [
        i
        for i, rule in enumerate(rules or [])
        if not isinstance(rule, dict)
        or not isinstance(rule.get("rubric"), str)
        or not rule["rubric"].strip()
    ]


async def _fetch_agent_data(client: AgentClient, agent_id: str) -> Any:
    result = await client.get(f"/api/agents/{agent_id}")
    return (result or {}).get("data", {}) or {}


async def _guard_policy(client: AgentClient, agent_id: str) -> str | None:
    """Return an error envelope if the agent must not be written back, else None.

    When data.policyError=true the stored policy is in a bad state; writing
    on top of it would persist a broken config, so we refuse.
    """
    try:
        data = await _fetch_agent_data(client, agent_id)
    except AgentError as e:
        return e.to_envelope()
    if data.get("policyError") is True:
        return error_envelope(
            "invalid_argument",
            "Agent has policyError=true — refusing to write back to avoid persisting "
            "a broken policy. Resolve the policy error first.",
            False,
        )
    return None


def register(mcp: FastMCP, client_factory: Callable[[], AgentClient | None]) -> None:

    # ----- permissions -----------------------------------------------------

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def mspbotsagent_get_agent_permissions(
        agent_id: Annotated[str, Field(description="Agent to read.")],
    ) -> str:
        """Check what an agent is allowed to do and when it must pause for a
        human ("what can this agent do", "why does it keep asking for
        approval", "who owns this agent"). To change any of it:
        mspbotsagent_upsert_agent_permissions.

        Returns the CURRENT SETTINGS — what is configured right now — never a
        menu of what you may configure:
          permission     {tool -> "allow" | "ask" | "deny"}, SPARSE — an
                         omitted key is ALLOWED. {} therefore means every
                         built-in runs freely: that is full autonomy, not a
                         locked-down agent and not a missing configuration.
          interruptOn    the rich form of "ask", per tool, sparse
                         (write it back as `interrupt_on`)
          owners         array of owner ids
          policyError    true = writes to permission / interruptOn are
                         refused; owners can still be set
          tools          LEGACY off-switch table, Record<string, boolean>.
                         false = that tool is switched off (unioned into
                         permission "deny"). NOT the permission map and NOT a
                         list of available tools. {} means nothing has been
                         switched off — everything is on, which is the normal
                         healthy state. Read it as switches, never as a
                         roster; never write it (use permission "deny").

        Nothing in this response lists the tools you may configure, and
        nothing here has to be non-empty before you can write. The writable
        keys are FIXED, identical for every agent, and listed in
        mspbotsagent_upsert_agent_permissions' description; they do not depend
        on what this call returns. Empty maps mean nothing is configured yet,
        never that no keys are available. Nothing needs initializing, by you
        or by the user.

        KEEP THIS RESPONSE. The upsert replaces the whole record, so whatever
        you do not send back is erased — this is the copy you edit and return.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            data = await _fetch_agent_data(client, agent_id)
        except AgentError as e:
            return e.to_envelope()
        projected = {
            "permission": data.get("permission"),
            "interruptOn": data.get("interruptOn"),
            "owners": data.get("owners"),
            "tools": data.get("tools"),
            "policyError": data.get("policyError"),
        }
        return dump_json(projected)

    @mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
    async def mspbotsagent_upsert_agent_permissions(
        agent_id: Annotated[str, Field(description="Agent to update.")],
        permission: Annotated[
            dict | None,
            Field(
                description=(
                    'Map of tool -> "allow" | "ask" | "deny". Keys are tool ids: '
                    "one of the built-ins listed below (bare, no dot), or a "
                    'connector id in "vendor.action" form (e.g. "qbo.createInvoice" '
                    "— call mspbotsagent_get_connectors first to find the real one; "
                    "guessing one is rejected). Example: "
                    '{"qbo.createInvoice": "ask", "execute": "deny"}'
                )
            ),
        ] = None,
        interrupt_on: Annotated[
            dict | None,
            Field(
                description=(
                    "Map of tool -> true, or "
                    '{"allowed_decisions": ["approve","edit","reject"], "description": "..."}. '
                    'Example: {"email.send": true}'
                )
            ),
        ] = None,
        owners: Annotated[
            list[dict] | None,
            Field(
                description=(
                    'List of {"userId", "name", "email"} objects who own the agent.'
                )
            ),
        ] = None,
    ) -> str:
        """Change what an agent can do, or who owns it — an ENFORCED,
        platform-checked gate, not documentation. Read the current record
        first with mspbotsagent_get_agent_permissions; you cannot write this
        safely without it.
        Not this tool: threshold or intent approval rules ("any refund over
        $500 needs approval") -> mspbotsagent_upsert_agent_approval; scope
        described in prose ("it should draft but never send") ->
        mspbotsagent_set_sop_purpose; `review` / self-review quality -> the
        evaluation tool.

        WHOLE-RECORD REPLACE — THERE IS NO PARTIAL WRITE
          This call replaces permission, interrupt_on and owners together.
          A key you omit is a key you ERASE, silently and without an error:
          sending {"permission": {"read_file": "allow"}} wipes every other
          tool's setting AND wipes owners. Always: get, edit what the get
          returned, send it all back — including the parts you are not
          changing, and including owners even when it is []. There is no way
          to change one tool by itself.
          (The get returns `interruptOn`; send it back as `interrupt_on`.)

          Because the map is sparse, sending permission {} allows everything.
          That is a real setting, but it is indistinguishable from an agent
          that was never configured — to record "full autonomy" as a decision,
          write all seven built-ins as "allow" explicitly.

        HOW THE TWO ACTION KEYS RELATE
          permission     per-tool baseline. allow = call freely; deny = never
                         call; ask = pause for a human before calling.
          interrupt_on   the rich form of "ask": {tool -> true} or {tool ->
                         {"allowed_decisions": [...], "description": "..."}}.
                         Use when a paused tool should limit what the reviewer
                         may decide. "ask" and interrupt_on are two forms of
                         ONE setting, not a conflict: a full or empty decision
                         set stays permission "ask"; a restricted set lives in
                         interrupt_on. allow/deny only ever live in
                         permission. allowed_decisions: "approve" | "edit" |
                         "reject".

        THE KEYS ARE THESE EIGHT AND NOTHING ELSE
          Connector (MCP) tool ids are NOT valid keys here and are rejected —
          not "qbo.createInvoice", not "ClickUp", not "Microsoft Graph". This
          gate covers built-ins only. A connector tool has no allow/ask/deny
          here: its one control is an on/off switch, set through
          mspbotsagent_set_connector_tools, not this tool.
          execute          Run shell commands / code
          read_file        Read files in the workspace
          write_file       Create or overwrite files
          edit_file        Modify existing files
          artifact         Hand a finished file to the user
          show             Show a file without storing it
          task             Delegate to a sub-agent
          start_async_task Start a background agent and carry on
          No ninth to discover, no catalog to fetch. Identical for every
          agent, whatever the get returned.
          `download` and `upload_artifact` are accepted as deprecated aliases
          of `artifact` (older cached instructions, or a value carried over
          from a prior get) and are translated, not rejected.

        policyError=true -> permission / interrupt_on writes are refused
        (shared assistant policy); owners can still be set.
        Never update one agent from two calls at once — the second replace
        wipes the first. Returns the updated agent record as JSON.
        Legacy: agentos unions {tool: false} in a `tools` map into "deny";
        never write that form.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        if permission is None and interrupt_on is None and owners is None:
            return "Error: provide at least one of 'permission', 'interrupt_on', or 'owners'"

        # PRD-21381: normalize legacy bare tool ids (download / upload_artifact,
        # carried over from a prior get or an old cached instruction) to the
        # current `artifact` BEFORE validating, so a legacy-keyed caller is
        # translated rather than rejected.
        permission = _normalize_legacy_tool_keys(permission)
        interrupt_on = _normalize_legacy_tool_keys(interrupt_on)

        bad_ids = _invalid_bare_tool_ids(permission) + _invalid_bare_tool_ids(interrupt_on)
        if bad_ids:
            return error_envelope(
                "invalid_argument",
                f"Not a recognized built-in tool id: {', '.join(sorted(set(bad_ids)))}. "
                f"Built-ins are: {', '.join(sorted(_KNOWN_BUILTIN_TOOL_IDS))}. A connector "
                "tool id must be in \"vendor.action\" form (e.g. \"qbo.createInvoice\") — "
                "call mspbotsagent_get_connectors first to find the real key.",
                False,
            )

        body: dict = {}
        if permission is not None:
            body["permission"] = permission
        if interrupt_on is not None:
            body["interruptOn"] = interrupt_on
        if owners is not None:
            body["owners"] = owners

        # policyError only makes permission/interruptOn unreliable — guard those,
        # but let an owners-only update through.
        if "permission" in body or "interruptOn" in body:
            blocked = await _guard_policy(client, agent_id)
            if blocked:
                return blocked

        try:
            result = await client.put(f"/api/agents/{agent_id}", body)
            return dump_json(result)
        except AgentError as e:
            return e.to_envelope()

    # ----- evaluation --------------------------------------------------

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def mspbotsagent_get_agent_evaluation(
        agent_id: Annotated[str, Field(description="Agent to read.")],
    ) -> str:
        """Check whether an agent reviews its own output before finishing.

        Use for questions like "does this agent double-check its work",
        "what quality rules does it follow". Returns review = {rules,
        max_iterations}, or null if unconfigured.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            data = await _fetch_agent_data(client, agent_id)
        except AgentError as e:
            return e.to_envelope()
        return dump_json({"review": data.get("review")})

    @mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
    async def mspbotsagent_upsert_agent_evaluation(
        agent_id: Annotated[str, Field(description="Agent to update.")],
        rules: Annotated[
            list[dict],
            Field(
                description=(
                    "List of rule objects; empty list disables self-eval. Each: "
                    '{"rubric": "...", "name": "...", "description": "...", '
                    '"triggers": ["regex or keyword", ...]}. `rubric` is REQUIRED '
                    "on every rule — it is the standard the output is checked "
                    "against; a rule without it is rejected."
                )
            ),
        ],
        max_iterations: Annotated[
            int | None, Field(description="Max self-review passes, e.g. 3.")
        ] = None,
    ) -> str:
        """Turn on/off or edit an agent's self-review rules before it finishes.

        Always this tool for "self-review"/"self-check"/"double-check X
        before Y"/"verify its math first"/"stop self-review" — never
        permissions, approval, or an SOP procedure step, however it's
        phrased. A vague `rubric` is enough (e.g. "checks the math is
        correct") — never wait for more specifics. Empty rules list turns
        it off. Do not call this twice concurrently for the same agent.
        """
        missing = _rules_missing_rubric(rules)
        if missing:
            return error_envelope(
                "invalid_argument",
                f"rules[{', '.join(map(str, missing))}] missing a non-empty `rubric`. "
                "Every rule needs one — the platform discards the whole evaluation "
                "otherwise. A short one is fine (e.g. \"checks the math is correct\"); "
                "add it and call again.",
                False,
            )

        client = client_factory()
        if client is None:
            return NO_TOKEN

        blocked = await _guard_policy(client, agent_id)
        if blocked:
            return blocked

        review: dict = {"rules": rules}
        if max_iterations is not None:
            review["max_iterations"] = max_iterations
        try:
            result = await client.put(f"/api/agents/{agent_id}", {"review": review})
            return dump_json(result)
        except AgentError as e:
            return e.to_envelope()

    # ----- human-in-loop (approval) ------------------------------------

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def mspbotsagent_get_agent_approval(
        agent_id: Annotated[str, Field(description="Agent to read.")],
    ) -> str:
        """Check which of an agent's actions need a human to approve first.

        Use for questions like "does this agent need approval to send
        refunds", "what needs sign-off before this agent does it". Returns
        approval = list of rule objects (may be empty).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            data = await _fetch_agent_data(client, agent_id)
        except AgentError as e:
            return e.to_envelope()
        return dump_json({"approval": data.get("approval")})

    @mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
    async def mspbotsagent_upsert_agent_approval(
        agent_id: Annotated[str, Field(description="Agent to update.")],
        rules: Annotated[
            list[dict],
            Field(
                description=(
                    "List of approval-rule objects. Each: "
                    '{"name": "...", "intent": "...", '
                    '"triggers": ["regex or keyword", ...], "tools": [...], '
                    '"decisions": ["approve", "reject"]}.'
                )
            ),
        ],
    ) -> str:
        """Require a human's sign-off before an agent takes a sensitive action.

        Use for requests like "make a human approve any refund over $500
        before this agent sends it".

        rules FULL REPLACES the list, it does not append — read via
        mspbotsagent_get_agent_approval first, then pass the whole updated
        array back. Empty list clears all gates.

        Shares one PUT with the permissions/evaluation tools — never run
        two of the three concurrently on the same agent.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        blocked = await _guard_policy(client, agent_id)
        if blocked:
            return blocked

        try:
            result = await client.put(
                f"/api/agents/{agent_id}", {"approval": {"rules": rules}}
            )
            return dump_json(result)
        except AgentError as e:
            return e.to_envelope()
