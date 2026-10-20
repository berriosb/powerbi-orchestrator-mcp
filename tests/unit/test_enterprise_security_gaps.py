"""Tests for the security gaps that were still open after v1.19.0.

Three of them, each verified before it was fixed:

- The role and table names are written into the TMDL as identifiers. A
  name carrying a line break closed the current role block and opened a
  new one, so a caller could write a role the spec never asked for. It
  also produced invalid TMDL for any name that is not a bare
  identifier, such as "Region-West".

- The read path was unbounded unless PBI_WORKSPACE_ROOT was set, so
  any directory outside the denylist could be read — and reading is how
  content reaches the model context.

- A plan could be deleted by anyone who knew its id, while reading one
  required being its owner.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from powerbi_orchestrator_mcp.orchestrator.plan_store import PlanStore
from powerbi_orchestrator_mcp.tools.setup_rls_and_roles import (
    RoleSpec,
    render_tmdl_role,
)
from powerbi_orchestrator_mcp.validation.path_safety import validate_safe_pbip_path


class TestTmdlIdentifierSafety:
    def test_a_role_name_with_a_line_break_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            RoleSpec(
                role_name="reader\n}\nrole admin\n    tablePermission Hidden",
                filter_expression="1=1",
                table="FactSales",
            )

    def test_a_table_name_with_a_line_break_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            RoleSpec(
                role_name="reader",
                filter_expression="1=1",
                table="FactSales\n}\nrole admin",
            )

    def test_a_brace_in_a_name_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            RoleSpec(role_name="read}er", filter_expression="1=1", table="FactSales")

    def test_a_quote_in_a_name_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            RoleSpec(role_name='read"er', filter_expression="1=1", table="FactSales")

    def test_a_plain_identifier_is_written_bare(self) -> None:
        role = RoleSpec(role_name="reader", filter_expression="1=1", table="FactSales")
        rendered = render_tmdl_role(role)
        assert "role reader\n" in rendered
        assert "tablePermission FactSales" in rendered

    def test_a_name_that_is_not_a_bare_identifier_is_quoted(self) -> None:
        """`role Region-West` is not valid TMDL; the hyphen needs quoting."""
        role = RoleSpec(role_name="Region-West", filter_expression="1=1", table="Fact Sales")
        rendered = render_tmdl_role(role)
        assert 'role "Region-West"' in rendered
        assert 'tablePermission "Fact Sales"' in rendered

    def test_the_rendered_block_has_no_injected_role(self) -> None:
        """A refused name never reaches the renderer."""
        with pytest.raises(ValidationError):
            RoleSpec(
                role_name="reader\n}\nrole admin\n    tablePermission Hidden",
                filter_expression="1=1",
                table="FactSales",
            )
        # The renderer refuses it too, in case a caller builds the model
        # by other means.
        role = RoleSpec.model_construct(
            role_name="reader\n}\nrole admin",
            filter_expression="1=1",
            table="FactSales",
        )
        with pytest.raises(ValueError, match="line break"):
            render_tmdl_role(role)


class TestReadPathContainment:
    def test_a_read_outside_the_root_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("PBI_WORKSPACE_ROOT", raising=False)
        monkeypatch.delenv("PBI_ALLOW_OUTSIDE_WORKSPACE_READ", raising=False)

        with pytest.raises(ValueError, match="outside allowed workspace root"):
            validate_safe_pbip_path(str(tmp_path / "elsewhere"), must_exist=False)

    def test_the_allowance_flag_restores_the_read(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("PBI_WORKSPACE_ROOT", raising=False)
        monkeypatch.setenv("PBI_ALLOW_OUTSIDE_WORKSPACE_READ", "1")
        target = tmp_path / "elsewhere"

        assert validate_safe_pbip_path(str(target), must_exist=False) == target.resolve()


class TestPlanDeletionOwnership:
    def test_a_plan_cannot_be_deleted_by_another_owner(self, tmp_path: Path) -> None:
        store = PlanStore(db_path=tmp_path / "plans.db")
        from powerbi_orchestrator_mcp.orchestrator.plan_models import Plan

        store.put(
            Plan(id="p1", steps=[]),
            owner="user-a",
        )
        assert store.get("p1", owner="user-a") is not None

        # Another principal knows the id and cannot delete the plan.
        assert store.delete("p1", owner="user-b") is False
        assert store.get("p1", owner="user-a") is not None

        # The owner can.
        assert store.delete("p1", owner="user-a") is True
        assert store.get("p1", owner="user-a") is None
