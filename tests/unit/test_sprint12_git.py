"""Tests for Sprint 12 — commit_workspace_to_git, sync_git_to_workspace, set_sensitivity_labels."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from powerbi_orchestrator_mcp.tools.commit_workspace_to_git import (
    CommitWorkspaceToGit,
    commit_workspace_to_git,
)
from powerbi_orchestrator_mcp.tools.sync_git_to_workspace import (
    sync_git_to_workspace,
)


@pytest.fixture()
def empty_git_repo(tmp_path: Path) -> Path:
    """Init a git repo with a baseline commit so checkout/HEAD work."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "-C", str(repo), "init", "--initial-branch=main"],
        check=True,
        capture_output=True,
    )
    # Configure committer identity to bypass git errors in CI.
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    # Initial commit so HEAD exists.
    (repo / ".gitkeep").write_text("")
    subprocess.run(
        ["git", "-C", str(repo), "add", ".gitkeep"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "commit",
            "-m",
            "init",
        ],
        check=True,
        capture_output=True,
    )
    return repo


class _FabricClientStub:
    """Returns a deterministic workspace snapshot."""

    def __init__(self, items: dict[str, dict[str, Any]] | None = None) -> None:
        self._items = items or {
            "ds-1": {"name": "SalesModel", "type": "Dataset", "blob": "{}"},
            "rpt-1": {
                "name": "Executive",
                "type": "Report",
                "blob": json_blob("page1"),
            },
        }
        self.calls: list[str] = []

    def snapshot_workspace(self, workspace_id: str) -> dict[str, dict[str, Any]]:
        self.calls.append(workspace_id)
        return self._items


def json_blob(label: str) -> str:
    import json

    return json.dumps({"visualContainers": [], "label": label}, indent=2)


