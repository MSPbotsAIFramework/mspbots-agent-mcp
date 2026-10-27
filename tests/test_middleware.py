"""Gateway credential middleware tests: missing-header 401, and header
values correctly reaching the per-request contextvar (no global-state
leakage across requests).
"""

from starlette.testclient import TestClient

from mspbots_agent_mcp.__main__ import _build_http_app
from mspbots_agent_mcp.api_client import AgentClient
from mspbots_agent_mcp.config import Settings
from mspbots_agent_mcp.server import create_mcp_server, get_client_from_context


def _make_app():
    settings = Settings()
    mcp = create_mcp_server(settings)
    return _build_http_app(mcp, settings), settings


def test_health_is_local_and_does_not_require_credentials():
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_missing_header_returns_401_with_required_headers_listed():
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["required_headers"] == ["X-MSP-Token", "X-MSP-Tenant-Id", "X-MSP-Host"]


def test_missing_single_header_still_returns_401():
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={
                "Accept": "application/json, text/event-stream",
                "X-MSP-Token": "dummy-token",
                "X-MSP-Tenant-Id": "dummy-tenant",
                # X-MSP-Host intentionally omitted
            },
        )
        assert resp.status_code == 401


def test_header_present_reaches_request_context(monkeypatch):
    # Directly exercises the middleware's contextvar plumbing without a full
    # MCP protocol round-trip: confirms the header values that arrive on the
    # request are exactly what get_client_from_context sees, and that they
    # are reset afterward (no leakage to the next request).
    import asyncio

    from mspbots_agent_mcp.server import GatewayTokenMiddleware, _gateway_creds_var

    settings = Settings()
    seen = {}

    async def fake_app(scope, receive, send):
        seen["creds"] = _gateway_creds_var.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = GatewayTokenMiddleware(fake_app, settings)

    async def run():
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [
                (b"x-msp-token", b"test-token-123"),
                (b"x-msp-tenant-id", b"tenant-abc"),
                (b"x-msp-host", b"https://agent.mspbots.ai"),
            ],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        sent = []

        async def send(message):
            sent.append(message)

        await middleware(scope, receive, send)

    asyncio.run(run())
    # PRD-21389: the 4th element is run_origin, forwarded from X-Channel-Run-Origin -- "" here
    # since this request didn't send one (see test_run_origin_header_reaches_request_context
    # below for the header actually arriving).
    assert seen["creds"] == ("test-token-123", "https://agent.mspbots.ai", "tenant-abc", "")
    # After the request completes, the contextvar must be reset — a fresh
    # get() outside any request context sees no leftover credential.
    assert _gateway_creds_var.get() is None


def test_client_factory_returns_none_without_context():
    settings = Settings()
    assert get_client_from_context(settings) is None


def test_run_origin_header_reaches_request_context_and_forwards_as_x_run_origin(monkeypatch):
    # PRD-21389: X-Channel-Run-Origin (set by mb-platform-agentos) is optional -- its absence must
    # not 401 the request (unlike the three gateway credential headers) -- and when present it
    # reaches get_client_from_context()'s AgentClient, which forwards it as X-Run-Origin.
    import asyncio

    from mspbots_agent_mcp.server import GatewayTokenMiddleware, _gateway_creds_var

    settings = Settings()
    seen = {}

    async def fake_app(scope, receive, send):
        seen["creds"] = _gateway_creds_var.get()
        seen["client_headers"] = get_client_from_context(settings)._headers()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = GatewayTokenMiddleware(fake_app, settings)

    async def run():
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [
                (b"x-msp-token", b"test-token-123"),
                (b"x-msp-tenant-id", b"tenant-abc"),
                (b"x-msp-host", b"https://agent.mspbots.ai"),
                (b"x-channel-run-origin", b"cron"),
            ],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        sent = []

        async def send(message):
            sent.append(message)

        await middleware(scope, receive, send)

    asyncio.run(run())
    assert seen["creds"] == ("test-token-123", "https://agent.mspbots.ai", "tenant-abc", "cron")
    assert seen["client_headers"]["X-Run-Origin"] == "cron"


def test_absent_run_origin_header_does_not_401_and_omits_x_run_origin(monkeypatch):
    # No X-Channel-Run-Origin at all is a normal, allowed request (e.g. a caller that predates
    # this change) -- it must still succeed, and the outgoing client must not send a bare/empty
    # X-Run-Origin header (mb-platform-agent's guard treats "missing" and "empty" identically,
    # but sending nothing is the honest signal here).
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={
                "Accept": "application/json, text/event-stream",
                "X-MSP-Token": "dummy-token",
                "X-MSP-Tenant-Id": "dummy-tenant",
                "X-MSP-Host": "https://agent.mspbots.ai",
            },
        )
        assert resp.status_code != 401

    client_obj = AgentClient("dummy-token", "https://agent.mspbots.ai", "dummy-tenant", "")
    assert "X-Run-Origin" not in client_obj._headers()
