"""Regression tests for HTTP session identity binding.

The MCP streamable-HTTP transport binds a session to the credential that
opened it: `streamable_http_manager` reads `scope["user"]`, derives an
`AuthorizationContext` from it and answers 404 when the caller of a later
request does not match the principal that created the session.

The orchestrator's middleware used to publish the Entra claims under
`scope["entra_claims"]`, a key the SDK never reads. Both sides of the
comparison were therefore `None`, the comparison never rejected, and any
authenticated principal could join another principal's session and
inherit its scopes.

These tests pin the behaviour of the fixed middleware: the session is
bound to the caller, and the per-tool authorization is evaluated against
the claims of the call being served rather than the claims of whoever
opened the session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from starlette.types import Message, Receive, Scope, Send

from powerbi_orchestrator_mcp.orchestrator.authorization import (
    ToolAuthorizationError,
    authorize_tool,
    current_identity,
)
from powerbi_orchestrator_mcp.orchestrator.transport import (
    EntraAuthMiddleware,
    build_authenticated_user,
    current_caller_claims,
    current_request_claims,
    granted_scopes_from_claims,
)

if TYPE_CHECKING:
    from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Token -> claims map used by the fake validator
# ---------------------------------------------------------------------------

TOKEN_A_WRITE: dict[str, object] = {
    "oid": "user-a",
    "sub": "user-a",
    "appid": "app-1",
    "iss": "https://login.microsoftonline.com/tenant/v2.0",
    "scp": "Tools.Read Tools.Write",
    "exp": 4_000_000_000,
}

TOKEN_B_READ: dict[str, object] = {
    "oid": "user-b",
    "sub": "user-b",
    "appid": "app-1",
    "iss": "https://login.microsoftonline.com/tenant/v2.0",
    "scp": "Tools.Read",
    "exp": 4_000_000_000,
}

TOKEN_MAP: dict[str, dict[str, object]] = {
    "token-a": TOKEN_A_WRITE,
    "token-b": TOKEN_B_READ,
}


def _validate(header: str | None) -> dict[str, object]:
    from powerbi_orchestrator_mcp.orchestrator.transport import AuthError, _extract_bearer

    token = _extract_bearer(header)
    claims = TOKEN_MAP.get(token)
    if claims is None:
        raise AuthError(401, "Missing bearer token", www_authenticate='Bearer realm="test"')
    return claims


def _build_client() -> TestClient:
    """Build a FastMCP app wrapped in the orchestrator's auth middleware."""
    from mcp.server.fastmcp import FastMCP

    # Imported lazily: the SDK's test client emits a deprecation notice on
    # import that is not actionable from this project's dependency set.
    from starlette.testclient import TestClient

    app = FastMCP("identity-test")

    @app.tool()
    def whoami() -> str:
        """Return the identity the tool body observes."""
        identity = current_identity()
        return identity or "anonymous"

    starlette_app = app.streamable_http_app()
    # The SDK's DNS-rebinding protection only allows loopback hosts *with a
    # port* (the allowlist entries are `127.0.0.1:*` style patterns), so
    # the test client must address the server as 127.0.0.1:8000.
    return TestClient(
        EntraAuthMiddleware(starlette_app, _validate), base_url="http://127.0.0.1:8000"
    )


def _post(client: TestClient, body: dict[str, Any], token: str, session_id: str | None = None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    return client.post("/mcp", json=body, headers=headers)


def _initialize(client: TestClient, token: str) -> str:
    response = _post(
        client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "identity-test", "version": "1.0"},
            },
        },
        token,
    )
    assert response.status_code == 200, response.text
    session_id = response.headers.get("mcp-session-id")
    assert session_id, "expected an mcp-session-id header"
    return session_id


# ---------------------------------------------------------------------------
# Session ownership
# ---------------------------------------------------------------------------


class TestSessionOwnership:
    def test_middleware_populates_scope_user(self) -> None:
        """The middleware must publish an SDK-readable authenticated user."""
        scope: Scope = {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": [(b"authorization", b"Bearer token-a")],
        }

        async def inner_app(scope: Scope, receive: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"OK"})

        async def fake_receive() -> Message:
            return {"type": "http.request"}

        async def fake_send(message: Message) -> None:
            return None

        middleware = EntraAuthMiddleware(inner_app, _validate)
        import anyio

        anyio.run(middleware, scope, fake_receive, fake_send)  # type: ignore[arg-type]

        user = scope.get("user")
        assert user is not None, "scope['user'] must be set for SDK session binding"
        assert user.access_token.subject == "user-a"
        assert user.access_token.client_id == "app-1"
        assert "Tools.Write" in user.access_token.scopes

    def test_other_principal_cannot_join_an_existing_session(self) -> None:
        """A second principal must not be able to reuse someone's session."""
        with _build_client() as client:
            session_id = _initialize(client, "token-a")

            # Same credential: allowed.
            ok = _post(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "whoami", "arguments": {}},
                },
                "token-a",
                session_id,
            )
            assert ok.status_code == 200, ok.text

            # Different credential on the same session: rejected.
            hijack = _post(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "whoami", "arguments": {}},
                },
                "token-b",
                session_id,
            )
            assert hijack.status_code == 404, (
                f"expected 404 for a foreign credential, got {hijack.status_code}: {hijack.text}"
            )

    def test_tool_body_sees_the_caller_of_the_current_request(self) -> None:
        """The tool body must observe the caller, not the session opener."""
        with _build_client() as client:
            session_id = _initialize(client, "token-a")
            response = _post(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "whoami", "arguments": {}},
                },
                "token-a",
                session_id,
            )
            assert response.status_code == 200, response.text
            assert "user-a" in response.text


# ---------------------------------------------------------------------------
# Per-call claim resolution
# ---------------------------------------------------------------------------


class TestClaimResolution:
    def test_build_authenticated_user_carries_claims(self) -> None:
        user = build_authenticated_user("token-a", TOKEN_A_WRITE)
        assert user.access_token.claims == TOKEN_A_WRITE
        assert user.access_token.subject == "user-a"
        assert user.access_token.expires_at == 4_000_000_000

    def test_granted_scopes_handles_delegated_and_app_tokens(self) -> None:
        assert granted_scopes_from_claims({"scp": "Tools.Read Tools.Write"}) == {
            "Tools.Read",
            "Tools.Write",
        }
        assert granted_scopes_from_claims({"roles": ["Tools.Admin"]}) == {"Tools.Admin"}
        assert granted_scopes_from_claims({"scp": ["Tools.Read"], "roles": ["Tools.Write"]}) == {
            "Tools.Read",
            "Tools.Write",
        }
        assert granted_scopes_from_claims({}) == set()

    def test_current_request_claims_without_a_request(self) -> None:
        """Outside a request there is no per-call identity to report."""
        assert current_request_claims() is None

    def test_current_caller_claims_falls_back_to_contextvar(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user(TOKEN_A_WRITE)
        try:
            assert current_caller_claims() == TOKEN_A_WRITE
        finally:
            set_current_user(None)

    def test_authorization_uses_the_caller_scopes(self) -> None:
        """A read-only caller must not be able to invoke a write tool."""
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user(TOKEN_B_READ)
        try:
            with pytest.raises(ToolAuthorizationError):
                authorize_tool("apply_plan")
            # Read tools stay available.
            authorize_tool("powerbi_health")
        finally:
            set_current_user(None)

    def test_current_identity_uses_the_caller_claims(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user(TOKEN_A_WRITE)
        try:
            assert current_identity() == "user-a"
        finally:
            set_current_user(None)
