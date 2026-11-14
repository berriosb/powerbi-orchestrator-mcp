"""Enterprise-readiness hardening tests across security, ops, HTTP probes, and async adapters."""

from __future__ import annotations

import json
import os
import smtplib
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from powerbi_orchestrator_mcp import __version__
from powerbi_orchestrator_mcp.cloud.auth import AuthConfig, FabricCredential
from powerbi_orchestrator_mcp.cloud.fabric_client import (
    FabricClient,
    reset_shared_fabric_resilience,
)
from powerbi_orchestrator_mcp.engines.base import ConnectionHandle
from powerbi_orchestrator_mcp.engines.errors import EngineError
from powerbi_orchestrator_mcp.engines.modeling_mcp import PowerBiModelingMcpEngine
from powerbi_orchestrator_mcp.orchestrator import server as server_mod
from powerbi_orchestrator_mcp.orchestrator.audit import AuditLog
from powerbi_orchestrator_mcp.orchestrator.authorization import (
    SCOPE_READ,
    ReadOnlyModeError,
    ToolAuthorizationError,
)
from powerbi_orchestrator_mcp.orchestrator.context import SessionContext, SessionStore
from powerbi_orchestrator_mcp.orchestrator.iso_handoff import export_audit_bundle
from powerbi_orchestrator_mcp.orchestrator.report_runner import (
    ReportEmailConfig,
    ReportJobSpec,
    run_scheduled_report,
)
from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user
from powerbi_orchestrator_mcp.tools.define_kpi_catalog import define_kpi_catalog
from powerbi_orchestrator_mcp.tools.promote_in_pipeline import (
    QualityGate,
    promote_in_pipeline_async,
)
from powerbi_orchestrator_mcp.tools.send_report_email import send_report_email
from powerbi_orchestrator_mcp.tools.set_sensitivity_labels import (
    LabelTarget,
    set_sensitivity_labels_async,
)
from powerbi_orchestrator_mcp.tools.sync_git_to_workspace import sync_git_to_workspace_async


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from powerbi_orchestrator_mcp.orchestrator import audit as audit_mod
    from powerbi_orchestrator_mcp.orchestrator import context as ctx_mod

    home = tmp_path / "pbi_home"
    home.mkdir(parents=True)
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    audit_dir = home / "audit"
    audit_dir.mkdir(parents=True)
    sessions_dir = home / "sessions"
    sessions_dir.mkdir(parents=True)

    monkeypatch.setenv("PBI_ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("PBI_ORCHESTRATOR_AUDIT_SECRET", "enterprise-hardening-secret")
    monkeypatch.setenv("PBI_EMAIL_ALLOWED_DOMAINS", "quadlogic.cl")
    monkeypatch.setenv("PBI_EMAIL_INTERNAL_DOMAINS", "quadlogic.cl")
    monkeypatch.setattr(audit_mod, "AUDIT_DIR", audit_dir)
    monkeypatch.setattr(audit_mod, "AUDIT_DB", audit_dir / "audit.db")
    monkeypatch.setattr(ctx_mod, "SESSIONS_DIR", sessions_dir)
    set_current_user(None)
    server_mod._reset_server_state()
    yield
    set_current_user(None)
    server_mod._reset_server_state()
    reset_shared_fabric_resilience()


class TestRbacAndAuditHardening:
    @pytest.mark.asyncio
    async def test_screenshot_requires_write_scope_and_respects_readonly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pbip = tmp_path / "ws" / "Model.pbip"
        pbip.mkdir(parents=True)

        set_current_user({"scp": SCOPE_READ})
        with pytest.raises(ToolAuthorizationError, match="Tools.Write"):
            await server_mod.screenshot_report_pages(
                pbip_path=str(pbip),
                output_dir=str(tmp_path / "ws" / "shots"),
            )

        set_current_user({"scp": "Tools.Read Tools.Write"})
        monkeypatch.setenv("PBI_READONLY", "1")
        with pytest.raises(ReadOnlyModeError):
            await server_mod.screenshot_report_pages(
                pbip_path=str(pbip),
                output_dir=str(tmp_path / "ws" / "shots"),
            )

    @pytest.mark.asyncio
    async def test_plan_change_records_audited_entry(self) -> None:
        res = await server_mod.plan_change(
            "safe_rename",
            options={"old_path": "Sales[Old]", "new_path": "Sales[New]"},
        )
        assert res.plan_id.startswith("plan_")

        audit = AuditLog()
        conn = audit._connect()
        try:
            rows = conn.execute("SELECT tool_name, result_status FROM audit_log").fetchall()
        finally:
            conn.close()
        assert ("cloud:plan_change", "attempted") in rows
        assert ("cloud:plan_change", "success") in rows
        assert audit.verify().valid is True


class TestEmailSecurityHardening:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_sender",
        [
            "bot/../../admin@quadlogic.cl",
            "bot?extra=1@quadlogic.cl",
            "bot#frag@quadlogic.cl",
            "bot%2f@quadlogic.cl",
            "a..b@quadlogic.cl",
        ],
    )
    async def test_rejects_malformed_or_path_injectable_sender(self, bad_sender: str) -> None:
        res = await send_report_email(
            recipients_to=["ops@quadlogic.cl"],
            subject="Daily KPI",
            body_markdown="Body",
            sender_address=bad_sender,
            transport="graph",
            dry_run=False,
        )
        assert res.status == "blocked"
        assert "sender_address" in res.blocked_reasons[0]

    @pytest.mark.asyncio
    async def test_rejects_subject_crlf_injection(self) -> None:
        res = await send_report_email(
            recipients_to=["ops@quadlogic.cl"],
            subject="Report\r\nBcc: evil@outside.com",
            body_markdown="Body",
            sender_address="bot@quadlogic.cl",
            dry_run=True,
        )
        assert res.status == "blocked"
        assert "CR/LF" in res.blocked_reasons[0]

    @pytest.mark.asyncio
    async def test_graph_token_acquired_off_event_loop_and_sender_encoded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import azure.identity

        monkeypatch.delenv("PBI_GRAPH_ACCESS_TOKEN", raising=False)
        caller_thread = threading.get_ident()
        token_threads: list[int] = []
        posted_urls: list[str] = []

        class _Token:
            token = "graph-thread-token"

        class _FakeCred:
            def get_token(self, _scope: str) -> _Token:
                token_threads.append(threading.get_ident())
                return _Token()

        class _FakeGraph:
            async def post(self, url: str, **_kwargs: Any) -> Any:
                posted_urls.append(url)
                return type("_Resp", (), {"status_code": 202, "headers": {"request-id": "g-1"}})()

        monkeypatch.setattr(azure.identity, "DefaultAzureCredential", _FakeCred)

        res = await send_report_email(
            recipients_to=["ops@quadlogic.cl"],
            subject="Clean",
            body_markdown="OK",
            sender_address="bot+kpi@quadlogic.cl",
            transport="graph",
            dry_run=False,
            graph_client=_FakeGraph(),
        )
        assert res.status == "sent"
        assert res.message_id == "g-1"
        assert token_threads and token_threads[0] != caller_thread
        assert posted_urls == [
            "https://graph.microsoft.com/v1.0/users/bot+kpi@quadlogic.cl/sendMail"
        ]

    @pytest.mark.asyncio
    async def test_smtp_refuses_starttls_downgrade_and_supports_ssl_465(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _NoTlsSmtp:
            def __init__(self, host: str, port: int, timeout: int = 20) -> None:
                self.host = host
                self.port = port

            def __enter__(self) -> _NoTlsSmtp:
                return self

            def __exit__(self, *_args: Any) -> None:
                return None

            def ehlo(self) -> None:
                return None

            def has_extn(self, _name: str) -> bool:
                return False

            def login(self, _u: str, _p: str) -> None:  # pragma: no cover - must not be reached
                raise AssertionError("cleartext login must not execute")

            def send_message(self, _msg: Any) -> None:  # pragma: no cover
                raise AssertionError("cleartext send must not execute")

        monkeypatch.setenv("PBI_SMTP_HOST", "smtp.quadlogic.cl")
        monkeypatch.setenv("PBI_SMTP_PORT", "587")
        monkeypatch.setenv("PBI_SMTP_USER", "bot@quadlogic.cl")
        monkeypatch.setenv("PBI_SMTP_PASSWORD", "secret")
        monkeypatch.setattr(smtplib, "SMTP", _NoTlsSmtp)

        downgraded = await send_report_email(
            recipients_to=["ops@quadlogic.cl"],
            subject="No TLS",
            body_markdown="Body",
            sender_address="bot@quadlogic.cl",
            transport="smtp",
            dry_run=False,
        )
        assert downgraded.status == "failed"
        assert "STARTTLS" in downgraded.blocked_reasons[0]

        ssl_calls: list[tuple[str, int]] = []

        class _FakeSmtpSsl:
            def __init__(
                self, host: str, port: int, timeout: int = 20, context: Any = None
            ) -> None:
                ssl_calls.append((host, port))
                assert context is not None

            def __enter__(self) -> _FakeSmtpSsl:
                return self

            def __exit__(self, *_args: Any) -> None:
                return None

            def ehlo(self) -> None:
                return None

            def login(self, _u: str, _p: str) -> None:
                return None

            def send_message(self, _msg: Any) -> None:
                return None

        monkeypatch.setenv("PBI_SMTP_PORT", "465")
        monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSmtpSsl)
        ssl_ok = await send_report_email(
            recipients_to=["ops@quadlogic.cl"],
            subject="SSL 465",
            body_markdown="Body",
            sender_address="bot@quadlogic.cl",
            transport="smtp",
            dry_run=False,
        )
        assert ssl_ok.status == "sent"
        assert ssl_calls == [("smtp.quadlogic.cl", 465)]


