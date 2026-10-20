"""Tests for cloud.auth — Azure Identity wrapper."""

from __future__ import annotations

import pytest

from powerbi_orchestrator_mcp.cloud.auth import (
    AuthConfig,
    AuthModeError,
    FabricCredential,
)


class TestAuthConfig:
    def test_from_env_with_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_TENANT_ID", "tenant-from-env")
        monkeypatch.setenv("AZURE_CLIENT_ID", "client-from-env")
        monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret-from-env")
        cfg = AuthConfig.from_env_or_args(mode="service_principal")
        assert cfg.tenant_id == "tenant-from-env"
        assert cfg.client_id == "client-from-env"
        assert cfg.client_secret == "secret-from-env"

    def test_from_env_with_explicit_args_wins(self) -> None:
        cfg = AuthConfig.from_env_or_args(
            mode="service_principal",
            tenant_id="explicit",
            client_id="explicit-c",
            client_secret="explicit-s",
        )
        assert cfg.tenant_id == "explicit"

    def test_reads_the_documented_pbi_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The variables the deployment docs tell operators to set must work."""
        monkeypatch.setenv("PBI_TENANT_ID", "pbi-tenant")
        monkeypatch.setenv("PBI_CLIENT_ID", "pbi-client")
        monkeypatch.setenv("PBI_CLIENT_SECRET", "pbi-secret")
        monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
        monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
        monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)

        cfg = AuthConfig.from_env_or_args(mode="service_principal")

        assert cfg.tenant_id == "pbi-tenant"
        assert cfg.client_id == "pbi-client"
        assert cfg.client_secret == "pbi-secret"

    def test_pbi_env_vars_win_over_azure_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A configured PBI_* identity must not be overridden by AZURE_*."""
        monkeypatch.setenv("PBI_CLIENT_ID", "pbi-client")
        monkeypatch.setenv("AZURE_CLIENT_ID", "azure-client")

        cfg = AuthConfig.from_env_or_args(mode="service_principal")

        assert cfg.client_id == "pbi-client"

    def test_azure_env_vars_still_work(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PBI_CLIENT_ID", raising=False)
        monkeypatch.setenv("AZURE_CLIENT_ID", "azure-client")

        cfg = AuthConfig.from_env_or_args(mode="service_principal")

        assert cfg.client_id == "azure-client"

    def test_auth_mode_comes_from_pbi_auth_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PBI_AUTH_MODE", "service_principal")

        cfg = AuthConfig.from_env_or_args()

        assert cfg.mode == "service_principal"

    def test_explicit_mode_wins_over_pbi_auth_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PBI_AUTH_MODE", "service_principal")

        cfg = AuthConfig.from_env_or_args(mode="interactive")

        assert cfg.mode == "interactive"

    def test_default_mode_is_interactive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PBI_AUTH_MODE", raising=False)

        assert AuthConfig.from_env_or_args().mode == "interactive"

    def test_interactive_mode_validates(self) -> None:
        cfg = AuthConfig(mode="interactive")
        cfg.validate()  # does not raise

    def test_service_principal_requires_tenant(self) -> None:
        cfg = AuthConfig(mode="service_principal", client_id="c", client_secret="s")
        with pytest.raises(AuthModeError, match="tenant_id"):
            cfg.validate()

    def test_service_principal_requires_client_id(self) -> None:
        cfg = AuthConfig(mode="service_principal", tenant_id="t", client_secret="s")
        with pytest.raises(AuthModeError, match="client_id"):
            cfg.validate()

    def test_service_principal_requires_client_secret(self) -> None:
        cfg = AuthConfig(mode="service_principal", tenant_id="t", client_id="c")
        with pytest.raises(AuthModeError, match="client_secret"):
            cfg.validate()

    def test_unknown_mode_raises(self) -> None:
        cfg = AuthConfig(mode="magic")
        with pytest.raises(AuthModeError, match="unknown auth_mode"):
            cfg.validate()


class TestFabricCredential:
    def test_interactive_builds_default_credential(self) -> None:
        cfg = AuthConfig(mode="interactive")
        cred = FabricCredential(cfg)
        assert cred._credential is not None

    def test_service_principal_builds_client_secret_credential(self) -> None:
        cfg = AuthConfig(
            mode="service_principal",
            tenant_id="t",
            client_id="c",
            client_secret="s",
        )
        cred = FabricCredential(cfg)
        assert cred._credential is not None

    def test_managed_identity_builds_managed_identity_credential(self) -> None:
        cfg = AuthConfig(mode="managed_identity")
        cred = FabricCredential(cfg)
        assert cred._credential is not None

    def test_get_token_for_scope_unknown_raises(self) -> None:
        cfg = AuthConfig(mode="interactive")
        cred = FabricCredential(cfg)
        import asyncio

        with pytest.raises(AuthModeError, match="unknown scope_kind"):
            asyncio.run(cred.get_token_for_scope("admin"))

    def test_auth_config_masks_client_secret_in_repr(self) -> None:
        cfg = AuthConfig(
            mode="service_principal",
            tenant_id="t",
            client_id="c",
            client_secret="sensitive_key_12345",
        )
        assert "sensitive_key_12345" not in repr(cfg)

    def test_interactive_includes_browser_credential(self) -> None:
        cfg = AuthConfig(mode="interactive", tenant_id="tid-1", client_id="cid-1")
        cred = FabricCredential(cfg)
        cred_types = [type(c).__name__ for c in cred._credential.credentials]
        assert "InteractiveBrowserCredential" in cred_types
