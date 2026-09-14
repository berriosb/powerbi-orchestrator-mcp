"""Tests for setup_rls_and_roles (Sprint 11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.tools.setup_rls_and_roles import (
    RlsTestQuery,
    RoleMember,
    RoleSpec,
    merge_roles_into_tmdl,
    render_tmdl_role,
    setup_rls_and_roles,
)

VALID_YAML = """\
- role_name: Region-West
  filter_expression: '[Region] = ''West'''
  table: DimRegion
  members:
    - type: email
      value: gerente.west@acme.test
    - type: group
      value: acme-west-managers
  test_queries:
    - name: Total Sales
      dax: 'EVALUATE ROW(''x'', SUM(FactSales[Amount]))'
      expected:
        Region-West: 100
- role_name: Region-East
  filter_expression: '[Region] = ''East'''
  table: DimRegion
  members:
    - type: email
      value: east.manager@acme.test
"""


class TestSpecParsing:
    def test_yaml_parses_to_two_roles(self) -> None:
        res = setup_rls_and_roles(target="/tmp/x.tmdl", spec_yaml=VALID_YAML, dry_run=True)
        assert len(res.roles_created) == 2
        assert res.roles_created[0].role_name == "Region-West"
        assert res.roles_created[0].members_count == 2
        assert res.roles_created[0].test_queries_count == 1

    def test_missing_spec_returns_error(self) -> None:
        res = setup_rls_and_roles(target="/tmp/x.tmdl", dry_run=True)
        assert res.warnings
        assert any("required" in w.lower() or "spec" in w.lower() for w in res.warnings)

    def test_both_specs_returns_error(self) -> None:
        res = setup_rls_and_roles(
            target="/tmp/x.tmdl",
            spec_yaml="- role_name: x",
            spec_json="[]",
            dry_run=True,
        )
        assert any("not both" in w for w in res.warnings)

    def test_invalid_json_returns_error(self) -> None:
        res = setup_rls_and_roles(target="/tmp/x.tmdl", spec_json="not json", dry_run=True)
        assert any("not valid" in w.lower() for w in res.warnings)


class TestRoleSpecPydantic:
    def test_invalid_member_type_rejected(self) -> None:
        with pytest.raises(Exception):
            RoleMember(type="unknown", value="x")

    def test_role_with_minimal_fields(self) -> None:
        r = RoleSpec(role_name="r", filter_expression="[X]=1", table="T")
        assert r.members == []
        assert r.test_queries == []

    def test_test_query_with_expected_map(self) -> None:
        q = RlsTestQuery(
            name="q", dax="1+1", expected={"Region-West": 100, "Region-East": 0}
        )
        assert q.expected["Region-West"] == 100


class TestTmdlRendering:
    def test_render_role_block_basic(self) -> None:
        block = render_tmdl_role(
            RoleSpec(
                role_name="R",
                filter_expression="[X]=1",
                table="T",
                members=[RoleMember(type="email", value="a@b")],
                description="A test role",
            )
        )
        assert "role R" in block
        assert "tablePermission T" in block
        assert "[X]=1" in block
        assert "a@b" in block
        assert "description" in block

    def test_render_role_without_members(self) -> None:
        block = render_tmdl_role(
            RoleSpec(role_name="R", filter_expression="[X]=1", table="T")
        )
        assert "role R" in block
        assert "member:" not in block

    def test_merge_appends_role_section(self) -> None:
        existing = "table Foo\n    column A\n    dataType: int64\n"
        blocks = [
            "role Bar\n    tablePermission T\n        filterExpression: \"[X]=1\"\n",
        ]
        merged = merge_roles_into_tmdl(existing, blocks)
        assert "table Foo" in merged
        assert "role Bar" in merged
        assert "---- Roles (RLS) ----" in merged


class TestAtomicWriteAndExecution:
    def _make_pbip(self, tmp_path: Path, tmdl_text: str = "table Foo\n") -> Path:
        """Build a minimal PBIP with a TMDL inside."""
        pbip = tmp_path / "demo.pbip"
        pbip.mkdir()
        (pbip / "demo.pbip").write_text("{}")
        ds = pbip / "demo.Dataset"
        ds.mkdir()
        (ds / "definition.pbism").write_text("{}")
        (ds / "definition.tmdl").write_text(tmdl_text)
        return pbip

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        pbip = self._make_pbip(tmp_path)
        res = setup_rls_and_roles(
            target=str(pbip), spec_yaml=VALID_YAML, dry_run=True
        )
        assert res.dry_run is True
        assert not any(
            "Roles (RLS)" in line
            for line in (pbip / "demo.Dataset" / "definition.tmdl").read_text().splitlines()
        )

    def test_apply_writes_roles_to_tmdl(self, tmp_path: Path) -> None:
        pbip = self._make_pbip(tmp_path)
        res = setup_rls_and_roles(
            target=str(pbip), spec_yaml=VALID_YAML, dry_run=False
        )
        assert res.dry_run is False
        text = (pbip / "demo.Dataset" / "definition.tmdl").read_text()
        assert "role Region-West" in text
        assert "role Region-East" in text

    def test_risk_score_increases_with_complexity(self, tmp_path: Path) -> None:
        # Many roles + many members → higher risk.
        yaml_many = """\
