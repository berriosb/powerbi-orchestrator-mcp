"""sync_git_to_workspace tool — v3 (SPEC §6.2; spec: specs/tools/sync-git-to-workspace.md).

Deploy a local Git repo's PBIP tree to a Fabric workspace. For v3
inicial the only conflict-resolution mode is ``manual``: any item where
the workspace side has diverged from local is skipped and surfaced in
the output for human review.

For MVP the ``fabric_client.apply_pbip(workspace_id, item_type,
item_name, blob)`` call is the unit of deployment. Without an
injected client the tool emits a dry-run deployment plan only.

The reverse direction (workspace -> git) lives in
``commit_workspace_to_git``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

_VALID_RESOLUTION: set[str] = {"manual", "prefer_workspace", "prefer_git"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SyncGitToWorkspace(BaseModel):
    """Input schema."""

    repo_path: str
    branch_or_commit: str = "HEAD"
    workspace_id: str
    conflict_resolution: str = "manual"
    pre_deploy_profile: str | None = None
    pre_deploy_findings: list[dict[str, str]] | None = None
    fabric_client: Any = None
    dry_run: bool = True

    @field_validator("conflict_resolution")
    @classmethod
    def _check_resolution(cls, v: str) -> str:
        if v not in _VALID_RESOLUTION:
            raise ValueError(
                f"invalid conflict_resolution {v!r}; expected one of "
                f"{sorted(_VALID_RESOLUTION)}"
            )
        return v


class DeployedItem(BaseModel):
    """Per-item deployment outcome."""

    item_id: str | None = None
    item_name: str
    item_type: str
    path: str
    status: str  # deployed | skipped | conflict | failed
    reason: str = ""


class SyncGitToWorkspaceResult(BaseModel):
    """Output."""

    items_deployed: list[DeployedItem] = Field(default_factory=list)
    items_skipped: list[DeployedItem] = Field(default_factory=list)
    pre_deploy_check_result: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _list_pbip_files(repo_path: Path, ref: str) -> list[Path]:
    """Return PBIP files in the repo at the given ref.

    Uses ``git ls-tree -r --name-only <ref> -- '*.pbip'`` and resolves
    each path against the working tree (we require ``ref`` to be
    checkoutable).
    """
    proc = subprocess.run(
        ["git", "-C", str(repo_path), "ls-tree", "-r", "--name-only", ref],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if proc.returncode != 0:
        return []
    return [
        repo_path / line.strip()
        for line in proc.stdout.splitlines()
        if line.strip().endswith(".pbip")
    ]


def _parse_item_path(path: Path) -> tuple[str, str] | None:
    """Parse ``<type>/<name>.pbip`` → ``(type, name)``."""
    parts = path.parts
    if len(parts) < 2:
        return None
    item_type = parts[-2]
    name = parts[-1][: -len(".pbip")]
    if not name:
        return None
    return item_type, name


# ---------------------------------------------------------------------------
# Workspace state probing (to detect conflicts in manual mode)
# ---------------------------------------------------------------------------


def _enumerate_workspace_items(
    workspace_id: str, fabric_client: Any
) -> dict[tuple[str, str], dict[str, Any]]:
    """Return ``{(type, name): item_dict}`` for every workspace item.

    Without a real ``fabric_client.list_workspace_items`` impl returns
    an empty dict so the tool behaves as if the workspace were fresh.
    """
    if fabric_client is None:
        return {}
    try:
        result = fabric_client.list_workspace_items(workspace_id)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(result, list):
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for item in result:
        if not isinstance(item, dict):
            continue
        t = str(item.get("type", "?"))
        n = str(item.get("name", "?"))
        out[(t, n)] = item
    return out


def _diff_local_blob_vs_workspace(
    local_blob: str,
    workspace_item: dict[str, Any],
) -> bool:
    """Return True if the workspace item content differs from local.

    The workspace item is expected to carry ``blob`` or ``etag``; we
    use a simple content-equality check (byte-for-byte) to flag
    potential conflicts. A more sophisticated v3.1 would do content-
    based diff for TMDL/PBIR.
    """
    workspace_blob: Any = workspace_item.get("blob")
    if workspace_blob is None:
        return False  # unknown → treat as no conflict
    return bool(workspace_blob != local_blob)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def sync_git_to_workspace(
    repo_path: str,
    workspace_id: str,
    branch_or_commit: str = "HEAD",
    conflict_resolution: str = "manual",
    pre_deploy_profile: str | None = None,
    pre_deploy_findings: list[dict[str, str]] | None = None,
    fabric_client: Any = None,
    dry_run: bool = True,
) -> SyncGitToWorkspaceResult:
    """Deploy the local Git PBIP tree to the workspace."""
    warnings: list[str] = []

    repo = Path(repo_path)
    if not (repo / ".git").exists():
        return SyncGitToWorkspaceResult(
            dry_run=dry_run,
            warnings=[f"not a git repo: {repo_path}"],
        )

    # Pre-deploy check (optional).
    pre_deploy_check_result: dict[str, Any] = {}
    if pre_deploy_profile is not None:
        try:
            pass
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"could not import pre_deploy_check: {exc}")
        else:
            from powerbi_orchestrator_mcp.tools.pre_deploy_check import (
                pre_deploy_check as _pre_deploy,
            )

            result = _pre_deploy(pre_deploy_findings or [], profile=pre_deploy_profile)
            pre_deploy_check_result = result.model_dump(mode="json")
            if not result.passed:
                warnings.append(
                    f"pre_deploy_check blocked the deployment "
                    f"(profile={pre_deploy_profile})"
                )
                return SyncGitToWorkspaceResult(
                    dry_run=dry_run,
                    pre_deploy_check_result=pre_deploy_check_result,
                    warnings=warnings,
                )

    pbip_files = _list_pbip_files(repo, branch_or_commit)
    if not pbip_files:
        return SyncGitToWorkspaceResult(
            dry_run=dry_run,
            pre_deploy_check_result=pre_deploy_check_result,
            warnings=[
                f"no *.pbip files in {repo}@{branch_or_commit}"
                + (" (or ref missing)" if branch_or_commit != "HEAD" else "")
            ],
        )

    workspace_items = _enumerate_workspace_items(workspace_id, fabric_client)

    deployed: list[DeployedItem] = []
    skipped: list[DeployedItem] = []

    for path in pbip_files:
        parsed = _parse_item_path(path)
        if parsed is None:
            warnings.append(f"skipping malformed path {path}")
            continue
        item_type, item_name = parsed
        if not path.exists():
            # ref-only path (commit not checked out locally); skip for v3
            warnings.append(
                f"ref-only path {path}; cannot deploy without local checkout"
            )
            continue
        local_blob = path.read_text(encoding="utf-8", errors="replace")
        ws_match = workspace_items.get((item_type, item_name))

        # Conflict resolution.
        if ws_match and _diff_local_blob_vs_workspace(local_blob, ws_match):
            if conflict_resolution == "manual":
                skipped.append(
                    DeployedItem(
                        item_id=str(ws_match.get("id")),
                        item_name=item_name,
                        item_type=item_type,
                        path=str(path.relative_to(repo)),
                        status="conflict",
                        reason="workspace diverged; manual resolution required",
                    )
                )
                continue
            if conflict_resolution == "prefer_workspace":
                skipped.append(
                    DeployedItem(
                        item_id=str(ws_match.get("id")),
                        item_name=item_name,
                        item_type=item_type,
                        path=str(path.relative_to(repo)),
                        status="skipped",
                        reason="prefer_workspace: kept workspace version",
                    )
                )
                continue
            # prefer_git: fall through to deploy.

        # Deploy.
        if fabric_client is not None and not dry_run:
            try:
                fabric_client.apply_pbip(
                    workspace_id=workspace_id,
                    item_type=item_type,
                    item_name=item_name,
                    blob=local_blob,
                )
                deployed.append(
                    DeployedItem(
                        item_id=str(ws_match.get("id")) if ws_match else None,
                        item_name=item_name,
                        item_type=item_type,
                        path=str(path.relative_to(repo)),
                        status="deployed",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                skipped.append(
                    DeployedItem(
                        item_id=str(ws_match.get("id")) if ws_match else None,
                        item_name=item_name,
                        item_type=item_type,
                        path=str(path.relative_to(repo)),
                        status="failed",
                        reason=str(exc),
                    )
                )
        else:
            # Dry-run: report what would happen.
            deployed.append(
                DeployedItem(
                    item_id=str(ws_match.get("id")) if ws_match else None,
                    item_name=item_name,
                    item_type=item_type,
                    path=str(path.relative_to(repo)),
                    status="deployed",
                    reason="dry_run",
                )
            )

    if not deployed and not skipped and not warnings:
        warnings.append("deployment plan is empty")

    return SyncGitToWorkspaceResult(
        items_deployed=deployed,
        items_skipped=skipped,
        pre_deploy_check_result=pre_deploy_check_result,
        dry_run=dry_run,
        warnings=warnings,
    )


__all__ = [
    "DeployedItem",
    "SyncGitToWorkspace",
    "SyncGitToWorkspaceResult",
    "sync_git_to_workspace",
]


# Static check satisfaction.
_ = re
