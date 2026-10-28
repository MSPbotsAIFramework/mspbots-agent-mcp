import asyncio
from typing import Any

import httpx

from ._json import error_envelope

# The Agent Platform app is mounted at this path prefix on the tenant host:
# "https://<host>/apps/mb-platform-agent/<sub-path>".
# X-MSP-Host only carries the bare host; do not hardcode the prefix elsewhere.
# Callers pass the full sub-path below this prefix, e.g.
#   "/api/capabilities/connectors", "/api/tasks", "/api/agents/<id>".
_APP_PREFIX = "/apps/mb-platform-agent"

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_MAX_BACKOFF_SECONDS = 20.0

# One shared connection pool for the process lifetime. No credentials are
# ever stored on it — the bearer token/tenant id/host are passed per-request
# via headers, so this is safe to share across tenants/requests (see
# server.py's contextvar-based credential isolation, which is what actually
# keeps tenants apart).
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
    return _http_client


# status_code -> (error code, retryable). status_code 0 means a network/
# connection-level failure (no response at all).
_STATUS_TO_CODE: dict[int, tuple[str, bool]] = {
    0: ("upstream_error", True),
    400: ("invalid_argument", False),
    401: ("unauthorized", False),
    403: ("unauthorized", False),
    404: ("not_found", False),
    422: ("invalid_argument", False),
    429: ("rate_limited", True),
}


def _classify(status_code: int) -> tuple[str, bool]:
    if status_code in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status_code]
    if status_code >= 500:
        return "upstream_error", True
    return "invalid_argument", False


class AgentError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Agent Platform API error {status_code}: {message}")

    def to_envelope(self) -> str:
        code, retryable = _classify(self.status_code)
        return error_envelope(code, self.message, retryable)


class AgentClient:
    """Async httpx client wrapping the MSPbots Agent Platform capabilities API.

    Reuses the module-level connection pool (see _get_http_client) across
    every call made through this instance, rather than opening a new
    connection per request.

    The credential is the platform-issued API key, passed through verbatim
    as `X-API-Key` (PRD-19165 — the JWT it replaced expired on its own, so
    it could not survive being stored as a tenant credential). Since the key
    is opaque, the tenant id always travels separately as an `X_Tenant_ID`
    header, per the platform convention (the sibling ticketqa-mcp service
    relies on it for routing).
    """

    def __init__(self, access_token: str, host: str, tenant_id: str, run_origin: str = ""):
        self._token = access_token
        self._tenant_id = tenant_id
        self._base_url = host.rstrip("/") + _APP_PREFIX
        # PRD-21389: forwarded from the incoming request's X-Channel-Run-Origin header (set by
        # mb-platform-agentos on every MCP tool call it makes). Empty string when absent -- the
        # receiving gate in mb-platform-agent treats that as a non-exempt (refused) origin.
        self._run_origin = run_origin

    def _headers(self) -> dict[str, str]:
        headers = {
            "X-API-Key": self._token,
            "X_Tenant_ID": self._tenant_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._run_origin:
            headers["X-Run-Origin"] = self._run_origin
        return headers

    def _clean_params(self, params: dict | None) -> dict:
        if not params:
            return {}
        return {k: v for k, v in params.items() if v is not None}

    async def get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(
        self,
        path: str,
        json_body: Any = None,
        *,
        read_timeout: float | None = None,
        retries: int | None = None,
    ) -> Any:
        # read_timeout / retries exist for one shape of endpoint: a call that
        # runs an agent and only answers when the run finishes. Such a call
        # blows past the 30s default read timeout, and it is NOT idempotent —
        # retrying a lost response starts a SECOND run rather than recovering
        # the first. Both defaults stay in place for every other caller.
        return await self._request(
            "POST", path, json_body=json_body, read_timeout=read_timeout, retries=retries
        )

    async def put(self, path: str, json_body: Any) -> Any:
        return await self._request("PUT", path, json_body=json_body)

    async def patch(self, path: str, json_body: Any) -> Any:
        return await self._request("PATCH", path, json_body=json_body)

    async def delete(self, path: str) -> Any:
        return await self._request("DELETE", path)

    async def delete_with_body(self, path: str, json_body: Any) -> Any:
        # httpx's AsyncClient.delete() has no json/content parameter (DELETE
        # bodies are unusual), so a DELETE-with-body endpoint has to go
        # through the same generic request() path as everything else.
        return await self._request("DELETE", path, json_body=json_body)

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: Any = None,
        read_timeout: float | None = None,
        retries: int | None = None,
    ) -> Any:
        client = _get_http_client()
        url = f"{self._base_url}{path}"
        headers = self._headers()
        params = self._clean_params(params)
        timeout = _TIMEOUT if read_timeout is None else httpx.Timeout(
            connect=_TIMEOUT.connect,
            read=read_timeout,
            write=_TIMEOUT.write,
            pool=_TIMEOUT.pool,
        )
        max_retries = _MAX_RETRIES if retries is None else retries

        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                resp = await client.request(
                    method, url, headers=headers, params=params, json=json_body, timeout=timeout
                )
            except httpx.RequestError as e:
                last_exc = e
                if attempt < max_retries:
                    await asyncio.sleep(min(2**attempt, _MAX_BACKOFF_SECONDS))
                    continue
                raise AgentError(0, f"{e or type(e).__name__} (url={url})") from e

            if resp.status_code in _RETRYABLE_STATUS and attempt < max_retries:
                delay = self._retry_delay(resp, attempt)
                await asyncio.sleep(delay)
                continue

            return self._handle(resp)

        # Unreachable in practice (loop always returns or raises above), but
        # keeps type checkers happy and guards against future edits.
        if last_exc:
            raise AgentError(0, f"{last_exc}") from last_exc
        raise AgentError(0, "request failed with no response")

    def _retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _MAX_BACKOFF_SECONDS)
            except ValueError:
                pass
        return min(2**attempt, _MAX_BACKOFF_SECONDS)

    def _handle(self, resp: httpx.Response) -> Any:
        if not resp.content:
            return None
        try:
            body = resp.json()
        except ValueError:
            body = {"raw_response": resp.text}
        if resp.status_code >= 400:
            message = body.get("message") if isinstance(body, dict) else str(body)
            errors = body.get("errors") if isinstance(body, dict) else None
            if errors:
                message = f"{message} | errors={errors}"
            raise AgentError(resp.status_code, message or "unknown error")
        return body
