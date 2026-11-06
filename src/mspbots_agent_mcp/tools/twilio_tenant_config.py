from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json
from ..api_client import AgentClient, AgentError
from ._common import NO_TOKEN

# GET/PATCH /api/agents/:id/twilio/tenant-config — the tenant-customizable
# SLICE of an agent's Twilio channel config (19 keys: greeting, language,
# idle/transfer behavior, tts, intake business-flow text). This is
# deliberately narrower than the full twilio block PUT /api/agents/:id
# accepts: system-level keys (credentials, recording, identity_* prompts,
# intake system prompt, speech model, event callbacks) are out of scope on
# this path — sending one is rejected, not silently ignored, and this
# endpoint never reveals or accepts a credential in the clear.
#
# PATCH is per-key and three-state, independently for each of the 19 keys:
# a key absent from the call leaves it untouched; null (or "" / [] for the
# collection-typed keys) clears it back to the channel default; a value
# writes it after server-side validation. A Python-level default of None
# can't distinguish "absent" from "explicit null" (both parse to None), so
# every optional field here defaults to the _UNSET sentinel instead —
# confirmed against a live FastMCP instance that this produces a clean
# optional JSON-schema entry (pydantic just omits the unserializable
# default rather than erroring) and that omitted/null/value all reach the
# tool body as distinguishable states.
_UNSET: Any = object()

_PATH = "/api/agents/{}/twilio/tenant-config"


