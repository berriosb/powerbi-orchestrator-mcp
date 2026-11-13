"""Bridges connecting FabricClient async REST APIs to MCP tool execution.

The v2/v3 orchestration tools were originally authored around injected sync
test stubs (``snapshot_workspace``, ``apply_pbip``, ``deploy_pipeline_item``,
``admin_bulk_set_labels``). This module implements those operations against
``FabricClient`` asynchronously so ``server.py`` invokes real Microsoft Fabric
and Power BI REST endpoints without blocking or calling ``asyncio.run()``.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from powerbi_orchestrator_mcp.cloud.fabric_client import FabricClient

_STAGE_INDEX = {"dev": 0, "test": 1, "prod": 2}

_TYPE_TO_LABEL_BUCKET = {
    "Dashboard": "dashboards",
    "dashboards": "dashboards",
    "Report": "reports",
    "PowerBIReport": "reports",
    "reports": "reports",
    "Dataset": "datasets",
    "SemanticModel": "datasets",
    "PowerBIDataset": "datasets",
    "datasets": "datasets",
    "Dataflow": "dataflows",
    "DataflowGen2": "dataflows",
    "dataflows": "dataflows",
}


class AsyncFabricBridge:
    """Async adapter translating high-level tool intents into Fabric REST calls."""

    def __init__(self, client: FabricClient) -> None:
        self._client = client

    async def snapshot_workspace(self, workspace_id: str) -> dict[str, dict[str, Any]]:
        """Return ``item_id -> {name, type, blob}`` for all workspace items."""
        items = await self._client.list_items(workspace_id)
        snapshot: dict[str, dict[str, Any]] = {}
        for item in items:
            item_id = str(item.get("id") or "")
            if not item_id:
                continue
            item_name = str(item.get("displayName") or item.get("name") or item_id)
            item_type = str(item.get("type") or "SemanticModel")
            blob_obj: dict[str, Any] = dict(item)
            try:
                definition = await self._client.get_item_definition(workspace_id, item_id)
                if definition:
                    blob_obj["definition"] = definition
            except Exception:  # noqa: BLE001
                # Some item types (e.g. classic dashboards) do not expose getDefinition.
                pass
            snapshot[item_id] = {
                "name": item_name,
                "type": item_type,
                "blob": json.dumps(blob_obj, indent=2, sort_keys=True),
            }
        return snapshot

    async def list_workspace_items(self, workspace_id: str) -> list[dict[str, Any]]:
        """List Fabric items normalized with ``id``, ``name``, ``type`` keys."""
        items = await self._client.list_items(workspace_id)
        normalized: list[dict[str, Any]] = []
        for item in items:
            normalized.append(
                {
                    **item,
                    "id": item.get("id"),
                    "name": item.get("displayName") or item.get("name"),
                    "type": item.get("type"),
                }
            )
        return normalized

    async def apply_pbip(
        self,
        *,
        workspace_id: str,
        item_type: str,
        item_name: str,
        blob: str,
    ) -> dict[str, Any]:
        """Create or update a Fabric item from a serialized PBIP/definition blob."""
        definition = _extract_definition_payload(blob)
        existing = await self.list_workspace_items(workspace_id)
        match = next(
            (
                it
                for it in existing
                if it.get("name") == item_name and (it.get("type") == item_type or not item_type)
            ),
            None,
        )
        if match and match.get("id"):
            return await self._client.update_item_definition(
                workspace_id,
                str(match["id"]),
                definition=definition,
            )
        fabric_type = "SemanticModel" if item_type == "Dataset" else item_type
        return await self._client.create_item(
            workspace_id,
            display_name=item_name,
            item_type=fabric_type,
            definition=definition,
        )

    async def resolve_stage_ids(
        self, pipeline_id: str, source_stage: str, target_stage: str
    ) -> tuple[str, str]:
        """Resolve symbolic stage names ('dev'/'test'/'prod') to stage UUIDs."""
        stages = await self._client.list_pipeline_stages(pipeline_id)
        sorted_stages = sorted(stages, key=lambda s: int(s.get("order", 0)))
        src_idx = _STAGE_INDEX.get(source_stage, 0)
        tgt_idx = _STAGE_INDEX.get(target_stage, 1)
        src_id = (
            str(sorted_stages[src_idx]["id"])
            if src_idx < len(sorted_stages) and sorted_stages[src_idx].get("id")
            else source_stage
        )
        tgt_id = (
            str(sorted_stages[tgt_idx]["id"])
            if tgt_idx < len(sorted_stages) and sorted_stages[tgt_idx].get("id")
            else target_stage
        )
        return src_id, tgt_id

    async def list_pipeline_items(self, pipeline_id: str, source_stage: str) -> list[str]:
        """Return item IDs belonging to ``source_stage`` of ``pipeline_id``."""
        stages = await self._client.list_pipeline_stages(pipeline_id)
        sorted_stages = sorted(stages, key=lambda s: int(s.get("order", 0)))
        idx = _STAGE_INDEX.get(source_stage, 0)
        if idx >= len(sorted_stages):
            return []
        ws_id = sorted_stages[idx].get("workspaceId")
        if not ws_id:
            return []
        items = await self._client.list_items(str(ws_id))
        return [str(it["id"]) for it in items if it.get("id")]

    async def deploy_pipeline_item(
        self,
        *,
        pipeline_id: str,
        source_stage: str,
        target_stage: str,
        item_id: str,
        item_type: str = "SemanticModel",
    ) -> dict[str, Any]:
        """Deploy a single item across Fabric deployment pipeline stages."""
        src_id, tgt_id = await self.resolve_stage_ids(pipeline_id, source_stage, target_stage)
        return await self._client.deploy_pipeline(
            pipeline_id,
            source_stage_id=src_id,
            target_stage_id=tgt_id,
            items=[{"sourceItemId": item_id, "itemType": item_type}],
            note=f"Promoted {item_id} from {source_stage} to {target_stage}",
        )

    async def admin_bulk_set_labels(
        self,
        *,
        items: list[dict[str, str]],
        label_id: str,
    ) -> dict[str, Any]:
        """Group items by Power BI artifact type and invoke Admin setLabels."""
        artifacts: dict[str, list[dict[str, str]]] = {}
        for item in items:
            iid = item.get("item_id") or item.get("id")
            if not iid:
                continue
            raw_type = item.get("item_type") or item.get("type") or "Report"
            bucket = _TYPE_TO_LABEL_BUCKET.get(raw_type, "reports")
            artifacts.setdefault(bucket, []).append({"id": iid})
        return await self._client.admin_set_labels(artifacts, label_id)

    async def execute_rls_test_query(
        self,
        *,
        workspace_id: str,
        dataset_id: str,
        role_user: str,
        dax: str,
    ) -> Any:
        """Execute a DAX test query impersonating ``role_user`` via Power BI REST."""
        resp = await self._client.execute_queries(
            workspace_id,
            dataset_id,
            queries=[{"query": dax}],
            impersonated_user_name=role_user,
        )
        results = resp.get("results") or []
        if results and isinstance(results, list):
            tables = results[0].get("tables") or []
            if tables:
                return tables[0].get("rows", [])
        return resp


def _extract_definition_payload(blob: str) -> dict[str, Any]:
    """Convert raw PBIP JSON/text into a Fabric ``definition`` parts dict."""
    try:
        parsed = json.loads(blob)
        if isinstance(parsed, dict) and isinstance(parsed.get("definition"), dict):
            return dict(parsed["definition"])
        if isinstance(parsed, dict) and isinstance(parsed.get("parts"), list):
            return parsed
    except (ValueError, TypeError):
        pass
    encoded = base64.b64encode(blob.encode("utf-8")).decode("ascii")
    return {
        "parts": [
            {
                "path": "definition.pbism",
                "payload": encoded,
                "payloadType": "InlineBase64",
            }
        ]
    }


__all__ = ["AsyncFabricBridge"]
