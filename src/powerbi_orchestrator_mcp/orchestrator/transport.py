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
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError

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
        jwks_uri = (
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        )
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

    scopes = claims.get("scp", "") or claims.get("roles", [])
    if isinstance(scopes, str):
        scopes = scopes.split()
    if required_scope not in scopes and "Tools.Admin" not in scopes:
        raise AuthError(
            403,
            f"Token missing required scope '{required_scope}'. "
            f"Present: {scopes or '(none)'}",
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
        help="Expected `aud` claim, e.g. api://powerbi-orchestrator-mcp "
        "(required for HTTP).",
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
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        return "stdio", None

    tenant_id = args.http_entra_tenant_id or os.environ.get(
        "PBI_ENTRA_TENANT_ID", ""
    )
    audience = args.http_entra_audience or os.environ.get(
        "PBI_ENTRA_AUDIENCE", ""
    )

    if not tenant_id:
        sys.stderr.write(
            "error: --transport http requires --http-entra-tenant-id or "
            "PBI_ENTRA_TENANT_ID env var\n"
        )
        raise SystemExit(2)
    if not audience:
        sys.stderr.write(
            "error: --transport http requires --http-entra-audience or "
            "PBI_ENTRA_AUDIENCE env var\n"
        )
        raise SystemExit(2)

    cfg = HttpConfig(
        host=args.http_host or os.environ.get("PBI_HTTP_HOST", "127.0.0.1"),
        port=args.http_port or int(os.environ.get("PBI_HTTP_PORT", "8000")),
        entra_tenant_id=tenant_id,
        entra_audience=audience,
        entra_required_scope=args.http_entra_required_scope
        or os.environ.get("PBI_ENTRA_REQUIRED_SCOPE", "Tools.Read"),
        mount_path=args.http_mount_path
        or os.environ.get("PBI_HTTP_MOUNT_PATH", "/mcp"),
    )

    return "http", cfg


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


__all__ = [
    "AuthError",
    "HttpConfig",
    "Transport",
    "auth_middleware_factory",
    "parse_transport_args",
    "validate_entra_token",
    "_extract_bearer",
]
