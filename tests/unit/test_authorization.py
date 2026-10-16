from __future__ import annotations

from typing import Any

import pytest

from powerbi_orchestrator_mcp.orchestrator.authorization import (
    SCOPE_ADMIN,
    SCOPE_READ,
    SCOPE_WRITE,
    TOOL_REQUIRED_SCOPE,
    ToolAuthorizationError,
    authorize_tool,
    required_scope_for_tool,
    requires_scope,
)


@pytest.fixture(autouse=True)
def _clear_identity() -> None:
    from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

    set_current_user(None)
    yield
    set_current_user(None)


class TestScopeMapping:
    def test_write_tools_are_mapped(self) -> None:
        for tool in ("deploy_to_workspace", "run_refresh", "apply_plan", "promote_in_pipeline"):
            assert TOOL_REQUIRED_SCOPE[tool] == SCOPE_WRITE

    def test_read_tools_are_not_in_the_write_map(self) -> None:
        for tool in ("execute_dax_query", "powerbi_health", "audit_model_and_report"):
            assert required_scope_for_tool(tool) == SCOPE_READ

    def test_unknown_tool_defaults_to_read(self) -> None:
        assert required_scope_for_tool("totally_unknown") == SCOPE_READ

    def test_admin_map_is_empty_until_an_admin_tool_exists(self) -> None:
        assert SCOPE_ADMIN not in set(TOOL_REQUIRED_SCOPE.values())

    def test_every_write_scope_entry_is_a_real_server_tool(self) -> None:
        import inspect

        from powerbi_orchestrator_mcp.orchestrator import server as server_mod

        server_tools = {
            name
            for name, obj in vars(server_mod).items()
            if inspect.isfunction(obj) and not name.startswith("_")
        }

        assert set(TOOL_REQUIRED_SCOPE) <= server_tools

    def test_write_scope_set_matches_declared_write_tools(self) -> None:
        assert len(TOOL_REQUIRED_SCOPE) == 16
        assert all(scope == SCOPE_WRITE for scope in TOOL_REQUIRED_SCOPE.values())


class TestAuthorizeTool:
    def test_read_scope_cannot_call_write_tool(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user({"scp": "Tools.Read"})

        with pytest.raises(ToolAuthorizationError, match="Tools.Write"):
            authorize_tool("run_refresh")

    def test_write_scope_can_call_write_tool(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user({"scp": "Tools.Read Tools.Write"})

        authorize_tool("run_refresh")

    def test_admin_scope_grants_everything(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user({"roles": ["Tools.Admin"]})

        authorize_tool("deploy_to_workspace")

    def test_read_scope_can_call_read_tool(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user({"scp": "Tools.Read"})

        authorize_tool("execute_dax_query")

    def test_app_role_in_roles_also_grants(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user({"roles": ["Tools.Write"]})

        authorize_tool("run_refresh")

    def test_no_identity_skips_enforcement(self) -> None:
        authorize_tool("deploy_to_workspace")

    def test_empty_scope_list_is_denied(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user({"scp": ""})

        with pytest.raises(ToolAuthorizationError):
            authorize_tool("run_refresh")


class TestRequiresScopeDecorator:
    @pytest.mark.asyncio
    async def test_blocks_before_body_executes(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user({"scp": "Tools.Read"})
        ran: list[str] = []

        @requires_scope("run_refresh")
        async def tool() -> dict[str, Any]:
            ran.append("yes")
            return {}

        with pytest.raises(ToolAuthorizationError):
            await tool()

        assert ran == []

    @pytest.mark.asyncio
    async def test_allows_when_scope_present(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user({"scp": "Tools.Write"})

        @requires_scope("run_refresh")
        async def tool() -> dict[str, Any]:
            return {"ok": True}

        assert await tool() == {"ok": True}

    @pytest.mark.asyncio
    async def test_stdio_mode_is_not_blocked(self) -> None:
        @requires_scope("run_refresh")
        async def tool() -> dict[str, Any]:
            return {"ok": True}

        assert await tool() == {"ok": True}


class TestServerToolsAreGuarded:
    @pytest.mark.parametrize(
        "tool_name",
        [
            "apply_plan",
            "commit_workspace_to_git",
            "create_report_from_dataset",
            "create_semantic_model_from_schema",
            "design_report_page_from_requirements",
            "deploy_to_workspace",
            "edit_report_visual",
            "promote_in_pipeline",
            "refactor_to_calculation_groups",
            "run_dax_regression",
            "run_refresh",
            "set_sensitivity_labels",
            "setup_rls_and_roles",
            "sync_git_to_workspace",
            "add_measure_with_validation",
            "apply_theme_and_accessibility_rules",
        ],
    )
    async def test_write_tool_enforces_its_scope(self, tool_name: str) -> None:
        from powerbi_orchestrator_mcp.orchestrator import server as server_mod
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user({"scp": "Tools.Read"})
        tool = getattr(server_mod, tool_name)

        with pytest.raises(ToolAuthorizationError):
            await tool()  # type: ignore[operator]