class TestReportRunnerAndAuditBackup:
    @pytest.mark.asyncio
    async def test_report_runner_fails_closed_when_audit_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = tmp_path / "ws"
        cat = ws / "cat.yaml"
        await define_kpi_catalog(
            catalog_path=str(cat),
            kpis=[
                {
                    "id": "k1",
                    "name": "K1",
                    "area": "Ops",
                    "unit": "count",
                    "formula_dax": "COUNTROWS(Control)",
                    "python_rule": "count_where",
                    "rule_params": {"column": "estado", "equals": ["ok"]},
                    "owner": "Ops",
                    "source_table": "control",
                    "periodicity": "daily",
                }
            ],
            dry_run=False,
        )
        src = ws / "src.csv"
        src.write_text("id,estado\n1,ok\n2,ok\n", encoding="utf-8")

        job = ReportJobSpec(
            report_title="Daily Report",
            catalog_path=str(cat),
            source_path=str(src),
            output_dir=str(ws / "out"),
            email=ReportEmailConfig(
                recipients_to=["ops@quadlogic.cl"],
                sender_address="bot@quadlogic.cl",
            ),
        )
        monkeypatch.setenv("PBI_AUDIT_FAIL_CLOSED", "1")
        monkeypatch.setattr(
            "powerbi_orchestrator_mcp.cloud.audit_cloud.CloudAuditLog.record",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
        )

        with pytest.raises(OSError, match="disk full"):
            await run_scheduled_report(job, as_of_date_override="2026-03-10", live=False)

    @pytest.mark.asyncio
    async def test_report_runner_records_write_ahead_attempt_before_email(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = tmp_path / "ws"
        cat = ws / "cat.yaml"
        await define_kpi_catalog(
            catalog_path=str(cat),
            kpis=[
                {
                    "id": "k1",
                    "name": "K1",
                    "area": "Ops",
                    "unit": "count",
                    "formula_dax": "COUNTROWS(Control)",
                    "python_rule": "count_where",
                    "rule_params": {"column": "estado", "equals": ["ok"]},
                    "owner": "Ops",
                    "source_table": "control",
                    "periodicity": "daily",
                }
            ],
            dry_run=False,
        )
        src = ws / "src.csv"
        src.write_text("id,estado\n1,ok\n2,ok\n", encoding="utf-8")
        recorded: list[str] = []
        from powerbi_orchestrator_mcp.cloud.audit_cloud import CloudAuditLog

        orig_record = CloudAuditLog.record

        def _spy_record(self: CloudAuditLog, **kwargs: Any) -> Any:
            recorded.append(str(kwargs.get("result_status")))
            return orig_record(self, **kwargs)

        monkeypatch.setattr(CloudAuditLog, "record", _spy_record)
        job = ReportJobSpec(
            report_title="Daily Report",
            catalog_path=str(cat),
            source_path=str(src),
            output_dir=str(ws / "out"),
            email=ReportEmailConfig(
                recipients_to=["ops@quadlogic.cl"],
                sender_address="bot@quadlogic.cl",
            ),
        )
        res = await run_scheduled_report(job, as_of_date_override="2026-03-10", live=False)
        assert res.status == "ok"
        assert recorded == ["attempted", "success"]

    def test_export_audit_bundle_uses_sqlite_backup_for_wal_consistency(
        self, tmp_path: Path
    ) -> None:
        home = Path(os.environ["PBI_ORCHESTRATOR_HOME"])
        audit_dir = home / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        db_path = audit_dir / "audit.db"
        audit = AuditLog(db_path=db_path)
        audit.insert(tool_name="t1", tool_args={"a": 1}, result_status="success")

        # Keep an active reader/writer open in WAL mode while another entry is inserted.
        keeper = sqlite3.connect(str(db_path))
        try:
            keeper.execute("BEGIN")
            keeper.execute("SELECT COUNT(*) FROM audit_log").fetchone()
            audit.insert(tool_name="t2", tool_args={"a": 2}, result_status="success")

            out_dir = tmp_path / "ws" / "bundle"
            manifest = export_audit_bundle(str(out_dir), rotate=False)
            assert "audit/audit.db" in manifest["files"]

            backed_up = AuditLog(db_path=out_dir / "audit" / "audit.db")
            bconn = backed_up._connect()
            try:
                rows = [
                    r[0]
                    for r in bconn.execute(
                        "SELECT tool_name FROM audit_log ORDER BY id DESC"
                    ).fetchall()
                ]
            finally:
                bconn.close()
            assert rows == ["t2", "t1"]
        finally:
            keeper.close()

    def test_session_store_prune_expired_removes_stale_sessions(self) -> None:
        store = SessionStore()
        fresh = store.create(SessionContext())
        stale = store.create(SessionContext())

        from powerbi_orchestrator_mcp.orchestrator import context as ctx_mod

        stale_db = ctx_mod.SESSIONS_DIR / f"{stale.session_id}.db"
        old_time = time.time() - (72 * 3600)
        os.utime(stale_db, (old_time, old_time))

        removed = store.prune_expired(max_age_hours=24.0)
        assert removed == 1
        assert store.get(stale.session_id) is None
        assert store.get(fresh.session_id) is not None


class TestSharedResilienceAndHttpProbes:
    def test_fabric_client_shares_bucket_and_breaker_per_credential_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = AuthConfig(mode="interactive", tenant_id="t-1", client_id="c-1")
        monkeypatch.setattr(FabricCredential, "_build_credential", lambda self: object())
        c1 = FabricClient(FabricCredential(cfg))
        c2 = FabricClient(FabricCredential(cfg))
        c3 = FabricClient(FabricCredential(cfg), shared_resilience=False)

        assert c1._bucket is c2._bucket
        assert c1._breaker is c2._breaker
        assert c3._bucket is not c1._bucket

    @pytest.mark.asyncio
    async def test_build_http_app_exposes_healthz_and_readyz_without_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _inner_app(scope: Any, receive: Any, send: Any) -> None:
            del scope, receive
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"inner"})

        def _deny_all(_auth: str | None) -> dict[str, object]:
            from powerbi_orchestrator_mcp.orchestrator.transport import AuthError

            raise AuthError(401, "missing token")

        app = server_mod.build_http_app(_inner_app, _deny_all)

        async def _call(method: str, path: str) -> tuple[int, bytes]:
            msgs: list[dict[str, Any]] = []

            async def _recv() -> dict[str, Any]:
                return {"type": "http.request", "body": b"", "more_body": False}

            async def _send(msg: dict[str, Any]) -> None:
                msgs.append(msg)

            await app(
                {"type": "http", "method": method, "path": path, "headers": []},
                _recv,
                _send,
            )
            status = int(msgs[0]["status"])
            body = b"".join(m.get("body", b"") for m in msgs if m["type"] == "http.response.body")
            return status, body

        status, body = await _call("GET", "/healthz")
        assert status == 200
        data = json.loads(body)
        assert data["status"] == "ok"
        assert data["version"] == __version__

        head_status, head_body = await _call("HEAD", "/healthz")
        assert head_status == 200
        assert head_body == b""

        # Make readiness fail (strict compliance without email allowlist or auth)
        monkeypatch.setenv("PBI_STRICT_COMPLIANCE", "1")
        monkeypatch.delenv("PBI_EMAIL_ALLOWED_DOMAINS", raising=False)
        r_status, r_body = await _call("GET", "/readyz")
        assert r_status == 503
        r_data = json.loads(r_body)
        assert r_data["status"] == "not_ready"
        assert "strict_compliance" in r_data["failed_checks"]

        # Unauthenticated /mcp MUST still be blocked by EntraAuthMiddleware
        mcp_status, _ = await _call("POST", "/mcp")
        assert mcp_status == 401


