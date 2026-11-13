"""Phase 1 readiness & hardening tests (Cloud REST fixes, async bridge, strict compliance)."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from powerbi_orchestrator_mcp.cli import main as cli_main
from powerbi_orchestrator_mcp.cloud.fabric_client import FabricClient
from powerbi_orchestrator_mcp.cloud.refresh import RefreshOrchestrator
from powerbi_orchestrator_mcp.cloud.tool_adapters import AsyncFabricBridge
from powerbi_orchestrator_mcp.orchestrator import server as mcp_server
from powerbi_orchestrator_mcp.orchestrator.audit_guard import _fail_closed
from powerbi_orchestrator_mcp.orchestrator.authorization import (
    SCOPE_READ,
    ReadOnlyModeError,
    ToolAuthorizationError,
    required_scope_for_tool,
)
from powerbi_orchestrator_mcp.orchestrator.readiness import collect_readiness
from powerbi_orchestrator_mcp.tools.create_semantic_model_from_schema import (
    create_semantic_model_from_schema,
)
from powerbi_orchestrator_mcp.tools.deploy_to_workspace import deploy_to_workspace
from powerbi_orchestrator_mcp.tools.promote_in_pipeline import promote_in_pipeline


class _LroHttpStub:
    """Minimal httpx-like async stub returning 202 Accepted for create_item / deploy."""

    def __init__(self, responses: list[tuple[int, dict[str, Any], dict[str, str]]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def aclose(self) -> None:
        return None

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        params: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> httpx.Response:
        del headers, params
        self.calls.append((method, url, json))
        body: dict[str, Any]
        resp_headers: dict[str, str]
        if not self._responses:
            status_code, body, resp_headers = 200, {}, {}
        else:
            status_code, body, resp_headers = self._responses.pop(0)

        if body:
            return httpx.Response(status_code, json=body, headers=resp_headers)
        return httpx.Response(status_code, content=b"", headers=resp_headers)


class _DummyCred:
    async def get_token(self, *_scopes: str) -> str:
        return "dummy-bearer-token"


async def test_refresh_orchestrator_uses_pbi_groups_fallback() -> None:
    """Verify _safe_get_history and _safe_cancel target Power BI REST endpoints."""
    client = MagicMock()
    client.get = AsyncMock(return_value={"value": [{"id": "r1", "status": "Completed"}]})
    client.post = AsyncMock(return_value={})
    # Ensure hasattr checks fall through to raw REST path
    del client.get_refresh_history
    del client.cancel_refresh

    orch = RefreshOrchestrator(client)
    history = await orch._safe_get_history("ws-1", "ds-1")
    await orch._safe_cancel("ws-1", "ds-1", "r1")

    assert history == [{"id": "r1", "status": "Completed"}]
    client.get.assert_awaited_once()
    get_args, get_kwargs = client.get.call_args
    assert get_args[0] == "/groups/ws-1/datasets/ds-1/refreshes"
    assert get_kwargs["service"] == "pbi"

    client.post.assert_awaited_once()
    post_args, _ = client.post.call_args
    assert post_args[0] == "/groups/ws-1/datasets/ds-1/refreshes/r1/cancel"


async def test_fabric_client_202_lro_and_deploy_semantic_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify 202 LRO polling and SemanticModel item type on deploy_to_workspace."""
    pbip_dir = tmp_path / "SalesModel.pbip"
    pbip_dir.mkdir()
    (pbip_dir / "model.bim").write_text('{"name": "SalesModel"}', encoding="utf-8")

    stub = _LroHttpStub(
        [
            (
                202,
                {},
                {
                    "x-ms-operation-id": "op-999",
                    "Location": "https://api.fabric.microsoft.com/v1/operations/op-999",
                },
            ),
            (200, {"status": "Succeeded"}, {}),
            (404, {}, {}),  # /operations/op-999/result fallback to list_items
            (
                200,
                {
                    "value": [
                        {
                            "id": "sm-42",
                            "displayName": "SalesModel",
                            "type": "SemanticModel",
                        }
                    ]
                },
                {},
            ),
            (200, {}, {}),  # PATCH schedule
            (202, {}, {"x-ms-request-id": "ref-1"}),  # POST refreshes
        ]
    )
    client = FabricClient(credential=cast(Any, _DummyCred()))
    client._fabric_client = stub  # type: ignore[assignment]
    client._pbi_client = stub  # type: ignore[assignment]

    deploy_mod = importlib.import_module("powerbi_orchestrator_mcp.tools.deploy_to_workspace")
    monkeypatch.setattr(deploy_mod, "FabricCredential", lambda _cfg: _DummyCred())
    monkeypatch.setattr(deploy_mod, "FabricClient", lambda _cred: client)

    res = await deploy_to_workspace(
        pbip_path=str(pbip_dir),
        workspace_id="ws-target",
        findings=[{"rule_id": "AUD-INFO-1", "severity": "info", "message": "audit ok"}],
        dry_run=False,
    )

    assert res.publish_ok is True
    assert res.dataset_id == "sm-42"
    post_urls = [u for m, u, _ in stub.calls if m == "POST"]
    assert any("/workspaces/ws-target/items" in u for u in post_urls)
    first_payload = stub.calls[0][2]
    assert first_payload is not None
    assert first_payload["type"] == "SemanticModel"