def register(mcp: FastMCP, client_factory: Callable[[], AgentClient | None]) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def mspbotsagent_get_agent_twilio_tenant_config(
        agent_id: Annotated[str, Field(description="Agent to read.")],
    ) -> str:
        """Read the tenant-customizable slice of an agent's Twilio channel config.

        Returns {configured, enabled, tenant, unset}: `tenant` has only the
        19 keys this endpoint governs, only the ones actually set — feed it
        straight into mspbotsagent_set_agent_twilio_tenant_config to
        round-trip. `unset` lists tenant keys still on the channel default.
        System-level keys (credentials, recording, prompts) never appear
        here.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(_PATH.format(agent_id))
            return dump_json(result)
        except AgentError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
    async def mspbotsagent_set_agent_twilio_tenant_config(
        agent_id: Annotated[str, Field(description="Agent to update.")],
        enabled: Annotated[
            bool | None, Field(description="Turn the Twilio channel on/off. No clear state — omit to leave unchanged.")
        ] = _UNSET,
        allow_from: Annotated[
            list[str] | str | None,
            Field(description="Allowed caller numbers (E.164). Null/empty = unrestricted."),
        ] = _UNSET,
        welcome_greeting: Annotated[str | None, Field(description="Opening line. Null/empty = channel default.")] = _UNSET,
        language: Annotated[str | None, Field(description="e.g. \"en-US\". Null/empty = channel default.")] = _UNSET,
        intake_playbook: Annotated[
            str | None,
            Field(
                description=(
                    "Tenant business-flow text — this is PART B of the stored intake prompt only; "
                    "the system prompt (PART A) is untouched. Do NOT pass a full intake_prompt here, "
                    "it will be rejected. Null/empty clears PART B, leaving only PART A."
                )
            ),
        ] = _UNSET,
        intake_fail_hint: Annotated[str | None, Field(description="Said when intake can't proceed. Null/empty = channel default.")] = _UNSET,
        transfer_groups: Annotated[
            list[dict] | None,
            Field(
                description=(
                    'Transfer-to-human routing groups, or null/[] to disable transfer entirely. Each: '
                    '{"label": str (unique), "timeout"?: 5-60, "members": [{"name": str (unique in group), '
                    '"phone": E.164 str, "email"?: str, "timeout"?: 5-60}], min 1 member}. label/name are '
                    "referenced by handoff-agent skills — renaming breaks their routing."
                )
            ),
        ] = _UNSET,
        transfer_greeting: Annotated[str | None, Field(description="Said when starting a transfer. Null/empty = channel default.")] = _UNSET,
        transfer_fail_hint: Annotated[str | None, Field(description="Said when transfer fails. Null/empty = channel default.")] = _UNSET,
        transfer_screen: Annotated[
            bool | None, Field(description="Require a keypress before connecting a transferred call. false clears it (off).")
        ] = _UNSET,
        transfer_screen_prompt: Annotated[str | None, Field(description="The keypress prompt. Null/empty = channel default.")] = _UNSET,
        transfer_screen_timeout: Annotated[
            int | None, Field(description="Seconds to wait for the keypress, 3-20. Null = default (8).")
        ] = _UNSET,
        idle_seconds: Annotated[
            int | None, Field(description="Silence before a re-prompt, 5-120 seconds. Null = default (15).")
        ] = _UNSET,
        idle_max_prompts: Annotated[
            int | None,
            Field(description="Re-prompt attempts before giving up, 0-5. 0 hangs up on first silence — it's a real setting, not \"unset\". Null = default (1)."),
        ] = _UNSET,
        idle_prompt: Annotated[
            list[str] | str | None, Field(description="Re-prompt line(s). Null/empty = channel default (\"Are you still there?\").")
        ] = _UNSET,
        idle_farewell: Annotated[str | None, Field(description="Said when giving up after idle_max_prompts. Null/empty = channel default.")] = _UNSET,
        tts_provider: Annotated[
            str | None,
            Field(
                description=(
                    '"ElevenLabs", "Google", or "Amazon" (case-insensitive). Null = default '
                    "(ElevenLabs). Must be ElevenLabs to use the ElevenLabs voice ids listed "
                    "under `voice`'s description."
                )
            ),
        ] = _UNSET,
        voice: Annotated[
            str | None,
            Field(
                description=(
                    "Conversational voice — what the AI speaks throughout the call. NOT free "
                    'text: must be a real Twilio catalog id, or the call gets disconnected by '
                    "ConversationRelay. ElevenLabs: a voice id, e.g. \"UgBBYS2sOqTuMpoF3BR0\" "
                    '(optionally with a model/tuning suffix like "-flash_v2_5"). Google/Amazon: '
                    'a catalog id with NO "Google."/"Polly." prefix, e.g. "en-US-Wavenet-D". If '
                    "the caller only names a gender/tone (\"a female voice\", \"more "
                    "authoritative\") with no exact id: do NOT invent one and do NOT pass the "
                    "description itself as the value — both disconnect the call. Instead set "
                    "tts_provider=ElevenLabs and use a whole matching row (voice AND say_voice "
                    "together, never just one) from this en-US preset table — male-young-"
                    "friendly (default): voice=UgBBYS2sOqTuMpoF3BR0, "
                    "say_voice=Polly.Matthew-Generative; male-customer-support: "
                    "voice=7EzWGsX10sAS4c9m9cPf, say_voice=Polly.Stephen-Generative; "
                    "male-authoritative: voice=sB7vwSCyX0tQmU24cW2C, "
                    "say_voice=Polly.Stephen-Generative; female-customer-support (default): "
                    "voice=DXFkLCBUTmvXpp2QwZjA, say_voice=Polly.Joanna-Generative; "
                    "female-young-friendly: voice=kdmDKE6EkgrWrrykO9Qt, "
                    "say_voice=Polly.Joanna-Generative; female-confident: "
                    "voice=g6xIsTj2HwM6VR4iXFCw, say_voice=Polly.Danielle-Generative. No exact "
                    "match for the requested tone? Use the closest row of the same gender, "
                    "never a made-up value. Null/empty = provider+language default (English "
                    "defaults are all male)."
                )
            ),
        ] = _UNSET,
        say_voice: Annotated[
            str | None,
            Field(
                description=(
                    "System-message voice (transfer prompts, farewell), spoken via <Say> — "
                    'format MUST include the provider prefix, e.g. "Polly.Matthew-Generative" '
                    'or "Google.X" (unlike `voice`, which has NO prefix). Pair it with `voice` '
                    "from the same preset row (see voice's description) — sending only one of "
                    "the two makes the call sound like two different people. Null/empty = the "
                    "tenant Twilio account's own default voice, independent of voice's default "
                    "(usually a mismatch, not a safe fallback)."
                )
            ),
        ] = _UNSET,
    ) -> str:
        """Set/clear tenant-level Twilio channel settings for an agent, one key at a time.

        Three states per key: omit it to leave that setting untouched; null
        (or "" / [] for list-typed keys) clears it back to the channel
        default; a value writes it after validation. Only pass the keys you
        want to change. `enabled: false` PAUSES the channel without losing
        any config or credentials — for a full, irreversible delete
        (including stored credentials) use mspbotsagent_clear_sop_section
        (section="twilio") instead; the two are not interchangeable.
        `voice`/`say_voice` are NOT free text — an invented or mismatched
        value disconnects the call; see their own descriptions for the
        required preset table. Fails the whole call (writes nothing) if any provided key is invalid or
        is a system-level key (credentials, recording, identity_*,
        speech/intake model, intake_prompt — use intake_playbook instead)
        — the server names which key and why. Returns {created, twilio}:
        `created: true` means this was the agent's first Twilio config and
        system defaults were seeded alongside your keys; `twilio` is the
        full saved view (secrets masked as has_*). Do not call this twice
        concurrently for the same agent.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        raw: dict[str, Any] = {
            "enabled": enabled,
            "allow_from": allow_from,
            "welcome_greeting": welcome_greeting,
            "language": language,
            "intake_playbook": intake_playbook,
            "intake_fail_hint": intake_fail_hint,
            "transfer_groups": transfer_groups,
            "transfer_greeting": transfer_greeting,
            "transfer_fail_hint": transfer_fail_hint,
            "transfer_screen": transfer_screen,
            "transfer_screen_prompt": transfer_screen_prompt,
            "transfer_screen_timeout": transfer_screen_timeout,
            "idle_seconds": idle_seconds,
            "idle_max_prompts": idle_max_prompts,
            "idle_prompt": idle_prompt,
            "idle_farewell": idle_farewell,
            "tts_provider": tts_provider,
            "voice": voice,
            "say_voice": say_voice,
        }
        body = {k: v for k, v in raw.items() if v is not _UNSET}
        if not body:
            return (
                "Error: provide at least one field to change "
                "(enabled/allow_from/welcome_greeting/language/intake_playbook/intake_fail_hint/"
                "transfer_groups/transfer_greeting/transfer_fail_hint/transfer_screen/"
                "transfer_screen_prompt/transfer_screen_timeout/idle_seconds/idle_max_prompts/"
                "idle_prompt/idle_farewell/tts_provider/voice/say_voice)"
            )
        try:
            result = await client.patch(_PATH.format(agent_id), body)
            return dump_json(result)
        except AgentError as e:
            return e.to_envelope()
