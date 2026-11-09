from __future__ import annotations

import io
import json
import os
import stat
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from powerbi_orchestrator_mcp import cli
from powerbi_orchestrator_mcp.cloud.auth import AuthConfig, FabricCredential
from powerbi_orchestrator_mcp.cloud.fabric_client import (
    FabricClient,
    _build_async_client,
    _sanitize_no_proxy_value,
)
from powerbi_orchestrator_mcp.orchestrator import audit as audit_mod
from powerbi_orchestrator_mcp.orchestrator import plan_executions as pe_mod
from powerbi_orchestrator_mcp.orchestrator import plan_store as ps_mod
from powerbi_orchestrator_mcp.orchestrator import server as server_mod
from powerbi_orchestrator_mcp.orchestrator.audit import AuditLog
from powerbi_orchestrator_mcp.orchestrator.elicitation import ElicitationResponse
from powerbi_orchestrator_mcp.orchestrator.plan_executions import (
    PlanExecution,
    PlanExecutionState,
    PlanExecutionStore,
)
from powerbi_orchestrator_mcp.orchestrator.plan_models import Plan, PlanOptions, PlanStep
from powerbi_orchestrator_mcp.orchestrator.plan_store import PlanStore
from powerbi_orchestrator_mcp.orchestrator.planner import PlanBuilder
from powerbi_orchestrator_mcp.orchestrator.readiness import (
    READ_OPT_OUT_ENV,
    WORKSPACE_ROOT_ENV,
    collect_readiness,
)
from powerbi_orchestrator_mcp.orchestrator.rollback import RollbackEngine, StepOutcome
from powerbi_orchestrator_mcp.orchestrator.step_executor import (
    get_default_registry,
    reset_default_registry,
)
from powerbi_orchestrator_mcp.tools.powerbi_health import powerbi_health

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")


class _ScriptedExecutor:
    def __init__(self, outcomes: dict[str, StepOutcome], engine_name: str = "modeling") -> None:
        self._outcomes = outcomes
        self.engine_name = engine_name
        self.calls: list[str] = []

    async def execute_step(self, step: PlanStep) -> StepOutcome:
        self.calls.append(step.id)
        if step.id in self._outcomes:
            return self._outcomes[step.id]
        return StepOutcome(success=True)


def _register_scripted(outcomes: dict[str, StepOutcome]) -> list[str]:
    calls: list[str] = []
    reg = get_default_registry()
    for eng in ("validation", "modeling", "report", "dry_run"):
        exec_inst = _ScriptedExecutor(outcomes, engine_name=eng)
        exec_inst.calls = calls
        reg.register(exec_inst)
    return calls


class TestVerifyDockerImageHandshake:
    def test_check_mcp_reads_responses_before_closing_stdin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import importlib.util

        script_path = Path(__file__).resolve().parents[2] / "scripts" / "verify_docker_image.py"
        spec = importlib.util.spec_from_file_location("verify_docker_image", script_path)
        assert spec is not None and spec.loader is not None
        vdi: Any = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vdi)

        events: list[str] = []

        class _FakeStdin:
            def __init__(self, parent: _FakePopen) -> None:
                self._parent = parent
                self.closed = False

            def write(self, data: str) -> int:
                assert not self.closed, "stdin was closed before writing"
                msg = json.loads(data.strip())
                method = msg.get("method")
                events.append(f"write:{method}")
                if method == "initialize":
                    self._parent.queue.append(
                        json.dumps(
                            {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}
                        )
                        + "\n"
                    )
                elif method == "tools/list":
                    self._parent.queue.append(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": 2,
                                "result": {
                                    "tools": [
                                        {"name": "plan_change"},
                                        {"name": "apply_plan"},
                                    ]
                                },
                            }
                        )
                        + "\n"
                    )
                return len(data)

            def flush(self) -> None:
                return None

            def close(self) -> None:
                self.closed = True
                events.append("stdin:close")

        class _FakeStdout:
            def __init__(self, parent: _FakePopen) -> None:
                self._parent = parent

            def readline(self) -> str:
                assert not self._parent.stdin.closed, "stdin was closed before reading response"
                if not self._parent.queue:
                    return ""
                line = self._parent.queue.pop(0)
                payload = json.loads(line)
                events.append(f"read:id={payload.get('id')}")
                return line

        class _FakePopen:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.queue: list[str] = []
                self.stdin = _FakeStdin(self)
                self.stdout = _FakeStdout(self)
                self.stderr = io.StringIO("")

            def poll(self) -> int | None:
                return 0

            def terminate(self) -> None:
                return None

            def wait(self, timeout: float | None = None) -> int:
                return 0

            def kill(self) -> None:
                return None

        monkeypatch.setattr(vdi.subprocess, "Popen", _FakePopen)
        tools = vdi.check_mcp("test-image:latest")
        assert tools == ["apply_plan", "plan_change"]
        assert events == [
            "write:initialize",
            "read:id=1",
            "write:notifications/initialized",
            "write:tools/list",
            "read:id=2",
            "stdin:close",
        ]


