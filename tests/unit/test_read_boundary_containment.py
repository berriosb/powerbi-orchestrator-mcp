"""Tests for the read boundary of the tools that read a caller-supplied path.

Four tools read a path the caller chooses, and none of them validated it:

- ``optimize_report_performance`` and ``audit_report_ux_and_storytelling``
  are **read-scoped**. The page names and visual ids they read off the
  disk end up in the result, so an unbounded read is a way to pull a
  report from anywhere on the host into the model context.
- ``deploy_to_workspace`` and ``sync_git_to_workspace`` **publish** what
  they read to a Fabric workspace. Their paths are validated as writes on
  purpose: the read opt-out must not be able to widen what leaves the
  process.

The paths used here are deliberately **outside** the workspace root. The
session-scoped ``tests/conftest.py`` fixture points ``PBI_WORKSPACE_ROOT``
at pytest's basetemp, so a path under ``tmp_path`` is inside the root and
would not exercise containment at all.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.tools.audit_report_ux_and_storytelling import (
    audit_report_ux_and_storytelling,
)
from powerbi_orchestrator_mcp.tools.deploy_to_workspace import (
    deploy_to_workspace,
)
from powerbi_orchestrator_mcp.tools.optimize_report_performance import (
    optimize_report_performance,
)
from powerbi_orchestrator_mcp.tools.sync_git_to_workspace import (
    sync_git_to_workspace,
)

# A directory name that must never reach a result: it is the tell that a
# file outside the workspace was read.
OUTSIDE_PAGE_NAME = "ConfidentialPayrollPage"
MARKER = "SECRET_MARKER_LEAK_9f3a"


def _write_pbip(root: Path, page_name: str) -> Path:
    """Write a minimal PBIP with one PBIR page."""
    pbip = root / "report.pbip"
    page = pbip / "report.Report" / "pages" / page_name
    page.mkdir(parents=True, exist_ok=True)
    (page / "page.json").write_text(
        json.dumps(
            {
                "name": page_name,
                "width": 1280,
                "height": 720,
                "visualContainers": [
                    {
                        "id": MARKER,
                        "config": json.dumps({"name": "card", "query": MARKER}),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    dataset = pbip / "report.Dataset"
    dataset.mkdir(parents=True, exist_ok=True)
    (dataset / "definition.pbism").write_text(
        json.dumps({"model": {"tables": []}}),
        encoding="utf-8",
    )
    return pbip


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A workspace root that is a strict subdirectory of the basetemp."""
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(root))
    return root