class TestUncoveredAdapterBranches:
    @pytest.mark.asyncio
    async def test_modeling_mcp_protocol_mappings_and_error_extraction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = ConnectionHandle(
            target_type="pbix_desktop",
            target_ref="127.0.0.1:1234",
            session_token="sess-1",
            engine="modeling_mcp",
        )
        adapter = PowerBiModelingMcpEngine(binary="npx")

        assert (
            adapter._build_mcp_call("database_operations/list_tables", conn, None)[1]["request"][
                "operation"
            ]
            == "List"
        )
        assert adapter._build_mcp_call("column_operations/list", conn, {"table": "Sales"})[1][
            "request"
        ]["filter"] == {"tableNames": ["Sales"]}
        rename_call = adapter._build_mcp_call(
            "column_operations/update",
            conn,
            {"table": "Sales", "column": "Amt", "changes": {"new_name": "Amount"}},
        )
        assert rename_call[1]["request"]["operation"] == "Rename"
        upd_col = adapter._build_mcp_call(
            "column_operations/update",
            conn,
            {"table": "Sales", "column": "Amt", "changes": {"formatString": "#,0"}},
        )
        assert upd_col[1]["request"]["operation"] == "Update"
        assert (
            adapter._build_mcp_call("measure_operations/list", conn, None)[1]["request"][
                "operation"
            ]
            == "List"
        )
        assert (
            adapter._build_mcp_call(
                "measure_operations/create",
                conn,
                {"table": "Sales", "measure": {"name": "M1", "expression": "1"}},
            )[1]["request"]["operation"]
            == "Create"
        )
        assert (
            adapter._build_mcp_call(
                "measure_operations/update",
                conn,
                {"table": "Sales", "measure": "M1", "changes": {"expression": "2"}},
            )[1]["request"]["operation"]
            == "Update"
        )
        assert (
            adapter._build_mcp_call(
                "measure_operations/delete",
                conn,
                {"table": "Sales", "measure": "M1"},
            )[1]["request"]["operation"]
            == "Delete"
        )
        assert (
            adapter._build_mcp_call("database_operations/list_relationships", conn, None)[1][
                "request"
            ]["operation"]
            == "List"
        )
        dax_req = adapter._build_mcp_call(
            "dax_query_operations/run",
            conn,
            {"query": "EVALUATE {1}", "effective_identity": {"username": "u@q.cl"}},
        )[1]["request"]
        assert dax_req["impersonation"] == {"username": "u@q.cl"}
        assert (
            adapter._build_mcp_call("database_operations/export_tmdl", conn, {"label": "snap"})[1][
                "request"
            ]["tmdlFolderPath"]
            == "snap"
        )
        assert (
            adapter._build_mcp_call("database_operations/import_tmdl", conn, {"path": "/p"})[1][
                "request"
            ]["tmdlFolderPath"]
            == "/p"
        )

        # Verify _dispatch parses JSON content and raises EngineError on isError=True
        async def _rpc_ok(_m: str, _p: dict[str, Any]) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": json.dumps({"tables": ["Sales"]})}]}

        monkeypatch.setattr(adapter, "_rpc", _rpc_ok)
        parsed = await adapter._dispatch("database_operations", "list_tables", conn)
        assert parsed == {"tables": ["Sales"]}

        async def _rpc_err(_m: str, _p: dict[str, Any]) -> dict[str, Any]:
            return {"isError": True, "content": [{"type": "text", "text": "DAX syntax error"}]}

        monkeypatch.setattr(adapter, "_rpc", _rpc_err)
        with pytest.raises(EngineError, match="DAX syntax error"):
            await adapter._dispatch("dax_query_operations", "run", conn, extra={"query": "BAD"})

    @pytest.mark.asyncio
    async def test_promote_sensitivity_and_git_async_paths(self, tmp_path: Path) -> None:
        # promote_in_pipeline_async: invalid/same stages, async client deploy + failure
        bad_stage = await promote_in_pipeline_async("p1", "invalid", "prod")
        assert "invalid stages" in bad_stage.warnings[0]
        same_stage = await promote_in_pipeline_async("p1", "dev", "dev")
        assert "must differ" in same_stage.warnings[0]

        class _AsyncPromoClient:
            async def list_pipeline_items(self, _p: str, _s: str) -> list[str]:
                return ["item_ok", "item_fail"]

            async def deploy_pipeline_item(self, *, item_id: str, **_kw: Any) -> None:
                if item_id == "item_fail":
                    raise RuntimeError("conflict")

        gate_fail = await promote_in_pipeline_async(
            "p1",
            "dev",
            "test",
            quality_gates=[QualityGate(type="audit_model_and_report", blocking=True)],
            custom_gates=[("c1", lambda: True)],
            audit_logger=lambda **_k: (_ for _ in ()).throw(RuntimeError("log err")),
        )
        assert gate_fail.failed_gate is not None
        assert "audit_logger raised" in gate_fail.warnings[0]

        promo_res = await promote_in_pipeline_async(
            "p1",
            "dev",
            "test",
            dry_run=False,
            fabric_client=_AsyncPromoClient(),
            quality_gates=[QualityGate(type="pre_deploy_check", blocking=True)],
            pre_deploy_findings=[{"rule": "DAX-01", "severity": "info", "message": "ok"}],
        )
        assert [i.item_id for i in promo_res.promoted_items] == ["item_ok"]
        assert any("failed to promote item_fail" in w for w in promo_res.warnings)

        # set_sensitivity_labels_async: 403 and generic error handling
        class _Admin403:
            async def admin_bulk_set_labels(self, **_kw: Any) -> dict[str, Any]:
                raise RuntimeError("403 Forbidden")

        res_403 = await set_sensitivity_labels_async(
            items=[LabelTarget(item_id="00000000-0000-0000-0000-000000000001")],
            label_id="22222222-2222-2222-2222-222222222222",
            label_name="Confidential",
            tenant_id="11111111-1111-1111-1111-111111111111",
            admin_scopes=["InformationProtectionPolicy.Apply.All"],
            dry_run=False,
            fabric_admin_client=_Admin403(),
            admin_token="tok",
        )
        assert "403 Forbidden" in res_403.warnings[0]

        class _AdminErr:
            async def admin_bulk_set_labels(self, **_kw: Any) -> dict[str, Any]:
                raise RuntimeError("upstream timeout")

        res_err = await set_sensitivity_labels_async(
            items=[LabelTarget(item_id="00000000-0000-0000-0000-000000000001")],
            label_id="22222222-2222-2222-2222-222222222222",
            label_name="Confidential",
            tenant_id="11111111-1111-1111-1111-111111111111",
            admin_scopes=["InformationProtectionPolicy.Apply.All"],
            dry_run=False,
            fabric_admin_client=_AdminErr(),
            admin_token="tok",
        )
        assert "bulk_set_labels failed" in res_err.warnings[0]

        class _AdminOk:
            async def admin_bulk_set_labels(self, **_kw: Any) -> dict[str, Any]:
                return {"status": "succeeded"}

        res_ok = await set_sensitivity_labels_async(
            items=[LabelTarget(item_id="00000000-0000-0000-0000-000000000001")],
            label_id="22222222-2222-2222-2222-222222222222",
            label_name="Confidential",
            tenant_id="11111111-1111-1111-1111-111111111111",
            admin_scopes=["InformationProtectionPolicy.Apply.All"],
            dry_run=False,
            fabric_admin_client=_AdminOk(),
            admin_token="tok",
        )
        assert res_ok.bulk_set_calls == 1
        assert res_ok.applied[0].status == "applied"

        # sync_git_to_workspace_async: list failure & apply partial failure
        import subprocess

        repo = tmp_path / "ws" / "repo"
        model_pbip = repo / "Dataset" / "SalesModel.pbip"
        model_pbip.parent.mkdir(parents=True)
        model_pbip.write_text('{"version": "1.0"}', encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "ci@quadlogic.cl"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "CI"],
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True
        )

        class _SyncClientListFail:
            async def list_workspace_items(self, _ws: str) -> list[dict[str, Any]]:
                raise RuntimeError("list 403")

        list_fail = await sync_git_to_workspace_async(
            repo_path=str(repo),
            workspace_id="ws-1",
            fabric_client=_SyncClientListFail(),
            dry_run=True,
        )
        assert any("could not list workspace items" in w for w in list_fail.warnings)

        class _SyncClientFailApply:
            async def list_workspace_items(self, _ws: str) -> list[dict[str, Any]]:
                return []

            async def apply_pbip(self, **_kw: Any) -> None:
                raise RuntimeError("apply rejected")

        sync_res = await sync_git_to_workspace_async(
            repo_path=str(repo),
            workspace_id="ws-1",
            fabric_client=_SyncClientFailApply(),
            dry_run=False,
        )
        assert sync_res.items_deployed == []
        assert any(
            i.status == "failed" and i.reason == "apply rejected" for i in sync_res.items_skipped
        )