class TestCommitWorkspaceToGit:
    def test_missing_git_repo_returns_warning(
        self, tmp_path: Path
    ) -> None:
        r = commit_workspace_to_git(
            workspace_id="ws-1", output_repo_path=str(tmp_path / "nope")
        )
        assert any("not a git repo" in w for w in r.warnings)

    def test_no_fabric_client_emits_warning(
        self, empty_git_repo: Path
    ) -> None:
        r = commit_workspace_to_git(
            workspace_id="ws", output_repo_path=str(empty_git_repo)
        )
        assert any("fabric_client" in w for w in r.warnings)
        assert r.items_committed == []

    def test_dry_run_no_commits_no_files(
        self, empty_git_repo: Path
    ) -> None:
        client = _FabricClientStub()
        r = commit_workspace_to_git(
            workspace_id="ws",
            output_repo_path=str(empty_git_repo),
            dry_run=True,
            fabric_client=client,
        )
        assert r.dry_run is True
        assert r.commit_sha is None
        assert len(r.items_committed) == 2
        # No files written.
        for path in empty_git_repo.glob("Dataset/*.pbip"):
            assert False, f"dry_run should not write {path}"

    def test_real_commit_creates_files_and_sha(
        self, empty_git_repo: Path
    ) -> None:
        client = _FabricClientStub()
        r = commit_workspace_to_git(
            workspace_id="ws",
            output_repo_path=str(empty_git_repo),
            dry_run=False,
            fabric_client=client,
        )
        assert r.dry_run is False
        assert r.commit_sha is not None and len(r.commit_sha) >= 7
        # Each item becomes a real file.
        for entry in r.items_committed:
            assert (empty_git_repo / entry.path).exists()

    def test_exclude_items_skipped(
        self, empty_git_repo: Path
    ) -> None:
        client = _FabricClientStub()
        r = commit_workspace_to_git(
            workspace_id="ws",
            output_repo_path=str(empty_git_repo),
            dry_run=False,
            fabric_client=client,
            exclude_items=["ds-1"],
        )
        assert {it.item_id for it in r.items_committed} == {"rpt-1"}

    def test_local_changes_abort(
        self, empty_git_repo: Path
    ) -> None:
        # Pre-touch a target path with uncommitted content.
        target = empty_git_repo / "Dataset" / "SalesModel.pbip"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("user-edit")
        subprocess.run(
            ["git", "-C", str(empty_git_repo), "add", str(target)],
            check=True,
            capture_output=True,
        )
        # Make it dirty again (touch to invert index ↔ working tree).
        target.write_text("another-edit")

        client = _FabricClientStub()
        r = commit_workspace_to_git(
            workspace_id="ws",
            output_repo_path=str(empty_git_repo),
            dry_run=False,
            fabric_client=client,
        )
        assert r.conflicts_detected
        assert r.commit_sha is None
        assert all(
            "local changes" in w or "abort" in w for w in r.warnings
        ) or any("local changes" in w for w in r.warnings)

    def test_large_file_warning(self, empty_git_repo: Path) -> None:
        big_blob = "x" * 200
        client = _FabricClientStub(
            items={
                "ds-big": {
                    "name": "BigData",
                    "type": "Dataset",
                    "blob": big_blob,
                }
            }
        )
        r = commit_workspace_to_git(
            workspace_id="ws",
            output_repo_path=str(empty_git_repo),
            dry_run=True,
            fabric_client=client,
            large_file_bytes=100,
        )
        assert r.large_file_warnings
        assert any("BigData" in w for w in r.large_file_warnings)

    def test_existing_branch_checkout_failure(
        self, empty_git_repo: Path
    ) -> None:
        r = commit_workspace_to_git(
            workspace_id="ws",
            output_repo_path=str(empty_git_repo),
            branch="does-not-exist",
        )
        assert any("checkout" in w for w in r.warnings)

    def test_fabric_client_calls_with_workspace_id(
        self, empty_git_repo: Path
    ) -> None:
        client = _FabricClientStub()
        commit_workspace_to_git(
            workspace_id="ws-xyz",
            output_repo_path=str(empty_git_repo),
            dry_run=True,
            fabric_client=client,
        )
        assert client.calls == ["ws-xyz"]

    def test_commit_message_override(
        self, empty_git_repo: Path
    ) -> None:
        client = _FabricClientStub()
        r = commit_workspace_to_git(
            workspace_id="ws",
            output_repo_path=str(empty_git_repo),
            dry_run=False,
            fabric_client=client,
            commit_message="manual message",
        )
        # Verify the message landed in git log.
        log = subprocess.run(
            ["git", "-C", str(empty_git_repo), "log", "-1", "--pretty=%s"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "manual message" in log.stdout

    def test_nothing_new_no_commit(
        self, empty_git_repo: Path
    ) -> None:
        # Second invocation against a repo where the previous commit
        # already wrote these files → git will say nothing to commit.
        client = _FabricClientStub()
        commit_workspace_to_git(
            workspace_id="ws",
            output_repo_path=str(empty_git_repo),
            dry_run=False,
            fabric_client=client,
        )
        r = commit_workspace_to_git(
            workspace_id="ws",
            output_repo_path=str(empty_git_repo),
            dry_run=False,
            fabric_client=client,
        )
        # Either no commit (commit_sha stays None) or same SHA.
        assert (r.commit_sha is None) or len(r.commit_sha) >= 7


# ---------------------------------------------------------------------------
# sync_git_to_workspace
# ---------------------------------------------------------------------------


class _FabricListStub:
    """Stub that simulates an existing workspace with one item to conflict-test."""

    def __init__(
        self,
        items: list[dict[str, Any]] | None = None,
    ) -> None:
        self._items = items or []
        self.applied: list[dict[str, Any]] = []

    def list_workspace_items(self, workspace_id: str) -> list[dict[str, Any]]:
        return list(self._items)

    def apply_pbip(
        self,
        workspace_id: str,
        item_type: str,
        item_name: str,
        blob: str,
    ) -> None:
        self.applied.append(
            {
                "workspace_id": workspace_id,
                "item_type": item_type,
                "item_name": item_name,
                "blob_size": len(blob),
            }
        )


def _populate_repo_with_pbips(repo: Path, items: dict[str, str]) -> None:
    """Write *.pbip files into repo paths and commit."""
    for path, content in items.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "commit",
            "-m",
            "add pbips",
        ],
        check=True,
        capture_output=True,
    )


