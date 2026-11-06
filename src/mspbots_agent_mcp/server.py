import contextvars
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .api_client import AgentClient
from .config import Settings

# Per-request credential isolation via contextvars.
# GatewayTokenMiddleware sets this before the MCP handler runs.
# Python asyncio copies context per task, so concurrent SSE connections are isolated.
# Value is (access_token, host, tenant_id, run_origin). tenant_id is forwarded to the
# downstream Agent API as an X_Tenant_ID header. run_origin (PRD-21389) is forwarded as
# X-Run-Origin -- empty string when the caller sent no X-Channel-Run-Origin header, which
# mb-platform-agent's self-configuration gate treats as non-exempt (fail-closed), not as
# "no opinion".
_gateway_creds_var: contextvars.ContextVar[tuple[str, str, str, str] | None] = contextvars.ContextVar(
    "mspbots_agent_gateway_creds", default=None
)


def get_client_from_context(settings: Settings) -> AgentClient | None:
    """Resolve the active AgentClient for the current request context."""
    creds = _gateway_creds_var.get()
    if not creds:
        return None
    token, host, tenant_id, run_origin = creds
    return AgentClient(token, host, tenant_id, run_origin)


class GatewayTokenMiddleware:
    """ASGI middleware.

    Reads X-API-Key, X-MSP-Tenant-Id, and X-MSP-Host (all required) from
    request headers and stores them in the contextvar. Returns 401 if any is
    missing on /mcp requests.
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        token = request.headers.get("x-api-key")
        if not token:
            # NOTE(transition, 2026-09-23): X-API-Key replaced X-MSP-Token as the
            # credential header name. Credential rows written before the rename
            # still hold the old key and the gateway injects whatever is stored,
            # so keep honouring it until every tenant's credential has been
            # re-saved under X-API-Key. Remove this fallback — and
            # test_legacy_token_header_is_still_accepted — once that is done.
            token = request.headers.get("x-msp-token")
        tenant_id = request.headers.get("x-msp-tenant-id")
        host = request.headers.get("x-msp-host")
        # PRD-21389: optional -- absent simply forwards as "", which the receiving gate in
        # mb-platform-agent treats as non-exempt. Unlike the three headers above, a caller that
        # predates this change (or an MCP client that is not mspbots-agentos) must not be 401'd.
        run_origin = request.headers.get("x-channel-run-origin") or ""
        if not token or not tenant_id or not host:
            response = JSONResponse(
                {
                    "error": "Missing credentials",
                    "message": (
                        "This server requires the X-API-Key header (Agent Platform "
                        "bearer access credential), the X-MSP-Tenant-Id header, and "
                        "the X-MSP-Host header (Agent API host)"
                    ),
                    "required_headers": ["X-API-Key", "X-MSP-Tenant-Id", "X-MSP-Host"],
                    "optional_headers": [],
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return

        ctx_token = _gateway_creds_var.set((token, host, tenant_id, run_origin))
        try:
            await self.app(scope, receive, send)
        finally:
            _gateway_creds_var.reset(ctx_token)


def create_mcp_server(settings: Settings) -> FastMCP:
    """Build the FastMCP server instance and register all Agent tools."""
    # DNS-rebinding protection is a browser-oriented safeguard that rejects
    # non-localhost Host headers with 421. Disable it so the server works
    # correctly behind a reverse proxy or docker network.
    mcp = FastMCP(
        name="mspbots-agent-mcp",
        instructions=(
            "MSPbots Agent Platform lets tenants build and operate AI agents — this server "
            "exposes that platform's own config API (internal, not a third-party integration). "
            "Concepts: an agent is an LLM worker with tool permissions and an optional SOP; "
            "connectors are integrations (e.g. ConnectWise) an agent can call; triggers fire an "
            "agent automatically, on a schedule or an event. Tool groups: mspbotsagent_get_connectors "
            "lists the tenant-wide connector inventory, mspbotsagent_get_sop_data_sources the "
            "connectors one agent uses, and mspbotsagent_list/set_connector_tools read/flip a "
            "connector's per-tool on/off switches for one agent; mspbotsagent_*_trigger* manage "
            "scheduled/event triggers; "
            "mspbotsagent_*_agent_permissions/evaluation/approval manage an agent's runtime "
            "policy (allowed tools, self-review, approval gates); mspbotsagent_*_sop_* manage an "
            "agent's SOP draft (name/source/purpose/data sources/visibility); "
            "mspbotsagent_clear_sop_section destructively deletes a module's data; "
            "mspbotsagent_list_sops browses the tenant's SOP library; "
            "mspbotsagent_*_agent_twilio_tenant_config manage the tenant-editable slice of an "
            "agent's Twilio phone channel (greeting, language, transfer/idle, tts), not system "
            "settings (credentials, prompts). Typical flow: check connectors, then policy or SOP, "
            "then triggers. Credentials come only from request headers, never tool arguments."
            # NOTE: the agent-skill tool group (mspbotsagent_*_agent_skill) is temporarily
            # disabled — see the commented-out skills.register() call below. Restore the
            # sentence "mspbotsagent_*_agent_skill manage an agent's private skills." here
            # when re-enabling.
            # NOTE: mspbotsagent_chat_with_sop is temporarily hidden (see
            # tools/sops.py) — restore the sentence "mspbotsagent_chat_with_sop
            # holds one blocking turn with a SOP's own agent;" after
            # "...browses the tenant's SOP library" above when re-enabling.
            # mspbotsagent_run_trigger is also temporarily hidden (see
            # tools/triggers.py); the wildcard "mspbotsagent_*_trigger*" mention
            # needs no change either way.
        ),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        stateless_http=True,
        json_response=True,
    )

    client_factory: Callable[[], AgentClient | None] = lambda: get_client_from_context(settings)

    from .tools import (
        agents,
        connectors,
        # skills,  # temporarily not exposed externally
        sop_author,
        sops,
        triggers,
        twilio_tenant_config,
        usage,
    )

    connectors.register(mcp, client_factory)
    triggers.register(mcp, client_factory)
    agents.register(mcp, client_factory)
    sop_author.register(mcp, client_factory)
    sops.register(mcp, client_factory)
    twilio_tenant_config.register(mcp, client_factory)
    # Agent-skill tools (mspbotsagent_list/create/update/delete/set_agent_skill*) are
    # temporarily hidden from external clients. The implementation stays in
    # tools/skills.py; uncomment this line and the `skills` import above to re-expose it.
    # skills.register(mcp, client_factory)
    usage.register(mcp, client_factory)

    return mcp
