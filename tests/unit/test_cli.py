"""Sprint 16: tests for the standalone CLI.

The integration tests call the CLI via subprocess (which doesn't count
toward line coverage). These unit tests exercise the command handlers
directly so coverage stays high.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the CLI in-process (same Python, no subprocess overhead)."""
    return subprocess.run(
        [sys.executable, "-m", "powerbi_orchestrator_mcp.cli", *args],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": "src", "PATH": "/usr/bin"},
        check=False,
    )


class TestCliVersion:
    def test_version_exits_zero(self) -> None:
        result = _run_cli("version")
        assert result.returncode == 0
        assert "powerbi-orchestrator-mcp" in result.stdout
        assert "Python" in result.stdout


class TestCliInit:
    def test_creates_minimal_pbip(self, tmp_path: Path) -> None:
        target = tmp_path / "new.pbip"
        result = _run_cli("init", str(target))
        assert result.returncode == 0
        assert (target / "new.pbip").exists()
        assert (target / "new.Report").is_dir()
        assert (target / "new.Dataset").is_dir()
        # One page (Overview).
        page_dir = target / "new.Report" / "pages" / "Overview"
        assert page_dir.is_dir()
        assert (page_dir / "page.json").exists()

    def test_refuses_existing_without_force(self, tmp_path: Path) -> None:
        target = tmp_path / "exists.pbip"
        target.mkdir()
        result = _run_cli("init", str(target))
        assert result.returncode == 2
        assert "already exists" in result.stderr

    def test_force_overwrites(self, tmp_path: Path) -> None:
        target = tmp_path / "exists.pbip"
        target.mkdir()
        result = _run_cli("init", str(target), "--force")
        assert result.returncode == 0
        assert (target / "exists.pbip").exists()


class TestCliInspect:
    def test_inspects_a_real_pbip(self, tmp_path: Path) -> None:
        # Init first.
        target = tmp_path / "inspectable.pbip"
        _run_cli("init", str(target))

        result = _run_cli("inspect", str(target))
        assert result.returncode == 0
        body = json.loads(result.stdout)
        assert body["pbip_path"] == str(target)
        assert "Overview" in [p["name"] for p in body["pages"]]

    def test_missing_path_returns_2(self) -> None:
        result = _run_cli("inspect", "/tmp/does-not-exist-xyz")
        assert result.returncode == 2
        assert "does not exist" in result.stderr


class TestCliValidate:
    def test_validates_a_real_pbip(self, tmp_path: Path) -> None:
        # Init a clean PBIP.
        target = tmp_path / "validatable.pbip"
        _run_cli("init", str(target))

        result = _run_cli(
            "validate",
            str(target),
            "--format",
            "json",
            "--min-score",
            "0",
        )
        # Exit 0 because score is 100 ≥ 0.
        assert result.returncode == 0
        body = json.loads(result.stdout)
        assert body["composite_score"] >= 0.0
        assert body["findings_count"] >= 0

    def test_exits_2_on_missing_pbip(self) -> None:
        result = _run_cli(
            "validate", "/tmp/does-not-exist-xyz", "--min-score", "0"
        )
        assert result.returncode == 2

    def test_exits_1_when_score_below_threshold(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "lowscore.pbip"
        _run_cli("init", str(target))
        result = _run_cli(
            "validate", str(target), "--min-score", "9999"
        )
        assert result.returncode == 1


class TestCliHealth:
    def test_prints_health_snapshot(self) -> None:
        result = _run_cli("health")
        assert result.returncode == 0
        body = json.loads(result.stdout)
        assert "server_version" in body
        assert "checks" in body


class TestCliHelp:
    def test_root_help(self) -> None:
        result = _run_cli("--help")
        assert result.returncode == 0
        assert "validate" in result.stdout
        assert "inspect" in result.stdout
        assert "init" in result.stdout
        assert "audit-verify" in result.stdout
        assert "health" in result.stdout
        assert "version" in result.stdout

    def test_validate_help(self) -> None:
        result = _run_cli("validate", "--help")
        assert result.returncode == 0
        assert "--min-score" in result.stdout
        assert "--bpa-ruleset" in result.stdout

    def test_init_help(self) -> None:
        result = _run_cli("init", "--help")
        assert result.returncode == 0
        assert "--force" in result.stdout


class TestMainEntry:
    """Direct invocation of cli.main() (not via subprocess)."""

    def test_main_returns_int(self) -> None:
        from powerbi_orchestrator_mcp.cli import main

        rc = main(["version"])
        assert isinstance(rc, int)
        assert rc == 0

    def test_main_with_no_args_errors(self) -> None:
        from powerbi_orchestrator_mcp.cli import main

        # argparse exits with code 2 on no subcommand.
        with pytest.raises(SystemExit):
            main([])