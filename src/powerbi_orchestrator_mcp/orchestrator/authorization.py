"""Per-tool authorization for the HTTP transport.

The Entra ID middleware validates the bearer token once per request, but a
single global scope does not express the difference between reading a model
and publishing to a production workspace. This module maps every tool to the
scope it needs and enforces that mapping before the tool body runs.

Enforcement is skipped when no identity is bound to the context: over the
stdio transport the MCP client already has full local access, so the process
itself is the trust boundary (see ``SECURITY.md`` threat model).
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

SCOPE_READ = "Tools.Read"
SCOPE_WRITE = "Tools.Write"
SCOPE_ADMIN = "Tools.Admin"

LOCAL_IDENTITY = "local"

_IDENTITY_CLAIMS = ("oid", "sub", "upn", "preferred_username", "appid", "unique_name", "name")


def current_identity() -> str | None:
    """Return a stable identifier for the caller, or None when unauthenticated.

    Used for object-level ownership checks: a plan created by one principal
    must not be executable by another that merely knows its id.
    """
    claims = _caller_claims()
    if not claims:
        return None
    for key in _IDENTITY_CLAIMS:
        value = claims.get(key)
        if value:
            return str(value)
    return None


_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "add_measure_with_validation",
        "apply_plan",
        "apply_theme_and_accessibility_rules",
        "commit_workspace_to_git",
        "create_report_from_dataset",
        "create_semantic_model_from_schema",
        "design_report_page_from_requirements",
        "edit_report_visual",
        "promote_in_pipeline",
        "refactor_to_calculation_groups",
        "run_dax_regression",
        "run_refresh",
        "deploy_to_workspace",
        "set_sensitivity_labels",
        "setup_rls_and_roles",
        "sync_git_to_workspace",
    }
)

_ADMIN_TOOLS: frozenset[str] = frozenset(
    {
        # Releasing artifacts between deployment pipeline stages and
        # applying sensitivity labels are governance operations: they
        # change what a workspace publishes and how it is classified,
        # so they need the administrative tier rather than write access.
        "promote_in_pipeline",
        "set_sensitivity_labels",
    }
)

TOOL_REQUIRED_SCOPE: dict[str, str] = {
    **dict.fromkeys(_WRITE_TOOLS, SCOPE_WRITE),
    **dict.fromkeys(_ADMIN_TOOLS, SCOPE_ADMIN),
}


class ToolAuthorizationError(PermissionError):
    """Raised when the caller's scopes do not cover the requested tool."""


def required_scope_for_tool(tool_name: str) -> str:
    """Return the scope a tool needs, defaulting to read-only access."""
    return TOOL_REQUIRED_SCOPE.get(tool_name, SCOPE_READ)


def _caller_claims() -> dict[str, object] | None:
    """Claims of the principal driving the current tool call.

    Resolved per request, not per session: the MCP server dispatches tool
    calls from the session task, so a contextvar written while handling
    the HTTP request would otherwise be the claims of whoever opened the
    session rather than the claims of the caller of this call.
    """
    from powerbi_orchestrator_mcp.orchestrator.transport import current_caller_claims

    return current_caller_claims()


def _granted_scopes() -> set[str] | None:
    from powerbi_orchestrator_mcp.orchestrator.transport import granted_scopes_from_claims

    claims = _caller_claims()
    if not claims:
        return None
    return granted_scopes_from_claims(claims)


def authorize_tool(tool_name: str) -> None:
    """Raise when the bound identity lacks the scope required by a tool."""
    scopes = _granted_scopes()
    if scopes is None:
        return

    needed = required_scope_for_tool(tool_name)
    if needed in scopes or SCOPE_ADMIN in scopes:
        return

    raise ToolAuthorizationError(
        f"tool {tool_name!r} requires scope {needed}; granted scopes: {sorted(scopes) or 'none'}"
    )


def require_capability_scope(scope: str, *, reason: str) -> None:
    """Raise when the bound identity lacks ``scope`` for one capability.

    Some capabilities are worth more than the tool that exposes them.
    Running a DAX query as another user, for instance, reads the data
    that user's row-level security is there to hide — so it is not a
    read, whatever the tool's own scope says.

    Without a bound identity there is nothing to check against, the
    same as for the per-tool scopes.
    """
    scopes = _granted_scopes()
    if scopes is None:
        return
    if scope in scopes or SCOPE_ADMIN in scopes:
        return
    raise ToolAuthorizationError(
        f"{reason} requires scope {scope}; granted scopes: {sorted(scopes) or 'none'}"
    )


def requires_scope(tool_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Enforce the tool's required scope before the body runs."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            authorize_tool(tool_name)
            return await func(*args, **kwargs)

        # Marks the tool as explicitly scoped, so a tool that was left
        # out of the mapping can be found.
        wrapper.__requires_scope__ = tool_name  # type: ignore[attr-defined]
        return wrapper

    return decorator


__all__ = [
    "LOCAL_IDENTITY",
    "SCOPE_ADMIN",
    "SCOPE_READ",
    "SCOPE_WRITE",
    "TOOL_REQUIRED_SCOPE",
    "ToolAuthorizationError",
    "authorize_tool",
    "current_identity",
    "required_scope_for_tool",
    "requires_scope",
]