async def test_promote_and_create_model_work_inside_active_event_loop(tmp_path: Path) -> None:
    """Verify sync wrappers do not raise RuntimeError under an active asyncio event loop."""
    promo = promote_in_pipeline(
        pipeline_id="pipe-1",
        source_stage="dev",
        target_stage="test",
        dry_run=True,
    )
    assert promo.dry_run is True

    created = create_semantic_model_from_schema(
        spec_json={
            "name": "ControlOps",
            "tables": [{"name": "Control", "columns": [{"name": "id", "type": "string"}]}],
        },
        output_pbip_path=str(tmp_path / "pbip_out"),
        dry_run=True,
    )
    assert created.validation_passed is True
    assert created.tables_created[0].table_name == "Control"


async def test_async_fabric_bridge_snapshot_and_deploy() -> None:
    """Verify AsyncFabricBridge translates high-level ops to FabricClient methods."""
    client = MagicMock(spec=FabricClient)
    client.list_items = AsyncMock(
        return_value=[{"id": "it-1", "displayName": "OpsModel", "type": "SemanticModel"}]
    )
    client.get_item_definition = AsyncMock(return_value={"parts": []})
    client.list_pipeline_stages = AsyncMock(
        return_value=[
            {"order": 0, "id": "stg-dev", "displayName": "Development", "workspaceId": "ws-dev"},
            {"order": 1, "id": "stg-test", "displayName": "Test", "workspaceId": "ws-test"},
        ]
    )
    client.deploy_pipeline = AsyncMock(return_value={"status": "Succeeded", "operationId": "op-1"})

    bridge = AsyncFabricBridge(client)
    snap = await bridge.snapshot_workspace("ws-dev")
    assert "it-1" in snap
    assert snap["it-1"]["name"] == "OpsModel"

    deployed = await bridge.deploy_pipeline_item(
        pipeline_id="pipe-1",
        source_stage="dev",
        target_stage="test",
        item_id="it-1",
        item_type="SemanticModel",
    )
    assert deployed["operationId"] == "op-1"
    client.deploy_pipeline.assert_awaited_once()


async def test_async_fabric_bridge_full_adapter_coverage() -> None:
    """Exercise remaining AsyncFabricBridge paths: apply_pbip, stages, labels, RLS."""
    client = MagicMock(spec=FabricClient)
    client.list_items = AsyncMock(
        return_value=[
            {"id": "dash-1", "name": "ExecDash", "type": "Dashboard"},
            {"id": "sm-1", "displayName": "OpsModel", "type": "SemanticModel"},
        ]
    )

    async def _get_def(_ws: str, item_id: str) -> dict[str, Any]:
        if item_id == "dash-1":
            raise RuntimeError("dashboards do not expose definition")
        return {"parts": [{"path": "model.bim", "payload": "e30=", "payloadType": "InlineBase64"}]}

    client.get_item_definition = AsyncMock(side_effect=_get_def)
    client.update_item_definition = AsyncMock(return_value={"status": "Updated"})
    client.create_item = AsyncMock(return_value={"id": "new-sm", "status": "Created"})
    client.list_pipeline_stages = AsyncMock(
        return_value=[
            {"order": 1, "id": "stg-test", "workspaceId": "ws-test"},
            {"order": 0, "id": "stg-dev", "workspaceId": "ws-dev"},
        ]
    )
    client.admin_set_labels = AsyncMock(return_value={"labels": "applied"})
    client.execute_queries = AsyncMock(
        return_value={"results": [{"tables": [{"rows": [{"c": 7}]}]}]}
    )

    bridge = AsyncFabricBridge(client)

    snap = await bridge.snapshot_workspace("ws-1")
    assert set(snap) == {"dash-1", "sm-1"}
    assert "definition" not in json.loads(snap["dash-1"]["blob"])
    assert "definition" in json.loads(snap["sm-1"]["blob"])

    # Update existing item via definition wrapper JSON
    upd = await bridge.apply_pbip(
        workspace_id="ws-1",
        item_type="SemanticModel",
        item_name="OpsModel",
        blob=json.dumps({"definition": {"parts": []}}),
    )
    assert upd["status"] == "Updated"
    client.update_item_definition.assert_awaited_once()

    # Create new item mapping Dataset -> SemanticModel via raw non-JSON blob
    crt = await bridge.apply_pbip(
        workspace_id="ws-1",
        item_type="Dataset",
        item_name="BrandNewModel",
        blob='{"version": 1}',
    )
    assert crt["id"] == "new-sm"
    assert client.create_item.call_args.kwargs["item_type"] == "SemanticModel"

    # list_pipeline_items valid stage + out-of-range stage
    pipe_items = await bridge.list_pipeline_items("pipe-1", "dev")
    assert pipe_items == ["dash-1", "sm-1"]
    assert await bridge.list_pipeline_items("pipe-1", "prod") == []

    # admin_bulk_set_labels buckets across types and skips missing id
    labeled = await bridge.admin_bulk_set_labels(
        items=[
            {"id": "sm-1", "type": "SemanticModel"},
            {"item_id": "rep-1", "item_type": "PowerBIReport"},
            {"item_id": "df-1", "item_type": "DataflowGen2"},
            {"item_id": "", "item_type": "Report"},
        ],
        label_id="lbl-confidential",
    )
    assert labeled == {"labels": "applied"}
    artifacts_arg = client.admin_set_labels.call_args.args[0]
    assert artifacts_arg == {
        "datasets": [{"id": "sm-1"}],
        "reports": [{"id": "rep-1"}],
        "dataflows": [{"id": "df-1"}],
    }

    # execute_rls_test_query extracts rows and falls back to raw response when empty
    rows = await bridge.execute_rls_test_query(
        workspace_id="ws-1",
        dataset_id="sm-1",
        role_user="user@quadlogic.cl",
        dax="EVALUATE {1}",
    )
    assert rows == [{"c": 7}]

    client.execute_queries = AsyncMock(return_value={"results": []})
    raw_fallback = await bridge.execute_rls_test_query(
        workspace_id="ws-1",
        dataset_id="sm-1",
        role_user="user@quadlogic.cl",
        dax="EVALUATE {1}",
    )
    assert raw_fallback == {"results": []}