class TestFabricClientNoProxyIPv6:
    def test_sanitize_no_proxy_strips_brackets_and_ipv6_cidr(self) -> None:
        raw = "localhost, 127.0.0.1, [::1], ::1/128, fe80::/10, [::1]:8080"
        assert _sanitize_no_proxy_value(raw) == "localhost,127.0.0.1,::1,::1,fe80::,::1"

    @pytest.mark.asyncio
    async def test_fabric_client_instantiates_with_bracketed_ipv6_in_no_proxy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_no_proxy = "localhost,127.0.0.1,[::1],::1/128"
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8080")
        monkeypatch.setenv("NO_PROXY", original_no_proxy)

        cred = FabricCredential(AuthConfig(mode="interactive"))
        client = FabricClient(cred)
        try:
            assert os.environ["NO_PROXY"] == original_no_proxy
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_build_async_client_falls_back_when_invalid_url_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[bool] = []
        real_client = httpx.AsyncClient

        def _fake_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            trust = bool(kwargs.get("trust_env", True))
            calls.append(trust)
            if trust:
                raise httpx.InvalidURL("Invalid port: ':1]'")
            return real_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _fake_async_client)
        client = _build_async_client(
            base_url="https://api.fabric.microsoft.com/v1",
            timeout=httpx.Timeout(5.0),
            trust_env=True,
        )
        try:
            assert calls == [True, False]
        finally:
            await client.aclose()


class TestFailedStepExcludedFromNonRevertible:
    @pytest.mark.asyncio
    async def test_rollback_engine_does_not_mark_failed_step_as_non_revertible(self) -> None:
        step1 = PlanStep(
            id="p:s1",
            engine="modeling",
            action="a1",
            rollback_step=PlanStep(id="p:s1-rb", engine="modeling", action="a1_rb"),
        )
        step2_failed = PlanStep(
            id="p:s2",
            engine="validation",
            action="a2",
            rollback_step=None,
        )
        exec_mock = _ScriptedExecutor({})
        engine = RollbackEngine(exec_mock)
        result = await engine.execute_plan(
            executed_steps=[step1, step2_failed],
            failed_step=step2_failed,
        )
        assert result.status == "rolled_back"
        assert result.rolled_back_steps == ["p:s1"]
        assert result.non_revertible_steps == []