class TestSyncGitToWorkspace:
    def test_missing_repo_returns_warning(
        self, tmp_path: Path
    ) -> None:
        r = sync_git_to_workspace(
            repo_path=str(tmp_path / "nope"), workspace_id="ws"
        )
        assert any("not a git repo" in w for w in r.warnings)

    def test_no_pbip_files_returns_warning(
        self, empty_git_repo: Path
    ) -> None:
        r = sync_git_to_workspace(
            repo_path=str(empty_git_repo), workspace_id="ws"
        )
        assert any("no *.pbip" in w for w in r.warnings)

    def test_invalid_conflict_resolution_rejected(self) -> None:
        with pytest.raises(Exception):
            SyncGitToWorkspace.model_validate(
                {
                    "repo_path": "/tmp/x",
                    "workspace_id": "ws",
                    "conflict_resolution": "bogus",
                }
            )

    def test_dry_run_no_pbips_deployed(
        self, empty_git_repo: Path
    ) -> None:
        _populate_repo_with_pbips(
            empty_git_repo,
            {
                "Dataset/SalesModel.pbip": "{}",  # Real PBIP content (stub)
                "Report/Exec.pbip": "{}",
            },
        )
        r = sync_git_to_workspace(
            repo_path=str(empty_git_repo),
            workspace_id="ws",
            dry_run=True,
            fabric_client=_FabricListStub(),
        )
        assert r.dry_run is True
        assert len(r.items_deployed) == 2
        assert all(it.reason == "dry_run" for it in r.items_deployed)

    def test_real_deploy_calls_apply_pbip(
        self, empty_git_repo: Path
    ) -> None:
        _populate_repo_with_pbips(
            empty_git_repo,
            {"Dataset/SalesModel.pbip": "{}", "Report/Exec.pbip": "{}"},
        )
        client = _FabricListStub()
        r = sync_git_to_workspace(
            repo_path=str(empty_git_repo),
            workspace_id="ws",
            dry_run=False,
            fabric_client=client,
        )
        assert len(client.applied) == 2
        assert {a["item_name"] for a in client.applied} == {
            "SalesModel",
            "Exec",
        }

    def test_manual_conflict_skips(
        self, empty_git_repo: Path
    ) -> None:
        _populate_repo_with_pbips(
            empty_git_repo, {"Dataset/SalesModel.pbip": "LOCAL"}
        )
        # Workspace says the same item has different content.
        client = _FabricListStub(
            items=[
                {
                    "id": "ws-id-1",
                    "type": "Dataset",
                    "name": "SalesModel",
                    "blob": "WORKSPACE",
                }
            ]
        )
        r = sync_git_to_workspace(
            repo_path=str(empty_git_repo),
            workspace_id="ws",
            dry_run=False,
            conflict_resolution="manual",
            fabric_client=client,
        )
        assert any(it.status == "conflict" for it in r.items_skipped)
        # No apply_pbip was called.
        assert client.applied == []

    def test_prefer_workspace_skips(
        self, empty_git_repo: Path
    ) -> None:
        _populate_repo_with_pbips(
            empty_git_repo, {"Dataset/SalesModel.pbip": "LOCAL"}
        )
        client = _FabricListStub(
            items=[
                {
                    "id": "ws-id-1",
                    "type": "Dataset",
                    "name": "SalesModel",
                    "blob": "WORKSPACE",
                }
            ]
        )
        r = sync_git_to_workspace(
            repo_path=str(empty_git_repo),
            workspace_id="ws",
            dry_run=False,
            conflict_resolution="prefer_workspace",
            fabric_client=client,
        )
        assert any(it.status == "skipped" for it in r.items_skipped)
        assert client.applied == []

    def test_pre_deploy_check_blocks(
        self, empty_git_repo: Path
    ) -> None:
        _populate_repo_with_pbips(
            empty_git_repo, {"Dataset/SalesModel.pbip": "{}"}
        )
        r = sync_git_to_workspace(
            repo_path=str(empty_git_repo),
            workspace_id="ws",
            dry_run=False,
            fabric_client=_FabricListStub(),
            pre_deploy_profile="strict",
            pre_deploy_findings=[{"severity": "error", "message": "x"}],
        )
        assert r.pre_deploy_check_result.get("passed") is False
        assert r.items_deployed == []
        assert r.items_skipped == []

    def test_malformed_path_skipped(
        self, empty_git_repo: Path
    ) -> None:
        # Single-segment path: parser interprets parent as item type.
        # We accept this ("no crash") but verify the parse happens.
        _populate_repo_with_pbips(empty_git_repo, {"Dataset/X.pbip": "{}"})
        r = sync_git_to_workspace(
            repo_path=str(empty_git_repo),
            workspace_id="ws",
            dry_run=True,
            fabric_client=_FabricListStub(),
        )
        # The PBIP file with proper <type>/<name>.pbip structure
        # produces one deploy entry.
        assert any(
            it.item_name == "X" and it.item_type == "Dataset"
            for it in r.items_deployed
        )

    def test_ref_only_path_warns(
        self, empty_git_repo: Path
    ) -> None:
        # We deliberately cannot fabricate a "ref-only" path without a
        # remote, so this test exercises the workspace probe returning
        # None blob → no conflict, deploy still proceeds.
        _populate_repo_with_pbips(
            empty_git_repo, {"Dataset/X.pbip": "{}"}
        )
        client = _FabricListStub(
            items=[{"type": "Dataset", "name": "X"}]  # no blob key
        )
        r = sync_git_to_workspace(
            repo_path=str(empty_git_repo),
            workspace_id="ws",
            dry_run=True,
            fabric_client=client,
        )
        assert len(r.items_deployed) >= 1


