"""Tests for the boundary holes the enterprise review found.

Five gaps, all of them "a caller could reach further than the
documentation promised":

1. Eight tools read a PBIP without checking the workspace root, so
   the content of any project on the host came back in the response.
2. The engines wrote wherever the plan pointed, and a plan is built
   by the caller.
3. Every plan dumped the whole model into the system temporary
   directory, world-readable and never removed.
4. A DAX query could be run as another user — reading the rows that
   user's row-level security is there to hide — with the read scope.
5. PBI_TRANSPORT was documented as selecting the transport and was
   not read, so a container started in stdio and published no port.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.orchestrator.authorization import (
    SCOPE_READ,
    SCOPE_WRITE,
    ToolAuthorizationError,
)
from powerbi_orchestrator_mcp.validation.model_metadata import (
    MetadataError,
    gather_pbip_metadata,
)


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(root))
    return root


def _pbip_at(root: Path, name: str = "sample.pbip") -> Path:
    pbip = root / name
    dataset = pbip / "sample.Dataset"
    dataset.mkdir(parents=True)
    (pbip / "sample.pbip").write_text("{}", encoding="utf-8")
    (dataset / "definition.pbism").write_text(
        json.dumps(
            {
                "model": {
                    "tables": [
                        {
                            "name": "FactSales",
                            "columns": [{"name": "Amount", "dataType": "decimal"}],
                            "measures": [
                                {
                                    "name": "Total",
                                    "expression": "SUM(FactSales[Amount])",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return pbip


class TestTheReadIsBounded:
    def test_a_project_inside_the_root_is_read(self, workspace: Path) -> None:
        meta = gather_pbip_metadata(str(_pbip_at(workspace)))

        assert meta.tables == ["FactSales"]
        assert "Total" in meta.measures

    def test_a_project_outside_the_root_is_refused(self, workspace: Path, tmp_path: Path) -> None:
        outside = _pbip_at(tmp_path / "outside", name="Secret.pbip")

        with pytest.raises(MetadataError, match="outside allowed workspace root"):
            gather_pbip_metadata(str(outside))

    def test_the_tools_that_read_a_model_are_bounded(self, workspace: Path, tmp_path: Path) -> None:
        """The choke point is enough: every tool goes through it."""
        from powerbi_orchestrator_mcp.tools.abbreviate_names import abbreviate_names
        from powerbi_orchestrator_mcp.tools.abstract_measures import abstract_measures
        from powerbi_orchestrator_mcp.tools.simplify_dax_measure import (
            simplify_dax_measure,
        )

        outside = _pbip_at(tmp_path / "outside", name="Secret.pbip")

        for result in (
            abstract_measures(str(outside)),
            abbreviate_names(str(outside)),
            simplify_dax_measure(pbip_path=str(outside), all_measures=True),
        ):
            assert result["success"] is False
            assert "outside allowed workspace root" in (result["error_message"] or "")


class TestTheWriteIsBounded:
    @pytest.mark.asyncio
    async def test_a_plan_cannot_write_outside_the_root(
        self, workspace: Path, tmp_path: Path
    ) -> None:
        """The target comes from the plan, and the plan comes from the caller."""
        from powerbi_orchestrator_mcp.orchestrator.server import (
            apply_plan,
            plan_change,
        )

        victim = tmp_path / "outside" / "Victim.pbip"
        _pbip_at(victim.parent, name="Victim.pbip")

        plan = await plan_change(
            intent="safe_rename",
            options={
                "old_path": "FactSales[Amount]",
                "new_path": "FactSales[Value]",
                "target": str(victim),
            },
        )
        result = await apply_plan(plan_id=plan.plan_id, dry_run=False)

        assert result.result != "success"
        # The model file was not touched.
        definition = victim / "sample.Dataset" / "definition.pbism"
        assert "Value" not in definition.read_text(encoding="utf-8")


class TestTheSnapshotStaysPrivate:
    @pytest.mark.asyncio
    async def test_it_does_not_go_to_the_temporary_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from powerbi_orchestrator_mcp.engines.base import ConnectionHandle
        from powerbi_orchestrator_mcp.engines.te_adapter import InMemoryModelingAdapter

        home = tmp_path / "home"
        monkeypatch.setenv("PBI_ORCHESTRATOR_HOME", str(home))
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(tmp_path))

        engine = InMemoryModelingAdapter()
        conn = ConnectionHandle(
            engine="test",
            target_type="pbip_folder",
            target_ref=str(_pbip_at(tmp_path / "proj")),
            session_token="s",
        )
        handle = await engine.snapshot(conn, "pre-rename-plan_test")
        path = Path(handle.path)

        assert str(path).startswith(str(home))
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700

    @pytest.mark.asyncio
    async def test_the_snapshot_step_refuses_a_target_outside_the_root(
        self, workspace: Path, tmp_path: Path
    ) -> None:
        from powerbi_orchestrator_mcp.orchestrator.planner import PlanBuilder
        from powerbi_orchestrator_mcp.orchestrator.step_executor import (
            ValidationStepExecutor,
        )

        outside = _pbip_at(tmp_path / "outside", name="Secret.pbip")
        plan = PlanBuilder().build_safe_rename(
            old_path="T[A]",
            new_path="T[B]",
            target=str(outside),
        )
        snapshot_step = next(s for s in plan.steps if s.action == "create_snapshot")
        snapshot_step.args["pbip_path"] = str(outside)

        outcome = await ValidationStepExecutor().execute_step(snapshot_step)

        assert outcome.success is False
        assert "outside allowed workspace root" in (outcome.error_message or "")


class TestImpersonationIsNotARead:
    def test_reading_as_another_user_needs_write(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from powerbi_orchestrator_mcp.orchestrator import authorization, transport

        monkeypatch.setattr(
            transport,
            "current_caller_claims",
            lambda: {"scp": SCOPE_READ, "oid": "user-1"},
        )
        monkeypatch.setattr(
            authorization,
            "_caller_claims",
            lambda: {"scp": SCOPE_READ, "oid": "user-1"},
        )

        with pytest.raises(ToolAuthorizationError, match="Tools.Write"):
            authorization.require_capability_scope(
                SCOPE_WRITE,
                reason="impersonating another user to read a dataset",
            )

    def test_write_is_enough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from powerbi_orchestrator_mcp.orchestrator import authorization, transport

        monkeypatch.setattr(
            transport,
            "current_caller_claims",
            lambda: {"scp": SCOPE_WRITE, "oid": "user-1"},
        )
        monkeypatch.setattr(
            authorization,
            "_caller_claims",
            lambda: {"scp": SCOPE_WRITE, "oid": "user-1"},
        )

        authorization.require_capability_scope(
            SCOPE_WRITE,
            reason="impersonating another user to read a dataset",
        )


class TestTheTransportVariable:
    def test_pbi_transport_selects_http(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import parse_transport_args

        monkeypatch.setenv("PBI_TRANSPORT", "http")
        monkeypatch.setenv("PBI_ENTRA_TENANT_ID", "tenant")
        monkeypatch.setenv("PBI_ENTRA_AUDIENCE", "api://x")

        transport, config = parse_transport_args([])

        assert transport == "http"
        assert config is not None

    def test_the_argument_still_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import parse_transport_args

        monkeypatch.setenv("PBI_TRANSPORT", "http")

        transport, _ = parse_transport_args(["--transport", "stdio"])

        assert transport == "stdio"

    def test_the_default_is_stdio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import parse_transport_args

        monkeypatch.delenv("PBI_TRANSPORT", raising=False)

        transport, _ = parse_transport_args([])

        assert transport == "stdio"


def test_the_environment_variable_is_read_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A container sets the variable; the process reads it on startup."""
    from powerbi_orchestrator_mcp.orchestrator.transport import parse_transport_args

    monkeypatch.delenv("PBI_TRANSPORT", raising=False)
    assert parse_transport_args([])[0] == "stdio"

    monkeypatch.setenv("PBI_TRANSPORT", "http")
    monkeypatch.setenv("PBI_ENTRA_TENANT_ID", "tenant")
    monkeypatch.setenv("PBI_ENTRA_AUDIENCE", "api://x")
    assert parse_transport_args([])[0] == "http"


def test_the_read_denylist_covers_the_container_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pod's secrets are not reachable even with the opt-out."""
    from powerbi_orchestrator_mcp.validation.path_safety import validate_safe_pbip_path

    monkeypatch.setenv("PBI_ALLOW_OUTSIDE_WORKSPACE_READ", "1")

    with pytest.raises(ValueError):
        validate_safe_pbip_path("/run/secrets/token", must_exist=False)


def test_the_sandbox_is_not_writable_from_a_read_scoped_caller() -> None:
    """The two file-producing tools stay inside the workspace.

    This is the property the scope classification relies on: a
    read-scoped caller can produce files, but only inside the
    workspace.
    """
    from powerbi_orchestrator_mcp.orchestrator.authorization import (
        required_scope_for_tool,
    )

    assert required_scope_for_tool("screenshot_report_pages") == SCOPE_READ
    assert required_scope_for_tool("generate_data_dictionary") == SCOPE_READ