- role_name: r1
  filter_expression: "[X]='*'"
  table: T
  members:
    - {type: email, value: a@b}
    - {type: email, value: c@d}
- role_name: r2
  filter_expression: "[Y]='*'"
  table: T
  members:
    - {type: email, value: e@f}
"""
        res = setup_rls_and_roles(target="/tmp/x", spec_yaml=yaml_many, dry_run=True)
        assert res.risk_score > 0.0

    def test_test_engine_runs_and_passes(self, tmp_path: Path) -> None:
        pbip = self._make_pbip(tmp_path)

        def fake_engine(role_name: str, dax: str) -> object:
            return {"Region-West": 100, "Region-East": 200}[role_name]

        res = setup_rls_and_roles(
            target=str(pbip),
            spec_yaml=VALID_YAML,
            dry_run=False,
            test_engine=fake_engine,
        )
        assert len(res.test_results) == 1
        assert res.test_results[0].passed is True
        assert res.failed_test is None

    def test_test_engine_failure_rolls_back(self, tmp_path: Path) -> None:
        pbip = self._make_pbip(tmp_path)
        original = (pbip / "demo.Dataset" / "definition.tmdl").read_text()

        def fake_engine(role_name: str, dax: str) -> object:
            return {"Region-West": 999, "Region-East": 0}[role_name]  # wrong value

        res = setup_rls_and_roles(
            target=str(pbip),
            spec_yaml=VALID_YAML,
            dry_run=False,
            test_engine=fake_engine,
            rollback_on_test_failure=True,
        )
        assert res.rollback_performed is True
        assert res.failed_test is not None
        # TMDL reverted.
        text = (pbip / "demo.Dataset" / "definition.tmdl").read_text()
        assert text == original

    def test_test_engine_failure_no_rollback_when_disabled(
        self, tmp_path: Path
    ) -> None:
        pbip = self._make_pbip(tmp_path)

        def fake_engine(role_name: str, dax: str) -> object:
            return 999  # always wrong

        res = setup_rls_and_roles(
            target=str(pbip),
            spec_yaml=VALID_YAML,
            dry_run=False,
            test_engine=fake_engine,
            rollback_on_test_failure=False,
        )
        # No rollback → changes persist even though tests fail.
        assert res.rollback_performed is False
        assert res.failed_test is not None
        text = (pbip / "demo.Dataset" / "definition.tmdl").read_text()
        assert "role Region-West" in text

    def test_missing_pbip_returns_warning(self, tmp_path: Path) -> None:
        res = setup_rls_and_roles(
            target=str(tmp_path / "nonexistent"), spec_yaml=VALID_YAML, dry_run=False
        )
        assert any("could not locate" in w or "not found" in w for w in res.warnings)

    def test_direct_tmdl_file_path(self, tmp_path: Path) -> None:
        tmdl = tmp_path / "model.tmdl"
        tmdl.write_text("table Foo\n")
        res = setup_rls_and_roles(
            target=str(tmdl), spec_yaml=VALID_YAML, dry_run=False
        )
        text = tmdl.read_text()
        assert "role Region-West" in text
        assert res.dry_run is False

    def test_atomic_write_no_partial_files(self, tmp_path: Path) -> None:
        pbip = self._make_pbip(tmp_path)
        setup_rls_and_roles(target=str(pbip), spec_yaml=VALID_YAML, dry_run=False)
        dataset = pbip / "demo.Dataset"
        temps = list(dataset.glob(".rls.*"))
        assert temps == []
