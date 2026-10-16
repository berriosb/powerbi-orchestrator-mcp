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

_ADMIN_TOOLS: frozenset[str] = frozenset()

TOOL_REQUIRED_SCOPE: dict[str, str] = {
    **dict.fromkeys(_WRITE_TOOLS, SCOPE_WRITE),
    **dict.fromkeys(_ADMIN_TOOLS, SCOPE_ADMIN),
}


class ToolAuthorizationError(PermissionError):
    """Raised when the caller's scopes do not cover the requested tool."""


def required_scope_for_tool(tool_name: str) -> str:
    """Return the scope a tool needs, defaulting to read-only access."""
    return TOOL_REQUIRED_SCOPE.get(tool_name, SCOPE_READ)


def _granted_scopes() -> set[str] | None:
    from powerbi_orchestrator_mcp.orchestrator.transport import get_current_user

    claims = get_current_user()
    if not claims:
        return None

    raw_scp = claims.get("scp", "")
    scp_list: list[str]
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


def requires_scope(tool_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Enforce the tool's required scope before the body runs."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            authorize_tool(tool_name)
            return await func(*args, **kwargs)

        return wrapper

    return decorator


__all__ = [
    "SCOPE_ADMIN",
    "SCOPE_READ",
    "SCOPE_WRITE",
    "TOOL_REQUIRED_SCOPE",
    "ToolAuthorizationError",
    "authorize_tool",
    "required_scope_for_tool",
    "requires_scope",
]
