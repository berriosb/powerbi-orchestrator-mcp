"""Sprint 16: tests for the standalone CLI.

Calls ``cli.main()`` in-process (no subprocess) so the tests work
identically on Linux, macOS, and Windows. Subprocess-based CLI
invocation is exercised by ``tests/integration/test_real_pbip_e2e.py``
(TestCliOnRealFixture), which is skipif-Windows to avoid a known
asyncio + subprocess WinError 10106 in the GitHub Actions Windows
runner.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp import cli


def _run_cli(*args: str) -> tuple[int, str, str]:
    """Invoke the CLI in-process; return (rc, stdout, stderr)."""
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        try:
            rc = cli.main(list(args))
        except SystemExit as exc:
            rc = int(exc.code or 0)
    return rc, out_buf.getvalue(), err_buf.getvalue()


class TestCliVersion:
    def test_version_exits_zero(self) -> None:
        rc, stdout, _ = _run_cli("version")
        assert rc == 0
        assert "powerbi-orchestrator-mcp" in stdout
        assert "Python" in stdout


class TestCliInit:
    def test_creates_minimal_pbip(self, tmp_path: Path) -> None:
        target = tmp_path / "new.pbip"
        rc, _, _ = _run_cli("init", str(target))
        assert rc == 0
        # ``init`` strips the .pbip suffix and uses the basename as the
        # project name. So target/new.pbip → new.<Dataset|Report>/ +
        # new.pbip (metadata file).
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
        rc, _, stderr = _run_cli("init", str(target))
        assert rc == 2
        assert "already exists" in stderr

    def test_force_overwrites(self, tmp_path: Path) -> None:
        target = tmp_path / "exists.pbip"
        target.mkdir()
        rc, _, _ = _run_cli("init", str(target), "--force")
        assert rc == 0
        assert (target / "exists.pbip").exists()


class TestCliInspect:
    def test_inspects_a_real_pbip(self, tmp_path: Path) -> None:
        target = tmp_path / "inspectable.new"
        _run_cli("init", str(target))

        rc, stdout, _ = _run_cli("inspect", str(target))
        assert rc == 0
        body = json.loads(stdout)
        assert body["pbip_path"] == str(target)
        assert "Overview" in [p["name"] for p in body["pages"]]

    def test_missing_path_returns_2(self) -> None:
        rc, _, stderr = _run_cli("inspect", "/tmp/does-not-exist-xyz")
        assert rc == 2
        assert "does not exist" in stderr


class TestCliValidate:
    def test_validates_a_real_pbip(self, tmp_path: Path) -> None:
        target = tmp_path / "validatable.new"
        _run_cli("init", str(target))

        rc, stdout, _ = _run_cli(
            "validate",
            str(target),
            "--format",
            "json",
            "--min-score",
            "0",
        )
        assert rc == 0
        body = json.loads(stdout)
        assert body["composite_score"] >= 0.0
        assert body["findings_count"] >= 0

    def test_exits_2_on_missing_pbip(self) -> None:
        rc, _, _ = _run_cli(
            "validate", "/tmp/does-not-exist-xyz", "--min-score", "0"
        )
        assert rc == 2

    def test_exits_1_when_score_below_threshold(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "lowscore.new"
        _run_cli("init", str(target))
        rc, _, _ = _run_cli("validate", str(target), "--min-score", "9999")
        assert rc == 1


class TestCliHealth:
    def test_prints_health_snapshot(self) -> None:
        rc, stdout, _ = _run_cli("health")
        assert rc == 0
        body = json.loads(stdout)
        assert "server_version" in body
        assert "checks" in body


class TestCliHelp:
    def test_root_help(self) -> None:
        rc, stdout, _ = _run_cli("--help")
        assert rc == 0
        for subcommand in (
            "validate",
            "inspect",
            "init",
            "audit-verify",
            "health",
            "version",
        ):
            assert subcommand in stdout

    def test_validate_help(self) -> None:
        rc, stdout, _ = _run_cli("validate", "--help")
        assert rc == 0
        assert "--min-score" in stdout
        assert "--bpa-ruleset" in stdout

    def test_init_help(self) -> None:
        rc, stdout, _ = _run_cli("init", "--help")
        assert rc == 0
        assert "--force" in stdout


class TestMainEntry:
    """Direct invocation of cli.main()."""

    def test_main_returns_int(self) -> None:
        rc = cli.main(["version"])
        assert isinstance(rc, int)
        assert rc == 0

    def test_main_with_no_args_exits_2(self) -> None:
        # argparse exits with code 2 when a required subcommand is
        # missing; argparse raises SystemExit to do so.
        with pytest.raises(SystemExit):
            cli.main([])
