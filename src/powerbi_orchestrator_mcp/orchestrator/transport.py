"""HTTP transport + Entra ID authentication for the MCP server.

Adds an opt-in `streamable-http` transport (MCP spec 2025-06+) on top
of the existing stdio default. Authentication uses Entra ID (Azure AD)
JWT bearer tokens validated against the tenant's JWKS endpoint.

Scope hierarchy (RBAC):

    Tools.Read    — read-only tools (connect_target, powerbi_health,
                    audit_model_and_report, audit_report_ux_and_storytelling)
    Tools.Write   — mutating tools (apply_plan, deploy_to_workspace,
                    run_refresh, create_semantic_model_from_schema, …)
    Tools.Admin   — administrative operations (audit log dump,
                    HMAC key rotation); implicitly grants all lower scopes.

The transport is opt-in via `--transport http` (or `PBI_TRANSPORT=http`).
stdio remains the default; nothing changes for existing Claude Desktop /
VS Code users.

Design reference: `specs/architecture/07-http-transport.md`.
"""

from __future__ import annotations

import argparse
import contextvars
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from starlette.types import ASGIApp, Receive, Scope, Send

_current_user_var: contextvars.ContextVar[dict[str, object] | None] = contextvars.ContextVar(
    "current_user", default=None
)


def get_current_user() -> dict[str, object] | None:
    return _current_user_var.get()


def set_current_user(claims: dict[str, object] | None) -> None:
    _current_user_var.set(claims)


# Claims that identify the calling principal, most specific first. `oid` is
# the stable object id of the user or service principal; `sub` is the
# pairwise subject; `appid`/`azp` identify the calling application.
_PRINCIPAL_CLAIM_KEYS = ("oid", "sub", "appid", "azp", "unique_name", "upn", "preferred_username")

# Claims that identify the *application* the call was made through.
_CLIENT_ID_CLAIM_KEYS = ("appid", "azp", "client_id")