@pytest.fixture()
def outside_dir() -> Iterator[Path]:
    """A directory **outside** the workspace root.

    ``tempfile.mkdtemp`` lands in the system temp dir, which is a sibling
    of pytest's basetemp — not a descendant of it — so the path is outside
    whatever root the session fixture configured.
    """
    path = Path(tempfile.mkdtemp(prefix="pbi_outside_"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def inside_pbip(workspace: Path) -> Path:
    return _write_pbip(workspace, "Overview")


@pytest.fixture()
def outside_pbip(outside_dir: Path) -> Path:
    return _write_pbip(outside_dir, OUTSIDE_PAGE_NAME)


class TestTheReadToolsAreBounded:
    """A read-scoped tool must not read outside the workspace."""

    def test_optimize_refuses_a_path_outside_the_workspace(
        self, workspace: Path, outside_pbip: Path
    ) -> None:
        with pytest.raises(ValueError, match="outside allowed workspace root"):
            optimize_report_performance(pbip_path=str(outside_pbip))

    def test_ux_audit_refuses_a_path_outside_the_workspace(
        self, workspace: Path, outside_pbip: Path
    ) -> None:
        with pytest.raises(ValueError, match="outside allowed workspace root"):
            audit_report_ux_and_storytelling(pbip_path=str(outside_pbip))

    def test_optimize_still_works_inside_the_workspace(
        self, workspace: Path, inside_pbip: Path
    ) -> None:
        result = optimize_report_performance(pbip_path=str(inside_pbip))
        assert result.pages_analyzed == 1
        assert result.visuals_analyzed == 1

    def test_ux_audit_still_works_inside_the_workspace(
        self, workspace: Path, inside_pbip: Path
    ) -> None:
        result = audit_report_ux_and_storytelling(pbip_path=str(inside_pbip))
        assert result.pages_analyzed == 1

    def test_the_outside_page_name_never_reaches_a_result(
        self, workspace: Path, outside_pbip: Path
    ) -> None:
        """The leak is the point: a file-derived name must not come back."""
        for call in (
            lambda: optimize_report_performance(pbip_path=str(outside_pbip)),
            lambda: audit_report_ux_and_storytelling(pbip_path=str(outside_pbip)),
        ):
            try:
                payload = call().model_dump_json()
            except ValueError:
                continue
            assert OUTSIDE_PAGE_NAME not in payload
            assert MARKER not in payload


class TestAConfiguredRootWinsOverTheReadOptOut:
    """``PBI_ALLOW_OUTSIDE_WORKSPACE_READ`` must not widen a declared root."""

    def test_the_read_tools_still_refuse(
        self, workspace: Path, outside_pbip: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PBI_ALLOW_OUTSIDE_WORKSPACE_READ", "1")
        with pytest.raises(ValueError, match="outside allowed workspace root"):
            optimize_report_performance(pbip_path=str(outside_pbip))
        with pytest.raises(ValueError, match="outside allowed workspace root"):
            audit_report_ux_and_storytelling(pbip_path=str(outside_pbip))


class TestThePublishingToolsAreBounded:
    """What leaves the process must come from the workspace, always."""

    async def test_deploy_refuses_a_path_outside_the_workspace(
        self, workspace: Path, outside_pbip: Path
    ) -> None:
        with pytest.raises(ValueError, match="outside allowed workspace root"):
            await deploy_to_workspace(
                pbip_path=str(outside_pbip),
                workspace_id="ws",
                dry_run=True,
            )

    def test_sync_git_refuses_a_path_outside_the_workspace(
        self, workspace: Path, outside_dir: Path
    ) -> None:
        with pytest.raises(ValueError, match="outside allowed workspace root"):
            sync_git_to_workspace(
                repo_path=str(outside_dir),
                workspace_id="ws",
                dry_run=True,
            )

    async def test_deploy_refuses_even_with_the_read_opt_out(
        self,
        outside_pbip: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The read opt-out must not widen what gets published."""
        monkeypatch.delenv("PBI_WORKSPACE_ROOT", raising=False)
        monkeypatch.setenv("PBI_ALLOW_OUTSIDE_WORKSPACE_READ", "1")
        with pytest.raises(ValueError, match="outside allowed workspace root"):
            await deploy_to_workspace(
                pbip_path=str(outside_pbip),
                workspace_id="ws",
                dry_run=True,
            )

    def test_sync_git_refuses_even_with_the_read_opt_out(
        self,
        outside_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("PBI_WORKSPACE_ROOT", raising=False)
        monkeypatch.setenv("PBI_ALLOW_OUTSIDE_WORKSPACE_READ", "1")
        with pytest.raises(ValueError, match="outside allowed workspace root"):
            sync_git_to_workspace(
                repo_path=str(outside_dir),
                workspace_id="ws",
                dry_run=True,
            )

    async def test_deploy_inside_the_workspace_is_not_refused(
        self, workspace: Path, inside_pbip: Path
    ) -> None:
        """A path inside the workspace still gets as far as the gate."""
        result = await deploy_to_workspace(
            pbip_path=str(inside_pbip),
            workspace_id="ws",
            dry_run=True,
        )
        # It got past containment and was stopped by the gate instead,
        # which fails closed when there are no findings to clear it.
        assert any("gate_blocked" in e for e in result.errors)
        assert result.planned_actions

    def test_sync_git_inside_the_workspace_is_not_refused(self, workspace: Path) -> None:
        """A path inside the workspace still gets as far as the git check."""
        result = sync_git_to_workspace(
            repo_path=str(workspace),
            workspace_id="ws",
            dry_run=True,
        )
        assert any("not a git repo" in w for w in result.warnings)
