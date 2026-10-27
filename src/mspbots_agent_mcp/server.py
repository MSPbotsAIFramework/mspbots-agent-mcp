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

    Reads X-MSP-Token, X-MSP-Tenant-Id, and X-MSP-Host (all required) from
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
                        "This server requires the X-MSP-Token header (Agent Platform "
                        "bearer access credential), the X-MSP-Tenant-Id header, and "
                        "the X-MSP-Host header (Agent API host)"
                    ),
                    "required_headers": ["X-MSP-Token", "X-MSP-Tenant-Id", "X-MSP-Host"],
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
            "MSPbots Agent Platform lets tenants build and operate AI agents — this "
            "server exposes that platform's own config API (an internal MSPbots "
            "product, not a third-party integration). Core concepts: an agent is a "
            "configured LLM worker with tool permissions and an optional written SOP; "
            "connectors are the integrations (e.g. ConnectWise) an agent can call; "
            "skills are packaged capabilities (SKILL.md-based) at mspbots/org/agent "
            "scope; triggers schedule or event-fire an agent to run automatically. "
            "Tool groups: mspbotsagent_get_connectors lists the tenant's connector "
            "inventory; mspbotsagent_*_trigger* manage scheduled/event triggers; "
            "mspbotsagent_*_agent_permissions/evaluation/approval manage an agent's "
            "runtime policy (allowed tools, self-review rules, human-approval gates); "
            "mspbotsagent_*_sop_* manage an agent's SOP draft (name/source/purpose/"
            "data sources/procedure/section visibility); mspbotsagent_clear_sop_section "
            "permanently deletes a whole module's underlying data (destructive, unlike "
            "the tools above); mspbotsagent_*_agent_twilio_tenant_config manage the "
            "tenant-editable slice of an agent's Twilio phone channel (greeting, "
            "language, transfer/idle behavior, tts) — system-level Twilio settings "
            "(credentials, recording, prompts) are out of scope there; "
            "mspbotsagent_*_agent_skill manage an agent's "
            "private skills. Typical flow: check connectors/skills, then configure "
            "an agent's policy or SOP, then wire up triggers. Credentials come only "
            "from request headers, never tool arguments."
        ),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    client_factory: Callable[[], AgentClient | None] = lambda: get_client_from_context(settings)

    from .tools import agents, connectors, skills, sop_author, triggers, twilio_tenant_config

    connectors.register(mcp, client_factory)
    triggers.register(mcp, client_factory)
    agents.register(mcp, client_factory)
    sop_author.register(mcp, client_factory)
    twilio_tenant_config.register(mcp, client_factory)
    skills.register(mcp, client_factory)

    return mcp
