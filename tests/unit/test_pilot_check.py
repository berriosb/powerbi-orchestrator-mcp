"""Tests for scripts/pilot_check.py.

The script's central guarantee is that it never mutates a tenant, so the
tests assert that property directly rather than trusting the docstring.
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pilot_check.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("pilot_check", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pilot = _load()


MUTATING_CLIENT_METHODS = (
    "create_item",
    "delete_item",
    "refresh_dataset",
    "cancel_refresh",
    "update_refresh_schedule",
    "take_over_dataset",
    "update_datasource",
    "patch",
    "delete",
)


class TestScriptIsReadOnly:
    def test_script_parses(self) -> None:
        ast.parse(_SCRIPT.read_text(encoding="utf-8"))

    @pytest.mark.parametrize("method", MUTATING_CLIENT_METHODS)
    def test_no_mutating_client_call(self, method: str) -> None:
        source = _SCRIPT.read_text(encoding="utf-8")

        assert f"client.{method}(" not in source, (
            f"pilot_check.py must not call the mutating client method {method}"
        )

    def test_no_write_verbs_outside_readonly_paths(self) -> None:
        source = _SCRIPT.read_text(encoding="utf-8")

        for forbidden in ("POST ", "PATCH ", "DELETE ", "PUT "):
            assert forbidden not in source

    def test_declares_itself_readonly(self) -> None:
        assert "read-only" in _SCRIPT.read_text(encoding="utf-8")


class TestReportRendering:
    def test_counts_are_reported(self) -> None:
        report = pilot.Report(checks=[])
        report.add("a", pilot.OK, "fine")
        report.add("b", pilot.FAIL, "broken")
        report.add("c", pilot.SKIP, "n/a")

        rendered = report.render()

        assert "1 passed, 1 failed, 1 skipped" in rendered
        assert "[ok  ] a: fine" in rendered
        assert report.failures[0].name == "b"

    def test_empty_report_renders(self) -> None:
        assert "0 passed, 0 failed, 0 skipped" in pilot.Report(checks=[]).render()


class TestHelpers:
    def test_truncate_collapses_whitespace(self) -> None:
        assert pilot._truncate("a\n\n   b") == "a b"

    def test_truncate_limits_length(self) -> None:
        result = pilot._truncate("x" * 500, limit=50)

        assert len(result) <= 55
        assert result.endswith("...")

    def test_count_rows_walks_the_response(self) -> None:
        payload = {
            "results": [
                {"tables": [{"rows": [1, 2]}, {"rows": [3]}]},
                {"tables": [{"rows": []}]},
            ]
        }

        assert pilot._count_rows(payload) == 3

    def test_count_rows_tolerates_junk(self) -> None:
        assert pilot._count_rows(None) == 0
        assert pilot._count_rows({"results": "nope"}) == 0
        assert pilot._count_rows({"results": [1, 2]}) == 0


class TestConfigFailurePath:
    @pytest.mark.asyncio
    async def test_missing_credentials_report_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("PBI_AUTH_MODE", "service_principal")

        report = await pilot.run_checks(None, None)

        assert report.failures
        assert report.failures[0].name == "auth.config"

    def test_exit_code_is_nonzero_on_failure(self, tmp_path: Path) -> None:
        import os

        env = dict(os.environ)
        for var in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
            env.pop(var, None)
        env["PBI_AUTH_MODE"] = "service_principal"
        env["PBI_ORCHESTRATOR_HOME"] = str(tmp_path)

        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

        assert result.returncode == 1
        assert "0 passed" in result.stdout

    def test_help_exits_cleanly(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0
        assert "--workspace-id" in result.stdout
        assert "--dataset-id" in result.stdout
