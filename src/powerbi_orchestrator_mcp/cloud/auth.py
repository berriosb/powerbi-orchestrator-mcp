"""Azure Identity wrapper for Power BI / Fabric authentication.

Implements ``specs/02-cloud-fabric.md`` §2.1.

Three auth modes supported (per spec):
- ``interactive`` (default) — InteractiveBrowserCredential with token
  cache in ``~/.azure/`` (managed by azure-identity SDK).
- ``service_principal`` — reads ``AZURE_CLIENT_ID`` /
  ``AZURE_TENANT_ID`` / ``AZURE_CLIENT_SECRET`` from env vars.
- ``managed_identity`` — when running in Azure (VMSS, Container Apps,
  App Service). Uses ``ManagedIdentityCredential``.

For MVP we use the sync ``azure.identity`` API wrapped in ``asyncio.to_thread``
so the orchestrator's event loop is never blocked. We do NOT depend on
``azure.identity.aio`` (the async-native variants) to keep the dep
footprint smaller and consistent with the rest of the project.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

from azure.core.exceptions import ClientAuthenticationError
from azure.identity import (
    ClientSecretCredential,
    DefaultAzureCredential,
    ManagedIdentityCredential,
)

READ_SCOPES = ("https://analysis.windows.net/powerbi/api/Dataset.Read.All",)
WRITE_SCOPES = (
    "https://analysis.windows.net/powerbi/api/Dataset.Read.All",
    "https://analysis.windows.net/powerbi/api/Dataset.ReadWrite.All",
)

# Credential environment variables, most specific first. The orchestrator's
# own ``PBI_*`` names win over the generic ``AZURE_*`` ones because they are
# what this project's own documentation (DOCKER.md, docker-compose.yml)
# tells an operator to set — reading only ``AZURE_*`` would silently
# authenticate as a different identity than the one that was configured.
ENV_TENANT_ID = ("PBI_TENANT_ID", "AZURE_TENANT_ID")
ENV_CLIENT_ID = ("PBI_CLIENT_ID", "AZURE_CLIENT_ID")
ENV_CLIENT_SECRET = ("PBI_CLIENT_SECRET", "AZURE_CLIENT_SECRET")
ENV_AUTH_MODE = ("PBI_AUTH_MODE",)


def _first_env(names: tuple[str, ...]) -> str | None:
    """Return the first environment variable that is set and non-empty."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def resolve_auth_mode(mode: str | None = None) -> str:
    """Resolve the auth mode from an argument, the environment, or the default.

    ``PBI_AUTH_MODE`` is what the project's own deployment docs tell an
    operator to set; it has to be honoured, otherwise the configured
    service principal is ignored and the server authenticates as
    something else.
    """
    return mode or _first_env(ENV_AUTH_MODE) or "interactive"


class AuthModeError(ValueError):
    """Raised when an unsupported auth mode is requested."""


