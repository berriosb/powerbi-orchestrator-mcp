"""Unit tests for the HTTP transport module.

Tests `_extract_bearer`, `validate_entra_token` (with a mocked JWKS
client so we don't depend on real Entra ID infrastructure), and
`parse_transport_args` for arg parsing and required-field enforcement.

Design reference: `specs/architecture/07-http-transport.md` §8
(acceptance criteria).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from jwt import PyJWKClient

from powerbi_orchestrator_mcp.orchestrator.transport import (
    AuthError,
    HttpConfig,
    _extract_bearer,
    auth_middleware_factory,
    parse_transport_args,
    validate_entra_token,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rsa_keypair() -> tuple[Any, Any]:
    """Return (private_key, public_key) as cryptography objects.

    Uses the `cryptography` library (pulled in transitively by
    `pyjwt[crypto]`) to generate an RSA key pair for tests.
    """
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def fake_jwks_client(rsa_keypair: tuple[Any, Any]) -> MagicMock:
    """A PyJWKClient mock that returns the test RSA public key.

    Mocks the only method `validate_entra_token` calls:
    `get_signing_key_from_jwt(token).key`. The returned object must
    expose `.key` with the public key in a format PyJWT can verify
    against (we pass the cryptography public key directly, which
    PyJWT's RSAAlgorithm accepts).
    """
    _, public_key = rsa_keypair

    signing_key = MagicMock()
    signing_key.key = public_key

    jwks_client = MagicMock(spec=PyJWKClient)
    jwks_client.get_signing_key_from_jwt.return_value = signing_key
    return jwks_client


def _make_token(
    private_key: Any,
    *,
    audience: str = "api://powerbi-orchestrator-mcp",
    issuer: str = "https://login.microsoftonline.com/test-tenant-id/v2.0",
    scope: str = "Tools.Read",
    expired: bool = False,
    wrong_audience: str | None = None,
    wrong_issuer: str | None = None,
) -> str:
    """Mint a test RS256 token with controllable claims."""
    import time

    import jwt

    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": wrong_issuer or issuer,
        "aud": wrong_audience or audience,
        "iat": now,
        "exp": now - 60 if expired else now + 600,
        "scp": scope,
        "oid": "00000000-0000-0000-0000-000000000001",
        "tid": "test-tenant-id",
    }
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )


# ---------------------------------------------------------------------------
# _extract_bearer
# ---------------------------------------------------------------------------


class TestExtractBearer:
    def test_valid_bearer(self) -> None:
        assert _extract_bearer("Bearer abc.def.ghi") == "abc.def.ghi"

    def test_lowercase_bearer(self) -> None:
        # RFC 6750: scheme is case-insensitive.
        assert _extract_bearer("bearer abc.def.ghi") == "abc.def.ghi"
        assert _extract_bearer("BEARER abc.def.ghi") == "abc.def.ghi"

    def test_missing_header(self) -> None:
        assert _extract_bearer(None) == ""
        assert _extract_bearer("") == ""

    def test_wrong_scheme(self) -> None:
        assert _extract_bearer("Basic abc") == ""

    def test_malformed(self) -> None:
        assert _extract_bearer("Bearer") == ""
        assert _extract_bearer("Bearer abc def") == ""


# ---------------------------------------------------------------------------
# validate_entra_token — happy path and scope checks
# ---------------------------------------------------------------------------


class TestValidateEntraToken:
    def test_valid_token_returns_claims(
        self,
        rsa_keypair: tuple[Any, Any],
        fake_jwks_client: MagicMock,
    ) -> None:
        private_key, _ = rsa_keypair
        token = _make_token(private_key, scope="Tools.Read")

        claims = validate_entra_token(
            token,
            tenant_id="test-tenant-id",
            audience="api://powerbi-orchestrator-mcp",
            required_scope="Tools.Read",
            jwks_client=fake_jwks_client,
        )
        assert claims["scp"] == "Tools.Read"
        assert claims["oid"] == "00000000-0000-0000-0000-000000000001"

    def test_admin_scope_grants_lower_scopes(
        self,
        rsa_keypair: tuple[Any, Any],
        fake_jwks_client: MagicMock,
    ) -> None:
        """Tools.Admin implicitly grants Tools.Read/Write per spec §3.4."""
        private_key, _ = rsa_keypair
        token = _make_token(private_key, scope="Tools.Admin")

        claims = validate_entra_token(
            token,
            tenant_id="test-tenant-id",
            audience="api://powerbi-orchestrator-mcp",
            required_scope="Tools.Write",
            jwks_client=fake_jwks_client,
        )
        assert claims["scp"] == "Tools.Admin"

    def test_missing_token_raises_401(self, fake_jwks_client: MagicMock) -> None:
        with pytest.raises(AuthError) as exc_info:
            validate_entra_token(
                "",
                tenant_id="test-tenant-id",
                audience="api://powerbi-orchestrator-mcp",
                required_scope="Tools.Read",
                jwks_client=fake_jwks_client,
            )
        assert exc_info.value.status == 401
        assert exc_info.value.www_authenticate is not None
        assert "Bearer" in exc_info.value.www_authenticate

    def test_wrong_audience_raises_401(
        self,
        rsa_keypair: tuple[Any, Any],
        fake_jwks_client: MagicMock,
    ) -> None:
        private_key, _ = rsa_keypair
        token = _make_token(
            private_key,
            audience="api://some-other-app",
        )

        with pytest.raises(AuthError) as exc_info:
            validate_entra_token(
                token,
                tenant_id="test-tenant-id",
                audience="api://powerbi-orchestrator-mcp",
                required_scope="Tools.Read",
                jwks_client=fake_jwks_client,
            )
        assert exc_info.value.status == 401

    def test_wrong_issuer_raises_401(
        self,
        rsa_keypair: tuple[Any, Any],
        fake_jwks_client: MagicMock,
    ) -> None:
        private_key, _ = rsa_keypair
        token = _make_token(
            private_key,
            issuer="https://login.microsoftonline.com/wrong-tenant/v2.0",
        )

        with pytest.raises(AuthError) as exc_info:
            validate_entra_token(
                token,
                tenant_id="test-tenant-id",
                audience="api://powerbi-orchestrator-mcp",
                required_scope="Tools.Read",
                jwks_client=fake_jwks_client,
            )
        assert exc_info.value.status == 401

    def test_expired_token_raises_401(
        self,
        rsa_keypair: tuple[Any, Any],
        fake_jwks_client: MagicMock,
    ) -> None:
        private_key, _ = rsa_keypair
        token = _make_token(private_key, expired=True)

        with pytest.raises(AuthError) as exc_info:
            validate_entra_token(
                token,
                tenant_id="test-tenant-id",
                audience="api://powerbi-orchestrator-mcp",
                required_scope="Tools.Read",
                jwks_client=fake_jwks_client,
            )
        assert exc_info.value.status == 401
        assert "expired" in exc_info.value.message.lower()

    def test_insufficient_scope_raises_403(
        self,
        rsa_keypair: tuple[Any, Any],
        fake_jwks_client: MagicMock,
    ) -> None:
        private_key, _ = rsa_keypair
        token = _make_token(private_key, scope="Tools.Read")

        with pytest.raises(AuthError) as exc_info:
            validate_entra_token(
                token,
                tenant_id="test-tenant-id",
                audience="api://powerbi-orchestrator-mcp",
                required_scope="Tools.Write",
                jwks_client=fake_jwks_client,
            )
        assert exc_info.value.status == 403
        assert exc_info.value.www_authenticate is None
        assert "Tools.Write" in exc_info.value.message

    def test_roles_claim_supported(
        self,
        rsa_keypair: tuple[Any, Any],
        fake_jwks_client: MagicMock,
    ) -> None:
        """App-role permissions (Entra ID `roles` claim) work as alternative to `scp`."""
        import time

        import jwt

        private_key, _ = rsa_keypair
        now = int(time.time())
        token = jwt.encode(
            {
                "iss": "https://login.microsoftonline.com/test-tenant-id/v2.0",
                "aud": "api://powerbi-orchestrator-mcp",
                "iat": now,
                "exp": now + 600,
                "roles": ["Tools.Write"],  # app role instead of scope
                "oid": "00000000-0000-0000-0000-000000000002",
                "tid": "test-tenant-id",
            },
            private_key,
            algorithm="RS256",
        )

        claims = validate_entra_token(
            token,
            tenant_id="test-tenant-id",
            audience="api://powerbi-orchestrator-mcp",
            required_scope="Tools.Write",
            jwks_client=fake_jwks_client,
        )
        roles = claims["roles"]
        assert isinstance(roles, list)
        assert "Tools.Write" in roles


# ---------------------------------------------------------------------------
# parse_transport_args
# ---------------------------------------------------------------------------


class TestParseTransportArgs:
    def test_default_is_stdio(self) -> None:
        transport, cfg = parse_transport_args([])
        assert transport == "stdio"
        assert cfg is None

    def test_explicit_stdio(self) -> None:
        transport, cfg = parse_transport_args(["--transport", "stdio"])
        assert transport == "stdio"
        assert cfg is None

    def test_http_requires_tenant_id(self) -> None:
        with pytest.raises(SystemExit):
            parse_transport_args(
                [
                    "--transport", "http",
                    "--http-entra-audience", "api://test",
                ]
            )

    def test_http_requires_audience(self) -> None:
        with pytest.raises(SystemExit):
            parse_transport_args(
                [
                    "--transport", "http",
                    "--http-entra-tenant-id", "abc-123",
                ]
            )

    def test_http_with_all_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PBI_HTTP_HOST", raising=False)
        monkeypatch.delenv("PBI_HTTP_PORT", raising=False)

        transport, cfg = parse_transport_args(
            [
                "--transport", "http",
                "--http-host", "0.0.0.0",
                "--http-port", "9001",
                "--http-entra-tenant-id", "abc-123",
                "--http-entra-audience", "api://test",
                "--http-entra-required-scope", "Tools.Write",
            ]
        )
        assert transport == "http"
        assert cfg is not None
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 9001
        assert cfg.entra_tenant_id == "abc-123"
        assert cfg.entra_audience == "api://test"
        assert cfg.entra_required_scope == "Tools.Write"

    def test_http_falls_back_to_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PBI_ENTRA_TENANT_ID", "env-tenant")
        monkeypatch.setenv("PBI_ENTRA_AUDIENCE", "api://env")
        monkeypatch.setenv("PBI_HTTP_HOST", "10.0.0.1")
        monkeypatch.setenv("PBI_HTTP_PORT", "7777")

        transport, cfg = parse_transport_args(["--transport", "http"])
        assert transport == "http"
        assert cfg is not None
        assert cfg.entra_tenant_id == "env-tenant"
        assert cfg.entra_audience == "api://env"
        assert cfg.host == "10.0.0.1"
        assert cfg.port == 7777


# ---------------------------------------------------------------------------
# auth_middleware_factory
# ---------------------------------------------------------------------------


class TestAuthMiddlewareFactory:
    def test_returns_callable_that_validates(
        self,
        rsa_keypair: tuple[Any, Any],
        fake_jwks_client: MagicMock,
    ) -> None:
        private_key, _ = rsa_keypair
        token = _make_token(private_key)
        cfg = HttpConfig(
            entra_tenant_id="test-tenant-id",
            entra_audience="api://powerbi-orchestrator-mcp",
        )

        validate = auth_middleware_factory(cfg, jwks_client=fake_jwks_client)
        claims = validate(f"Bearer {token}")
        assert claims["scp"] == "Tools.Read"

    def test_middleware_returns_401_on_missing_header(
        self,
        fake_jwks_client: MagicMock,
    ) -> None:
        cfg = HttpConfig(
            entra_tenant_id="test-tenant-id",
            entra_audience="api://powerbi-orchestrator-mcp",
        )
        validate = auth_middleware_factory(cfg, jwks_client=fake_jwks_client)

        with pytest.raises(AuthError) as exc_info:
            validate(None)
        assert exc_info.value.status == 401