def _first_claim(claims: dict[str, object], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = claims.get(key)
        if value:
            return str(value)
    return ""


def granted_scopes_from_claims(claims: dict[str, object]) -> set[str]:
    """Extract the scopes/app-roles an Entra token carries.

    Handles both shapes Entra emits: delegated tokens use the space or
    list separated `scp` claim, application tokens use the `roles`
    claim.
    """
    raw_scp = claims.get("scp", "")
    if isinstance(raw_scp, str):
        scp_list = raw_scp.split()
    elif isinstance(raw_scp, (list, tuple, set)):
        scp_list = [str(item) for item in raw_scp]
    else:
        scp_list = []
    raw_roles = claims.get("roles", [])
    if isinstance(raw_roles, (list, tuple, set)):
        roles_list = [str(item) for item in raw_roles]
    else:
        roles_list = [str(raw_roles)] if raw_roles else []
    return set(scp_list) | set(roles_list)


def build_authenticated_user(token: str, claims: dict[str, object]) -> AuthenticatedUser:
    """Wrap Entra claims in the MCP SDK's `AuthenticatedUser`.

    The SDK binds a streamable-HTTP session to the credential that opened
    it: `streamable_http_manager` reads `scope["user"]`, derives an
    `AuthorizationContext` from it and answers 404 when the caller of a
    later request does not match the principal that created the session.

    Without `scope["user"]` both sides of that comparison are `None`,
    the comparison never rejects, and any authenticated principal can
    join any other principal's session and inherit its scopes.
    """
    principal = _first_claim(claims, _PRINCIPAL_CLAIM_KEYS)
    expires_at = claims.get("exp")
    return AuthenticatedUser(
        AccessToken(
            token=token,
            client_id=_first_claim(claims, _CLIENT_ID_CLAIM_KEYS) or principal,
            scopes=sorted(granted_scopes_from_claims(claims)),
            expires_at=int(expires_at) if isinstance(expires_at, int) else None,
            subject=principal or None,
            claims=dict(claims),
        )
    )


def current_request_claims() -> dict[str, object] | None:
    """Claims of the caller of the *current* MCP request, if any.

    The MCP server dispatches tool calls from the session task, so the
    contextvar written by the HTTP middleware while handling the HTTP
    request is not visible from there. The SDK does attach the
    originating Starlette request to the message metadata, and the
    middleware stores the authenticated user in that request's ASGI
    scope — so the per-call claims are read back from there.
    """
    try:
        from mcp.server.lowlevel.server import request_ctx
    except ImportError:  # pragma: no cover - the SDK always ships it
        return None
    ctx = request_ctx.get(None)
    request = getattr(ctx, "request", None)
    scope = getattr(request, "scope", None)
    if not isinstance(scope, dict):
        return None
    user = scope.get("user")
    claims = getattr(getattr(user, "access_token", None), "claims", None)
    if isinstance(claims, dict):
        return dict(claims)
    return None


def current_caller_claims() -> dict[str, object] | None:
    """Claims of the principal driving the current tool call.

    Prefers the per-request claims (HTTP transport: the exact caller of
    this call) and falls back to the contextvar (stdio transport: the
    single local user).
    """
    claims = current_request_claims()
    if claims is not None:
        return claims
    return get_current_user()


Transport = Literal["stdio", "http"]

log = __import__("logging").getLogger(__name__)


@dataclass(frozen=True)
class HttpConfig:
    """Configuration for the HTTP transport.

    All fields are required when transport=http; the CLI parser and
    `parse_transport_args` enforce this and exit non-zero if missing.
    """

    host: str = "127.0.0.1"
    port: int = 8000
    entra_tenant_id: str = ""
    entra_audience: str = ""
    entra_required_scope: str = "Tools.Read"
    mount_path: str = "/mcp"
    allowed_hosts: tuple[str, ...] = ()
    """Extra Host header values the server answers to.

    The SDK's DNS-rebinding protection only allows loopback hosts by
    default, so a server bound to a container address would answer 421
    to every client. These values are added to the allowlist; the
    protection itself stays enabled.
    """


class AuthError(Exception):
    """Authentication failure surfaced by `validate_entra_token`.

    Attributes:
        status: HTTP status code (401 for missing/invalid credentials,
            403 for valid credentials without required scope).
        message: Human-readable description for the 401/403 body.
        www_authenticate: Value for the `WWW-Authenticate` response
            header on 401. `None` for 403 (RFC 6750 §3).
    """

    def __init__(
        self,
        status: int,
        message: str,
        www_authenticate: str | None = None,
    ) -> None:
        self.status = status
        self.message = message
        self.www_authenticate = www_authenticate
        super().__init__(message)


def _extract_bearer(authorization_header: str | None) -> str:
    """Extract a bearer token from an `Authorization` header value.

    Returns the empty string if the header is missing or malformed
    (i.e., not `Bearer <token>`). Case-insensitive scheme per RFC 6750.
    """
    if not authorization_header:
        return ""
    parts = authorization_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1]


