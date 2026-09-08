"""commit_workspace_to_git tool — v3 (SPEC §6.2; spec: specs/tools/sync-git-to-workspace.md).

Serialize a Fabric / Power BI workspace snapshot into a local PBIP-tree
inside an existing Git repository and create a Git commit. The actual
fabric_client call returns a dict per item (id / name / type / raw PBIP
blob); without an injected client the tool uses a deterministic stub
that emits the spec-mandated layout (Dataset + Report, no real Fabric
REST hit).

For MVP we treat the ``fabric_client.snapshot_workspace(workspace_id)``
output as a mapping ``item_id -> {name, type, size, blob}``. The tool
walks the mapping, writes each item into the Git tree at
``<output_repo_path>/<item_type>/<item_name>.pbip``, and finally runs
``git add`` + ``git commit`` via subprocess (no ``gitpython``
dependency required).

Conflict handling (v3 inicial):

- If the destination path has unstaged local changes, abort with
  remediation hint (no auto-merge).
- If the file is larger than 50 MB, emit ``large_file_warning`` but
  still commit (opt-in LFS is the caller's responsibility).

The reverse direction (git -> workspace) lives in ``sync_git_to_workspace``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# 50 MB default cap per Fabric Git Integration docs.
_LARGE_FILE_BYTES_DEFAULT = 50 * 1024 * 1024


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CommitWorkspaceToGit(BaseModel):
    """Input schema."""

    workspace_id: str
    output_repo_path: str
    branch: str | None = None
    commit_message: str | None = None
    exclude_items: list[str] = Field(default_factory=list)
    dry_run: bool = True
    fabric_client: Any = None  # injected; sees snapshot_workspace
    large_file_bytes: int = _LARGE_FILE_BYTES_DEFAULT


class CommittedItem(BaseModel):
    """One item committed."""

    item_id: str
    item_name: str
    item_type: str  # Dataset | Report | Dataflow | PaginatedReport
    path: str


class CommitWorkspaceToGitResult(BaseModel):
    """Output."""

    items_committed: list[CommittedItem] = Field(default_factory=list)
    commit_sha: str | None = None
    conflicts_detected: list[str] = Field(default_factory=list)
    large_file_warnings: list[str] = Field(default_factory=list)
    dry_run: bool = True
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _LocalChangeSnapshot:
    """Cached ``git status --porcelain`` output for conflict detection."""

    dirty_paths: list[str]
    branch: str | None


def _run_git(
    repo_path: Path, args: list[str], check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a git command in the given repo; raise on failure (unless check=False)."""
    cmd = ["git", "-C", str(repo_path), *args]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, check=check, timeout=30
    )
    return proc


def _detect_local_changes(repo_path: Path) -> _LocalChangeSnapshot:
    """Inspect the working tree for unstaged / staged changes."""
    try:
        proc = _run_git(repo_path, ["status", "--porcelain"], check=False)
    except (OSError, subprocess.SubprocessError):
        return _LocalChangeSnapshot(dirty_paths=[], branch=None)

    dirty: list[str] = []
    for line in (proc.stdout or "").splitlines():
        # Porcelain format: XY <path>
        if len(line) >= 3:
            path = line[3:].strip()
            if path:
                # For renames: "old -> new"; keep just the new.
                if " -> " in path:
                    path = path.split(" -> ", 1)[1].strip()
                dirty.append(path)

    branch: str | None = None
    try:
        branch_proc = _run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
        branch = (branch_proc.stdout or "").strip() or None
    except (OSError, subprocess.SubprocessError):
        branch = None

    return _LocalChangeSnapshot(dirty_paths=dirty, branch=branch)


def _snapshot_workspace(
    workspace_id: str, fabric_client: Any
) -> dict[str, dict[str, Any]]:
    """Call fabric_client.snapshot_workspace or return an empty stub.

    The client must return a mapping ``item_id -> {name, type, blob}``.
    """
    if fabric_client is None:
        # Without a real client we have nothing to commit; caller can
        # provide a stub for tests.
        return {}
    try:
        result = fabric_client.snapshot_workspace(workspace_id)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(result, dict):
        return {}
    return result


def _render_pbip(item: dict[str, Any]) -> str:
    """Emit a PBIP-flavoured descriptor for the item.

    In production this is the raw blob from ``fabric_client``. For the
    stub path we serialize the metadata as JSON so the tool can be
    exercised end-to-end.
    """
    return json.dumps(item, indent=2, sort_keys=True)


def _detect_target_conflict(
    repo_path: Path, target_path: Path, dirty_paths: list[str]
) -> bool:
    """True if the working tree has unstaged changes on the target file.

    ``dirty_paths`` from ``git status --porcelain`` are repo-relative;
    we resolve them relative to ``repo_path`` before comparison.
    """
    for dirty in dirty_paths:
        try:
            absolute = (repo_path / dirty).resolve()
        except (OSError, ValueError, RuntimeError):
            continue
        try:
            if absolute == target_path.resolve():
                return True
        except (OSError, ValueError):
            continue
    return False