@dataclass(frozen=True)
class AuthConfig:
    """Resolved auth configuration.

    Attributes:
        mode: One of "interactive", "service_principal", "managed_identity".
        tenant_id: Azure tenant ID (required for service_principal).
        client_id: SPN client ID (required for service_principal).
        client_secret: SPN secret (required for service_principal).
            In production, prefer cert-based auth; we support secret for MVP.
    """

    mode: str
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)

    @classmethod
    def from_env_or_args(
        cls,
        mode: str | None = None,
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> AuthConfig:
        """Resolve auth config from explicit args, falling back to env vars.

        Precedence: explicit arguments, then the orchestrator's own
        ``PBI_*`` variables, then the ``AZURE_*`` variables the
        azure-identity SDK uses.

        Environment variables:
        - ``PBI_AUTH_MODE`` / ``AZURE_CLIENT_ID`` family:
          ``PBI_AUTH_MODE``, ``PBI_TENANT_ID`` / ``AZURE_TENANT_ID``,
          ``PBI_CLIENT_ID`` / ``AZURE_CLIENT_ID``,
          ``PBI_CLIENT_SECRET`` / ``AZURE_CLIENT_SECRET``.
        """
        resolved_mode = resolve_auth_mode(mode)
        return cls(
            mode=resolved_mode,
            tenant_id=tenant_id or _first_env(ENV_TENANT_ID),
            client_id=client_id or _first_env(ENV_CLIENT_ID),
            client_secret=client_secret or _first_env(ENV_CLIENT_SECRET),
        )

    def validate(self) -> None:
        """Raise if the config is incomplete for the chosen mode."""
        if self.mode not in {"interactive", "service_principal", "managed_identity"}:
            raise AuthModeError(
                f"unknown auth_mode {self.mode!r}; "
                f"valid: interactive | service_principal | managed_identity"
            )
        if self.mode == "service_principal":
            missing = []
            if not self.tenant_id:
                missing.append("tenant_id")
            if not self.client_id:
                missing.append("client_id")
            if not self.client_secret:
                missing.append("client_secret")
            if missing:
                raise AuthModeError(f"service_principal mode requires: {', '.join(missing)}")


# ---------------------------------------------------------------------------
# FabricCredential — async wrapper around azure-identity
# ---------------------------------------------------------------------------


class FabricCredential:
    """Async wrapper around azure-identity.

    The ``azure-identity`` SDK is sync; we use ``asyncio.to_thread`` so
    token refresh doesn't block the orchestrator's event loop. Tokens are
    cached by azure-identity itself (DefaultAzureCredential chains and
    caches); we just call get_token() per request.
    """

    def __init__(self, config: AuthConfig) -> None:
        config.validate()
        self._config = config
        self._credential: Any = self._build_credential()

    def _build_credential(self) -> Any:
        """Construct the appropriate azure-identity credential.

        Each branch returns the SDK-specific credential; we keep them
        typed as ``Any`` to avoid leaking the SDK types into the rest
        of the project.
        """
        if self._config.mode == "interactive":
            kwargs: dict[str, Any] = {"exclude_interactive_browser_credential": False}
            if self._config.tenant_id:
                kwargs["interactive_browser_tenant_id"] = self._config.tenant_id
            if self._config.client_id:
                kwargs["interactive_browser_client_id"] = self._config.client_id
            return DefaultAzureCredential(**kwargs)
        if self._config.mode == "service_principal":
            if (
                self._config.tenant_id is None
                or self._config.client_id is None
                or self._config.client_secret is None
            ):
                raise ValueError(
                    "tenant_id, client_id, and client_secret are required for service_principal mode"
                )
            return ClientSecretCredential(
                tenant_id=self._config.tenant_id,
                client_id=self._config.client_id,
                client_secret=self._config.client_secret,
            )
        # managed_identity
        return ManagedIdentityCredential()

    async def get_token(self, scopes: tuple[str, ...] = WRITE_SCOPES) -> str:
        """Return a bearer token, refreshing transparently.

        AzureIdentityCredential caches; second call within the same
        hour returns the cached token. On expiry, get_token() refreshes.
        """
        return await asyncio.to_thread(self._get_token_sync, scopes)

    def _get_token_sync(self, scopes: tuple[str, ...]) -> str:
        try:
            token = self._credential.get_token(*scopes)
        except ClientAuthenticationError as exc:
            raise AuthModeError(f"Azure authentication failed: {exc}") from exc
        return token.token  # type: ignore[no-any-return]

    async def get_token_for_scope(self, scope_kind: str) -> str:
        """Convenience: return a token for the given operation scope.

        ``scope_kind`` is "read" or "write".
        """
        if scope_kind == "read":
            return await self.get_token(READ_SCOPES)
        if scope_kind == "write":
            return await self.get_token(WRITE_SCOPES)
        raise AuthModeError(f"unknown scope_kind {scope_kind!r}")


__all__ = [
    "ENV_AUTH_MODE",
    "ENV_CLIENT_ID",
    "ENV_CLIENT_SECRET",
    "ENV_TENANT_ID",
    "AuthConfig",
    "AuthModeError",
    "FabricCredential",
    "READ_SCOPES",
    "WRITE_SCOPES",
    "resolve_auth_mode",
]