async def test_readonly_and_scope_guards_on_screenshot_and_data_dictionary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify PBI_READONLY blocks screenshot/dict writes and output_path requires write scope."""
    assert required_scope_for_tool("screenshot_report_pages") == SCOPE_READ

    pbip_dir = tmp_path / "m.pbip"
    pbip_dir.mkdir()
    monkeypatch.setenv("PBI_READONLY", "1")
    with pytest.raises(ReadOnlyModeError):
        await mcp_server.screenshot_report_pages(
            pbip_path=str(pbip_dir),
            output_dir=str(tmp_path / "shots"),
        )
    with pytest.raises(ReadOnlyModeError):
        await mcp_server.generate_data_dictionary(
            pbip_path=str(pbip_dir),
            output_path=str(tmp_path / "dict.md"),
        )

    monkeypatch.delenv("PBI_READONLY", raising=False)
    monkeypatch.setattr(
        "powerbi_orchestrator_mcp.orchestrator.authorization._granted_scopes",
        lambda: {SCOPE_READ},
    )
    with pytest.raises(ToolAuthorizationError):
        await mcp_server.generate_data_dictionary(
            pbip_path=str(pbip_dir),
            output_path=str(tmp_path / "dict.md"),
        )


def test_strict_compliance_preset_and_cli_audit_export(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify PBI_STRICT_COMPLIANCE enforces fail-closed audit and flags missing env vars."""
    monkeypatch.delenv("PBI_AUDIT_FAIL_CLOSED", raising=False)
    monkeypatch.setenv("PBI_STRICT_COMPLIANCE", "1")
    assert _fail_closed() is True

    monkeypatch.delenv("PBI_AUDIT_SECRET", raising=False)
    monkeypatch.delenv("PBI_EMAIL_ALLOWED_DOMAINS", raising=False)
    findings = collect_readiness()
    strict_finding = next(f for f in findings if f.name == "strict_compliance")
    assert strict_finding.ok is False
    assert "PBI_ORCHESTRATOR_AUDIT_SECRET" in strict_finding.detail

    home = Path(tmp_path / "fake_home")
    home.mkdir()
    (home / "audit.db").write_bytes(b"sqlite-audit")
    (home / ".audit_anchor").write_text("anchor-hmac", encoding="utf-8")
    monkeypatch.setenv("PBI_ORCHESTRATOR_HOME", str(home))

    export_dir = tmp_path / "audit_export"
    rc = cli_main(["audit-export", str(export_dir)])
    assert rc == 0
    manifest = json.loads((export_dir / "audit-export-manifest.json").read_text(encoding="utf-8"))
    assert "audit.db" in manifest["files"]
    assert ".audit_anchor" in manifest["files"]
    assert ".audit_key" not in manifest["files"]