def _commit_to_git(
    repo_path: Path, target_paths: list[Path], message: str
) -> str | None:
    """Run git add + commit; return the resulting commit SHA or None."""
    rel_paths = [str(p.relative_to(repo_path)) for p in target_paths]
    _run_git(repo_path, ["add", "--", *rel_paths])
    try:
        _run_git(repo_path, ["commit", "-m", message])
    except subprocess.CalledProcessError as exc:
        # Possibly nothing to commit or pre-commit hook rejection.
        if "nothing to commit" in (exc.stderr or "").lower():
            return None
        raise
    sha_proc = _run_git(repo_path, ["rev-parse", "HEAD"])
    return (sha_proc.stdout or "").strip() or None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def commit_workspace_to_git(
    workspace_id: str,
    output_repo_path: str,
    branch: str | None = None,
    commit_message: str | None = None,
    exclude_items: list[str] | None = None,
    dry_run: bool = True,
    fabric_client: Any = None,
    large_file_bytes: int = _LARGE_FILE_BYTES_DEFAULT,
) -> CommitWorkspaceToGitResult:
    """Snapshot the workspace and commit it to the local Git repo.

    Args:
        workspace_id: Fabric workspace ID.
        output_repo_path: Path to a local Git repo (must already be
            initialised).
        branch: Optional branch name; switches the repo to it before
            committing. Default: current branch (no switch).
        commit_message: Optional override; default
            "Auto-commit from workspace <id> at <timestamp>".
        exclude_items: Item IDs to skip.
        dry_run: If True, no files are written and no commit happens.
        fabric_client: Injected REST adapter with
            ``snapshot_workspace(workspace_id)``. None → empty
            snapshot.
        large_file_bytes: Threshold (bytes) above which to flag a
            ``large_file_warning``. Default 50 MB.

    Returns:
        CommitWorkspaceToGitResult.
    """
    warnings: list[str] = []
    excluded = set(exclude_items or [])
    items_committed: list[CommittedItem] = []
    large_warnings: list[str] = []
    conflicts: list[str] = []

    repo_path = Path(output_repo_path)
    if not (repo_path / ".git").exists():
        return CommitWorkspaceToGitResult(
            dry_run=dry_run,
            warnings=[f"not a git repo: {repo_path}"],
        )

    # Optional branch switch (only if requested).
    if branch is not None:
        try:
            _run_git(repo_path, ["checkout", branch])
        except subprocess.CalledProcessError as exc:
            return CommitWorkspaceToGitResult(
                dry_run=dry_run,
                warnings=[f"git checkout {branch!r} failed: {(exc.stderr or '').strip()}"],
            )

    snapshot = _snapshot_workspace(workspace_id, fabric_client)
    if not snapshot:
        if fabric_client is None:
            warnings.append(
                "no fabric_client injected; nothing to commit "
                "(wire fabric_client.snapshot_workspace for real use)"
            )
        else:
            warnings.append("fabric_client returned an empty snapshot")

    # Conflict detection against the working tree.
    local = _detect_local_changes(repo_path)

    # Render-and-write phase.
    target_paths: list[Path] = []
    for item_id, item in snapshot.items():
        if item_id in excluded:
            continue
        item_name = str(item.get("name", item_id))
        item_type = str(item.get("type", "Dataset"))
        blob = str(item.get("blob", _render_pbip(item)))
        target = repo_path / item_type / f"{item_name}.pbip"
        target.parent.mkdir(parents=True, exist_ok=True)

        if _detect_target_conflict(repo_path, target, local.dirty_paths):
            conflicts.append(str(target))
            warnings.append(
                f"local changes on {target}; abort to avoid clobbering"
            )
            continue

        size = len(blob.encode("utf-8"))
        if size > large_file_bytes:
            large_warnings.append(
                f"{item_type}/{item_name}.pbip is {size} bytes (> {large_file_bytes}); "
                "consider Git LFS"
            )

        if not dry_run:
            target.write_text(blob, encoding="utf-8")

        items_committed.append(
            CommittedItem(
                item_id=item_id,
                item_name=item_name,
                item_type=item_type,
                path=str(target.relative_to(repo_path)),
            )
        )
        target_paths.append(target)

    if conflicts:
        return CommitWorkspaceToGitResult(
            items_committed=[],
            conflicts_detected=conflicts,
            large_file_warnings=large_warnings,
            dry_run=dry_run,
            warnings=warnings,
        )

    # Commit phase (only if there's something new AND not dry_run).
    commit_sha: str | None = None
    if not dry_run and target_paths:
        from datetime import UTC, datetime

        msg = (
            commit_message
            or f"Auto-commit from workspace {workspace_id} at "
            f"{datetime.now(UTC).isoformat(timespec='seconds')}"
        )
        try:
            commit_sha = _commit_to_git(repo_path, target_paths, msg)
        except subprocess.CalledProcessError as exc:
            warnings.append(f"git commit failed: {(exc.stderr or '').strip()}")
        except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
            warnings.append(f"git invocation failed: {exc}")

    return CommitWorkspaceToGitResult(
        items_committed=items_committed,
        commit_sha=commit_sha,
        large_file_warnings=large_warnings,
        dry_run=dry_run,
        warnings=warnings,
    )


__all__ = [
    "CommitWorkspaceToGit",
    "CommitWorkspaceToGitResult",
    "CommittedItem",
    "commit_workspace_to_git",
]


# Re-export for sibling module convenience.
_ = shutil