class TestAutoRollbackAndArtifactsChanged:
    @pytest.fixture(autouse=True)
    def _isolated_stores(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
        monkeypatch.setattr(ps_mod, "PLAN_DIR", tmp_path / "plans")
        monkeypatch.setattr(ps_mod, "PLAN_DB", tmp_path / "plans" / "plans.db")
        monkeypatch.setattr(
            server_mod, "_plan_store", PlanStore(db_path=tmp_path / "plans" / "plans.db")
        )
        monkeypatch.setattr(pe_mod, "EXEC_DIR", tmp_path / "exec")
        monkeypatch.setattr(pe_mod, "EXEC_DB", tmp_path / "exec" / "executions.db")
        prev_active = server_mod.get_active_session_id()
        server_mod._reset_server_state()
        reset_default_registry()
        yield
        server_mod._reset_server_state()
        server_mod.set_active_session_id(prev_active)
        reset_default_registry()

    @pytest.mark.asyncio
    async def test_auto_rollback_false_skips_rollback_and_keeps_artifacts_changed(self) -> None:
        plan_res = await server_mod.plan_change(
            "safe_rename",
            options={
                "old_path": "Sales[Old]",
                "new_path": "Sales[New]",
                "auto_rollback": False,
            },
        )
        plan_id = plan_res.plan_id
        loaded = server_mod._plan_store.get(plan_id)
        assert loaded is not None
        assert loaded.auto_rollback is False

        outcomes = {
            f"{plan_id}:s1": StepOutcome(success=True, changed_files=["snap.tar"]),
            f"{plan_id}:s2": StepOutcome(success=True, changed_files=["model.tmdl"]),
            f"{plan_id}:s3": StepOutcome(
                success=False, error_message="report error", changed_files=[]
            ),
        }
        calls = _register_scripted(outcomes)

        apply_res = await server_mod.apply_plan(plan_id, dry_run=False)
        assert apply_res.result == "failed"
        assert apply_res.rollback_steps_executed == []
        assert apply_res.artifacts_changed == ["snap.tar", "model.tmdl"]
        assert f"{plan_id}:s2-revert" not in calls

    @pytest.mark.asyncio
    async def test_artifacts_changed_populated_on_success_and_cleared_on_clean_rollback(
        self,
    ) -> None:
        plan_res = await server_mod.plan_change(
            "safe_rename",
            options={"old_path": "Sales[Old]", "new_path": "Sales[New]"},
        )
        plan_id = plan_res.plan_id

        ok_outcomes = {
            f"{plan_id}:s1": StepOutcome(success=True, changed_files=[]),
            f"{plan_id}:s2": StepOutcome(success=True, changed_files=["Sales.tmdl"]),
            f"{plan_id}:s3": StepOutcome(success=True, changed_files=["page.json", "Sales.tmdl"]),
            f"{plan_id}:s4": StepOutcome(success=True, changed_files=[]),
        }
        _register_scripted(ok_outcomes)

        res_ok = await server_mod.apply_plan(plan_id, dry_run=False)
        assert res_ok.result == "success"
        assert res_ok.artifacts_changed == ["Sales.tmdl", "page.json"]

        fail_outcomes = {
            f"{plan_id}:s1": StepOutcome(success=True, changed_files=[]),
            f"{plan_id}:s2": StepOutcome(success=True, changed_files=["Sales.tmdl"]),
            f"{plan_id}:s3": StepOutcome(success=False, error_message="boom"),
            f"{plan_id}:s2-revert": StepOutcome(success=True, changed_files=["Sales.tmdl"]),
        }
        _register_scripted(fail_outcomes)

        res_rb = await server_mod.apply_plan(plan_id, dry_run=False)
        assert res_rb.result == "rolled_back"
        assert res_rb.artifacts_changed == []
        assert all(
            entry["status"] != "not_revertible" or entry["id"] != f"{plan_id}:s3"
            for entry in res_rb.rollback_steps_executed
        )


class TestOrphanExecutionReconciliation:
    @pytest.fixture(autouse=True)
    def _isolated_stores(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
        monkeypatch.setattr(ps_mod, "PLAN_DIR", tmp_path / "plans")
        monkeypatch.setattr(ps_mod, "PLAN_DB", tmp_path / "plans" / "plans.db")
        monkeypatch.setattr(
            server_mod, "_plan_store", PlanStore(db_path=tmp_path / "plans" / "plans.db")
        )
        monkeypatch.setattr(pe_mod, "EXEC_DIR", tmp_path / "exec")
        monkeypatch.setattr(pe_mod, "EXEC_DB", tmp_path / "exec" / "executions.db")
        prev_active = server_mod.get_active_session_id()
        server_mod._reset_server_state()
        reset_default_registry()
        yield
        server_mod._reset_server_state()
        server_mod.set_active_session_id(prev_active)
        reset_default_registry()

    def _seed_orphan(self, plan_id: str) -> PlanExecution:
        store = PlanExecutionStore()
        now = datetime.now(UTC).isoformat()
        ex = PlanExecution(
            plan_id=plan_id,
            plan_yaml="plan_id: test\n",
            state=PlanExecutionState.ORPHANED,
            current_step_index=1,
            started_at=now,
            last_heartbeat_at=now,
        )
        store.create(ex)
        return ex

    @pytest.mark.asyncio
    async def test_apply_plan_fails_closed_when_orphan_exists_and_no_ctx(self) -> None:
        plan_res = await server_mod.plan_change(
            "audit", options={"target": "/tmp/x", "checks": ["bpa"]}
        )
        orphan = self._seed_orphan(plan_res.plan_id)

        res = await server_mod.apply_plan(plan_res.plan_id, dry_run=False, ctx=None)
        assert res.result == "failed"
        assert orphan.execution_id in (res.failed_step or {}).get("error_message", "")

    @pytest.mark.asyncio
    async def test_apply_plan_abort_choice_stops_execution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan_res = await server_mod.plan_change(
            "audit", options={"target": "/tmp/x", "checks": ["bpa"]}
        )
        self._seed_orphan(plan_res.plan_id)

        async def _fake_elicit(*args: Any, **kwargs: Any) -> ElicitationResponse:
            return ElicitationResponse(accepted=True, values={"response": "abort"})

        monkeypatch.setattr(server_mod, "_elicit", _fake_elicit)
        res = await server_mod.apply_plan(
            plan_res.plan_id,
            dry_run=False,
            ctx=object(),  # type: ignore[arg-type]
        )
        assert res.result == "stopped_by_user"
        assert res.executed_steps == []

    @pytest.mark.asyncio
    async def test_apply_plan_rollback_choice_rolls_back_orphan_and_continues(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan_res = await server_mod.plan_change(
            "safe_rename",
            options={"old_path": "Sales[Old]", "new_path": "Sales[New]"},
        )
        orphan = self._seed_orphan(plan_res.plan_id)

        _register_scripted({})

        async def _fake_elicit(*args: Any, **kwargs: Any) -> ElicitationResponse:
            return ElicitationResponse(accepted=True, values={"response": "rollback"})

        monkeypatch.setattr(server_mod, "_elicit", _fake_elicit)
        res = await server_mod.apply_plan(
            plan_res.plan_id,
            dry_run=False,
            ctx=object(),  # type: ignore[arg-type]
        )
        assert res.result == "success"
        updated_orphan = PlanExecutionStore().get(orphan.execution_id)
        assert updated_orphan is not None
        assert updated_orphan.state == PlanExecutionState.ROLLED_BACK


class TestAuditAnchorBoundToDbPath:
    def test_custom_db_path_uses_its_own_anchor_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PBI_ORCHESTRATOR_AUDIT_SECRET", "test-secret")
        global_dir = tmp_path / "global_audit"
        custom_dir = tmp_path / "custom_audit"
        monkeypatch.setattr(audit_mod, "AUDIT_DIR", global_dir)
        monkeypatch.setattr(audit_mod, "AUDIT_DB", global_dir / "audit.db")

        custom_db = custom_dir / "custom.db"
        log = AuditLog(db_path=custom_db)
        log.insert(tool_name="t1", tool_args={"x": 1}, result_status="success")

        assert (custom_dir / ".audit_anchor").exists()
        assert not (global_dir / ".audit_anchor").exists()
        assert log.verify().valid is True


class TestExecutionStorePermissionsAndHealth:
    @posix_only
    def test_execution_store_sets_private_permissions(self, tmp_path: Path) -> None:
        db_path = tmp_path / "private_exec" / "executions.db"
        store = PlanExecutionStore(db_path=db_path)
        now = datetime.now(UTC).isoformat()
        store.create(
            PlanExecution(
                plan_id="p1",
                plan_yaml="id: p1",
                started_at=now,
                last_heartbeat_at=now,
            )
        )
        dir_mode = stat.S_IMODE(db_path.parent.stat().st_mode)
        file_mode = stat.S_IMODE(db_path.stat().st_mode)
        assert dir_mode == 0o700
        assert file_mode == 0o600

    @pytest.mark.asyncio
    async def test_health_does_not_warn_or_create_db_when_exec_db_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ph_mod = sys.modules["powerbi_orchestrator_mcp.tools.powerbi_health"]

        missing_exec_db = tmp_path / "nonexistent_dir" / "executions.db"
        monkeypatch.setattr(ph_mod, "EXEC_DB", missing_exec_db)

        snapshot = await powerbi_health(include_engine_details=False)
        assert snapshot["execution_count"] == 0
        assert not missing_exec_db.exists()
        assert not any("execution store unavailable" in w for w in snapshot["warnings"])


class TestPlanStoreChronoOwnerAndCount:
    def test_list_all_orders_by_timestamp_not_random_plan_id(self, tmp_path: Path) -> None:
        store = PlanStore(db_path=tmp_path / "plans.db")
        first = Plan(id="plan_2026-11-09_zzzzzzzz", yaml="id: z", steps=[])
        second = Plan(id="plan_2026-11-09_aaaaaaaa", yaml="id: a", steps=[])
        store.put(first)
        store.put(second)

        listed = store.list_all()
        assert [p.id for p in listed] == [
            "plan_2026-11-09_aaaaaaaa",
            "plan_2026-11-09_zzzzzzzz",
        ]

    def test_put_refuses_to_overwrite_plan_owned_by_another_principal(self, tmp_path: Path) -> None:
        store = PlanStore(db_path=tmp_path / "plans.db")
        plan = Plan(id="shared-id", yaml="id: 1", steps=[])
        store.put(plan, owner="alice")

        with pytest.raises(PermissionError, match="belongs to another"):
            store.put(plan, owner="mallory")

    def test_count_in_stdio_mode_is_scoped_to_local_identity(self, tmp_path: Path) -> None:
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user(None)
        store = PlanStore(db_path=tmp_path / "plans.db")
        store.put(Plan(id="local-1", yaml="id: l1", steps=[]))
        store.put(Plan(id="remote-1", yaml="id: r1", steps=[]), owner="alice")

        assert store.count() == 1
        assert store.count(owner="alice") == 1


class TestCliWorkspaceBoundaries:
    def test_inspect_rejects_path_outside_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside.pbip"
        outside.mkdir()
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(workspace))

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = cli.main(["inspect", str(outside)])
        assert rc == 2
        assert "outside allowed workspace root" in err_buf.getvalue()

    def test_init_rejects_path_outside_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside" / "new.pbip"
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(workspace))

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = cli.main(["init", str(outside)])
        assert rc == 2
        assert "outside allowed workspace root" in err_buf.getvalue()


class TestReadinessAndStartupWarnings:
    def test_read_boundary_is_ok_when_workspace_root_overrides_opt_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(WORKSPACE_ROOT_ENV, str(tmp_path))
        monkeypatch.setenv(READ_OPT_OUT_ENV, "1")

        findings = {f.name: f for f in collect_readiness()}
        assert findings["read_boundary"].ok is True
        assert WORKSPACE_ROOT_ENV in findings["read_boundary"].detail

    def test_main_with_http_cli_arg_sets_pbi_transport_before_startup_warnings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from powerbi_orchestrator_mcp.orchestrator import transport as transport_mod

        monkeypatch.delenv("PBI_TRANSPORT", raising=False)
        monkeypatch.setattr(sys, "argv", ["powerbi-orchestrator-mcp", "--transport", "http"])
        monkeypatch.setattr(
            transport_mod,
            "parse_transport_args",
            lambda: ("http", None),
        )

        observed_transport: list[str | None] = []

        def _spy_warnings(*, transport: str | None = None) -> None:
            observed_transport.append(transport)

        monkeypatch.setattr(server_mod, "_log_startup_warnings", _spy_warnings)
        with pytest.raises(SystemExit) as excinfo:
            server_mod.main()
        assert excinfo.value.code == 2
        assert observed_transport == ["http"]


class TestPlanBuilderAutoRollbackOption:
    def test_plan_builder_propagates_auto_rollback_option(self) -> None:
        builder = PlanBuilder()
        plan = builder.build_audit(
            target="/tmp/pbip",
            checks=["bpa"],
            options=PlanOptions(auto_rollback=False),
        )
        assert plan.auto_rollback is False