def validate_entra_token(
    token: str,
    *,
    tenant_id: str,
    audience: str,
    required_scope: str,
    jwks_client: PyJWKClient | None = None,
) -> dict[str, object]:
    """Validate an Entra ID JWT and return its claims.

    Validates (per `specs/architecture/07-http-transport.md` §3.3):
    - signature (RS256 against JWKS)
    - `iss` matches `https://login.microsoftonline.com/{tenant_id}/v2.0`
    - `aud` matches `audience`
    - `exp`, `iat`, `nbf` time bounds (PyJWT default)
    - `scp` (delegated) or `roles` (app-role) contains `required_scope`
      OR `Tools.Admin` (admin grants all scopes implicitly)

    Raises:
        AuthError: 401 on missing/invalid/expired/wrong-audience token.
        AuthError: 403 on valid token missing the required scope.

    Returns:
        The decoded claims dict on success.
    """
    if not token:
        raise AuthError(
            401,
            "Missing bearer token",
            www_authenticate='Bearer realm="powerbi-orchestrator-mcp"',
        )

    if jwks_client is None:
        jwks_uri = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        jwks_client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600)

    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise AuthError(401, f"Token expired: {e}") from e
    except jwt.InvalidAudienceError as e:
        raise AuthError(401, f"Invalid audience: {e}") from e
    except jwt.InvalidIssuerError as e:
        raise AuthError(401, f"Invalid issuer: {e}") from e
    except PyJWTError as e:
        raise AuthError(401, f"Invalid token: {e}") from e

    raw_scp = claims.get("scp", "")
    scp_list = raw_scp.split() if isinstance(raw_scp, str) else list(raw_scp)
    raw_roles = claims.get("roles", [])
    roles_list = raw_roles if isinstance(raw_roles, list) else [str(raw_roles)]
    scopes = set(scp_list) | set(roles_list)
    if required_scope not in scopes and "Tools.Admin" not in scopes:
        raise AuthError(
            403,
            f"Token missing required scope '{required_scope}'. "
            f"Present: {sorted(scopes) or '(none)'}",
        )

    return claims


def parse_transport_args(
    argv: list[str] | None = None,
) -> tuple[Transport, HttpConfig | None]:
    """Parse CLI args for transport selection.

    Returns:
        A tuple `(transport, http_config)`. `http_config` is `None`
        when `transport == "stdio"`.

    Reads env vars as fallback for HTTP config:
    - `PBI_HTTP_HOST` (default `127.0.0.1`)
    - `PBI_HTTP_PORT` (default `8000`)
    - `PBI_ENTRA_TENANT_ID` (required for HTTP)
    - `PBI_ENTRA_AUDIENCE` (required for HTTP)
    - `PBI_ENTRA_REQUIRED_SCOPE` (default `Tools.Read`)

    Exits the process via `SystemExit` if HTTP is selected but
    tenant/audience are missing — fail loudly at startup, not on the
    first request.
    """
    parser = argparse.ArgumentParser(
        prog="powerbi-orchestrator-mcp",
        description=(
            "MCP server for Power BI / Fabric orchestration. "
            "stdio is default; pass --transport http for remote use."
        ),
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="MCP transport (default: stdio).",
    )
    parser.add_argument(
        "--http-host",
        default=None,
        help="HTTP bind host (default 127.0.0.1, set to 0.0.0.0 for containers).",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=None,
        help="HTTP bind port (default 8000).",
    )
    parser.add_argument(
        "--http-entra-tenant-id",
        default=None,
        help="Entra ID tenant ID for token validation (required for HTTP).",
    )
    parser.add_argument(
        "--http-entra-audience",
        default=None,
        help="Expected `aud` claim, e.g. api://powerbi-orchestrator-mcp (required for HTTP).",
    )
    parser.add_argument(
        "--http-entra-required-scope",
        default=None,
        help="Scope required to call tools (default Tools.Read).",
    )
    parser.add_argument(
        "--http-mount-path",
        default=None,
        help="URL path for the MCP endpoint (default /mcp).",
    )
    parser.add_argument(
        "--http-allowed-host",
        action="append",
        default=None,
        dest="http_allowed_host",
        help=(
            "Extra Host header value the server answers to. Repeatable. "
            "Needed when the server is bound to a non-loopback address: "
            "the SDK's DNS-rebinding protection otherwise rejects every "
            "request with 421."
        ),
    )
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        return "stdio", None

    tenant_id = args.http_entra_tenant_id or os.environ.get("PBI_ENTRA_TENANT_ID", "")
    audience = args.http_entra_audience or os.environ.get("PBI_ENTRA_AUDIENCE", "")

    if not tenant_id:
        sys.stderr.write(
            "error: --transport http requires --http-entra-tenant-id or "
            "PBI_ENTRA_TENANT_ID env var\n"
        )
        raise SystemExit(2)
    if not audience:
        sys.stderr.write(
            "error: --transport http requires --http-entra-audience or PBI_ENTRA_AUDIENCE env var\n"
        )
        raise SystemExit(2)

    cfg = HttpConfig(
        host=args.http_host or os.environ.get("PBI_HTTP_HOST", "127.0.0.1"),
        port=args.http_port or int(os.environ.get("PBI_HTTP_PORT", "8000")),
        entra_tenant_id=tenant_id,
        entra_audience=audience,
        entra_required_scope=args.http_entra_required_scope
        or os.environ.get("PBI_ENTRA_REQUIRED_SCOPE", "Tools.Read"),
        mount_path=args.http_mount_path or os.environ.get("PBI_HTTP_MOUNT_PATH", "/mcp"),
        allowed_hosts=_allowed_hosts(args.http_allowed_host),
    )

    return "http", cfg