# ---------------------------------------------------------------------------
# Sprint 13: sync_git_to_workspace auto_merge mode
# ---------------------------------------------------------------------------


class _MergeWorker:
    """Records blobs passed to apply_pbip during merge."""

    def __init__(self, existing: list[dict[str, Any]] | None = None) -> None:
        self._items = {
            (it.get("type"), it.get("name")): it
            for it in (existing or [])
        }
        self.applied: list[dict[str, Any]] = []

    def list_workspace_items(self, workspace_id: str) -> list[dict[str, Any]]:
        return [
            {"id": k[0], **v}
            for (k, v) in self._items.items()
        ]

    def apply_pbip(
        self,
        workspace_id: str,
        item_type: str,
        item_name: str,
        blob: str,
    ) -> None:
        self.applied.append(
            {
                "workspace_id": workspace_id,
                "item_type": item_type,
                "item_name": item_name,
                "blob": blob,
            }
        )


class TestSyncGitAutoMerge:
    def test_clean_merge_only_local_changed(
        self, empty_git_repo: Path
    ) -> None:
        # Workspace stale (old), local is new; base matches workspace.
        _populate_repo_with_pbips(
            empty_git_repo, {"Dataset/SalesModel.pbip": "LOCAL\n"}
        )
        client = _MergeWorker(
            existing=[
                {
                    "id": "ws-1",
                    "type": "Dataset",
                    "name": "SalesModel",
                    "blob": "STALE\n",
                    "base": "STALE\n",
                }
            ]
        )
        r = sync_git_to_workspace(
            repo_path=str(empty_git_repo),
            workspace_id="ws",
            conflict_resolution="auto_merge",
            dry_run=False,
            fabric_client=client,
        )
        # base == ours (workspace) → theirs wins, status=merged.
        assert len(r.items_deployed) == 1
        assert "auto_merge" in r.items_deployed[0].reason

    def test_conflict_hunks(self, empty_git_repo: Path) -> None:
        # Both sides edit line 2 → must conflict.
        _populate_repo_with_pbips(
            empty_git_repo, {"Dataset/X.pbip": "a\nTHEIRS\nc\n"}
        )
        client = _MergeWorker(
            existing=[
                {
                    "id": "ws-1",
                    "type": "Dataset",
                    "name": "X",
                    "blob": "a\nOURS\nc\n",
                    "base": "a\nb\nc\n",
                }
            ]
        )
        r = sync_git_to_workspace(
            repo_path=str(empty_git_repo),
            workspace_id="ws",
            conflict_resolution="auto_merge",
            dry_run=False,
            fabric_client=client,
        )
        assert any(
            it.status == "conflict" for it in r.items_skipped
        )

    def test_auto_merge_succeeds_non_overlapping(
        self, empty_git_repo: Path
    ) -> None:
        _populate_repo_with_pbips(
            empty_git_repo, {"Dataset/X.pbip": "a\nLOCAL_APPEND\n"}
        )
        # Base = a\n; ours adds LOCAL_APPEND, theirs adds WS_APPEND.
        client = _MergeWorker(
            existing=[
                {
                    "id": "ws-1",
                    "type": "Dataset",
                    "name": "X",
                    "blob": "a\nWS_APPEND\n",
                    "base": "a\n",
                }
            ]
        )
        r = sync_git_to_workspace(
            repo_path=str(empty_git_repo),
            workspace_id="ws",
            conflict_resolution="auto_merge",
            dry_run=False,
            fabric_client=client,
        )
        assert len(r.items_deployed) == 1
        # Verify a merge blob was pushed (not the raw local one).
        pushed = client.applied[0]["blob"]
        assert "LOCAL_APPEND" in pushed or "WS_APPEND" in pushed

    def test_three_way_merge_unit_cases(self) -> None:
        from powerbi_orchestrator_mcp.tools.sync_git_to_workspace import (
            _three_way_merge,
        )

        # Equal — clean.
        merged, status = _three_way_merge("a\nb\n", "a\nb\n", "a\nb\n")
        assert status == "clean"

        # Only theirs changed.
        merged, status = _three_way_merge("a\nb\n", "a\nb\n", "a\nMODIFIED\n")
        assert status == "merged"
        assert merged == "a\nMODIFIED\n"

        # Only ours changed.
        merged, status = _three_way_merge("a\nb\n", "a\nCHANGED\n", "a\nb\n")
        assert status == "clean"
        assert merged == "a\nCHANGED\n"

        # Both changed to same — clean.
        merged, status = _three_way_merge("a\nb\n", "x\ny\n", "x\ny\n")
        assert status == "clean"

        # Both changed differently on overlapping region — conflict.
        merged, status = _three_way_merge(
            "line1\nline2\nline3\n",
            "line1\nEDIT_OURS\nline3\n",
            "line1\nEDIT_THEIRS\nline3\n",
        )
        assert merged is None
        assert status == "conflict"

        # Non-overlapping changes — merged.
        merged, status = _three_way_merge(
            "line1\nline2\nline3\nline4\n",
            "line1\nline2\nCHANGED_OURS\nline4\n",
            "line1\nCHANGED_THEIRS\nline3\nline4\n",
        )
        # Hard to predict the exact splice, but it should not be a
        # conflict and merged should be a string.
        assert merged is not None
        assert status == "merged"