def _allowed_hosts(explicit: list[str] | None) -> tuple[str, ...]:
    """Resolve the extra Host header values the server answers to."""
    hosts: list[str] = []
    if explicit:
        hosts.extend(h.strip() for h in explicit if h.strip())
    env_value = os.environ.get("PBI_HTTP_ALLOWED_HOSTS", "")
    hosts.extend(h.strip() for h in env_value.split(",") if h.strip())
    return tuple(dict.fromkeys(hosts))


def auth_middleware_factory(
    cfg: HttpConfig,
    jwks_client: PyJWKClient | None = None,
) -> Callable[[str | None], dict[str, object]]:
    """Build an HTTP middleware that validates Entra ID tokens.

    Returns a callable suitable for FastMCP's `add_middleware` (the
    exact signature depends on the `mcp` version; this returns the
    validation function plus a helper to format 401/403 responses
    that the middleware adapter can call).

    Kept as a factory so unit tests can inject a mock `jwks_client`.
    """
    client = jwks_client or PyJWKClient(
        f"https://login.microsoftonline.com/{cfg.entra_tenant_id}/discovery/v2.0/keys",
        cache_keys=True,
        lifespan=3600,
    )

    def validate_request(authorization_header: str | None) -> dict[str, object]:
        """Validate the request's bearer token; raise AuthError on failure."""
        token = _extract_bearer(authorization_header)
        return validate_entra_token(
            token,
            tenant_id=cfg.entra_tenant_id,
            audience=cfg.entra_audience,
            required_scope=cfg.entra_required_scope,
            jwks_client=client,
        )

    return validate_request


class EntraAuthMiddleware:
    """ASGI middleware that validates the Entra ID bearer token.

    Besides rejecting unauthenticated requests, it publishes the caller's
    identity in the ASGI scope under `user` so the MCP SDK can bind the
    session to that credential.
    """

    def __init__(
        self,
        app: ASGIApp,
        validate_fn: Callable[[str | None], dict[str, object]],
    ) -> None:
        self.app = app
        self.validate_fn = validate_fn

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http":
            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode("latin1")
            try:
                claims = self.validate_fn(auth_header)
                set_current_user(claims)
                scope["entra_claims"] = claims
                # Bind the session to this credential. The SDK compares the
                # caller of every later request against the principal that
                # created the session and answers 404 on a mismatch; that
                # check only works when `scope["user"]` is populated.
                scope["user"] = build_authenticated_user(_extract_bearer(auth_header), claims)
            except AuthError as exc:
                resp_headers = [(b"content-type", b"application/json")]
                if exc.www_authenticate:
                    resp_headers.append(
                        (b"www-authenticate", exc.www_authenticate.encode("latin1"))
                    )
                body = json.dumps({"error": exc.message}).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": exc.status,
                        "headers": resp_headers,
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": body,
                    }
                )
                return
        await self.app(scope, receive, send)


__all__ = [
    "AuthError",
    "EntraAuthMiddleware",
    "HttpConfig",
    "Transport",
    "auth_middleware_factory",
    "build_authenticated_user",
    "current_caller_claims",
    "current_request_claims",
    "get_current_user",
    "granted_scopes_from_claims",
    "parse_transport_args",
    "set_current_user",
    "validate_entra_token",
    "_extract_bearer",
]