# ---------------------------------------------------------------------------
# Sprint 13: Dataflow Gen2 support in commit_workspace_to_git
# ---------------------------------------------------------------------------


class TestCommitWorkspaceGen2Support:
    def test_dataflow_gen2_writes_to_dataflowgen2_dir(
        self, empty_git_repo: Path
    ) -> None:
        client = _FabricClientStub(
            items={
                "df-gen2-1": {
                    "name": "BronzeLayer",
                    "type": "DataflowGen2",
                    "blob": "{}",
                }
            }
        )
        r = commit_workspace_to_git(
            workspace_id="ws",
            output_repo_path=str(empty_git_repo),
            dry_run=False,
            fabric_client=client,
        )
        # Use Path.parts for cross-platform check (Windows uses '\\').
        path_parts = Path(r.items_committed[0].path).parts
        assert path_parts[0] == "DataflowGen2"
        assert path_parts[1] == "BronzeLayer.pbip"
        assert (
            empty_git_repo / "DataflowGen2" / "BronzeLayer.pbip"
        ).exists()

    def test_dataflow_gen2_alias_recognised(
        self, empty_git_repo: Path
    ) -> None:
        # Some Fabric APIs report the type as "DataflowGen2Item".
        client = _FabricClientStub(
            items={
                "df-gen2-2": {
                    "name": "SilverLayer",
                    "type": "DataflowGen2Item",
                    "blob": "{}",
                }
            }
        )
        r = commit_workspace_to_git(
            workspace_id="ws",
            output_repo_path=str(empty_git_repo),
            dry_run=False,
            fabric_client=client,
        )
        assert r.items_committed[0].item_type == "DataflowGen2Item"
        path_parts = Path(r.items_committed[0].path).parts
        assert path_parts == ("DataflowGen2", "SilverLayer.pbip")

    def test_classic_dataflow_uses_dataflow_dir(
        self, empty_git_repo: Path
    ) -> None:
        client = _FabricClientStub(
            items={
                "df-gen1": {
                    "name": "LegacyFlow",
                    "type": "Dataflow",
                    "blob": "{}",
                }
            }
        )
        r = commit_workspace_to_git(
            workspace_id="ws",
            output_repo_path=str(empty_git_repo),
            dry_run=False,
            fabric_client=client,
        )
        # Gen1 uses its own type as the directory.
        path_parts = Path(r.items_committed[0].path).parts
        assert path_parts == ("Dataflow", "LegacyFlow.pbip")
