"""Sprint 15 follow-up: tests for the subprocess paths in BpaRunner.

The existing tests in test_validation.py cover the mock path + ruleset
validation. These tests cover the real-binary code paths (build args,
parse output, missing binary, timeout, non-zero exit, malformed JSON).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.engines.errors import (
    EngineCrashedError,
    EngineError,
    EngineNotFoundError,
    EngineOutputParseError,
    EngineTimeoutError,
)
from powerbi_orchestrator_mcp.validation.bpa_runner import (
    SUPPORTED_RULESETS,
    BpaRunner,
)


class TestBuildArgs:
    def test_default_ruleset(self) -> None:
        runner = BpaRunner()
        args = runner._build_args(Path("model.bim"), "default", None)  # noqa: SLF001
        assert args == ["bpa", "model.bim", "--ruleset", "default", "--format", "json"]

    def test_custom_ruleset_overrides_name(self) -> None:
        runner = BpaRunner()
        ruleset = Path("/tmp/custom.json")
        args = runner._build_args(  # noqa: SLF001
            Path("model.bim"), "performance", ruleset
        )
        assert "--ruleset" in args
        assert str(ruleset) in args
        assert "performance" not in args


class TestParseOutput:
    def test_minimal_payload(self) -> None:
        runner = BpaRunner()
        result = runner._parse_output(  # noqa: SLF001
            json.dumps({"score": 92.0, "findings": []}), "default"
        )
        assert result.score == 92.0
        assert result.findings == []
        assert result.ruleset_used == "default"

    def test_full_payload_te_keys(self) -> None:
        runner = BpaRunner()
        stdout = json.dumps(
            {
                "score": 75.0,
                "findings": [
                    {
                        "RuleId": "R1",
                        "RuleName": "Avoid Filter()",
                        "Severity": "error",
                        "ObjectName": "Sales[TotalAmount]",
                        "ObjectType": "measure",
                        "Message": "Use CALCULATE",
                        "CanFix": True,
                        "FixExpression": "CALCULATE([M])",
                    }
                ],
            }
        )
        result = runner._parse_output(stdout, "performance")  # noqa: SLF001
        assert result.score == 75.0
        f = result.findings[0]
        assert f.rule_id == "R1"
        assert f.rule_name == "Avoid Filter()"
        assert f.severity == "error"
        assert f.object_name == "Sales[TotalAmount]"
        assert f.object_type == "measure"
        assert f.message == "Use CALCULATE"
        assert f.auto_fixable is True
        assert f.fix_suggestion == "CALCULATE([M])"
        assert result.ruleset_used == "performance"

    def test_payload_snake_case_keys(self) -> None:
        """Lowercase snake_case keys are also accepted (defensive)."""
        runner = BpaRunner()
        stdout = json.dumps(
            {
                "score": 50.0,
                "findings": [
                    {
                        "rule_id": "R1",
                        "rule_name": "Test",
                        "severity": "warning",
                        "object_name": "Sales",
                        "object_type": "table",
                        "message": "x",
                    }
                ],
            }
        )
        result = runner._parse_output(stdout, "default")  # noqa: SLF001
        assert result.findings[0].rule_id == "R1"
        assert result.findings[0].severity == "warning"

    def test_malformed_json_raises_parse_error(self) -> None:
        runner = BpaRunner()
        with pytest.raises(EngineOutputParseError):
            runner._parse_output("{ not json", "default")  # noqa: SLF001


class TestRunSubprocess:
    async def test_missing_binary_raises_engine_not_found(
        self, tmp_path: Path
    ) -> None:
        # A binary path that cannot exist on PATH.
        runner = BpaRunner(binary="/nonexistent/te-binary")
        target = tmp_path / "model.bim"
        target.write_text("")
        with pytest.raises(EngineNotFoundError) as ei:
            await runner.run(target)
        assert "/nonexistent/te-binary" in str(ei.value)

    async def test_binary_exit_nonzero_raises_crashed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_communicate():
            return (b"", b"some te error message")

        class FakeProc:
            returncode = 2

            async def communicate(self) -> tuple[bytes, bytes]:
                return await fake_communicate()

            async def wait(self) -> None:
                return None

            def kill(self) -> None:
                pass

        async def fake_exec(*_args: object, **_kwargs: object) -> FakeProc:
            return FakeProc()

        monkeypatch.setattr(
            "asyncio.create_subprocess_exec", fake_exec
        )
        runner = BpaRunner(binary="te2")
        target = tmp_path / "model.bim"
        target.write_text("")
        with pytest.raises(EngineCrashedError):
            await runner.run(target)

    async def test_timeout_raises_engine_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                raise TimeoutError

            async def wait(self) -> None:
                return None

            def kill(self) -> None:
                pass

        async def fake_exec(*_a: object, **_kw: object) -> FakeProc:
            return FakeProc()

        monkeypatch.setattr(
            "asyncio.create_subprocess_exec", fake_exec
        )
        runner = BpaRunner(binary="te2", timeout_s=1)
        target = tmp_path / "model.bim"
        target.write_text("")
        with pytest.raises(EngineTimeoutError) as ei:
            await runner.run(target)
        assert ei.value.timeout_s == 1

    async def test_successful_run_parses_stdout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                payload = json.dumps(
                    {"score": 88.0, "findings": []}
                )
                return (payload.encode("utf-8"), b"")

            async def wait(self) -> None:
                return None

            def kill(self) -> None:
                pass

        async def fake_exec(*_a: object, **_kw: object) -> FakeProc:
            return FakeProc()

        monkeypatch.setattr(
            "asyncio.create_subprocess_exec", fake_exec
        )
        runner = BpaRunner(binary="te2")
        target = tmp_path / "model.bim"
        target.write_text("")
        result = await runner.run(target)
        assert result.score == 88.0
        assert result.ruleset_used == "default"


class TestPublicApi:
    def test_supported_rulesets_frozen(self) -> None:
        assert frozenset(
            {"default", "performance", "governance"}
        ) == SUPPORTED_RULESETS

    def test_default_timeout_constant(self) -> None:
        from powerbi_orchestrator_mcp.validation.bpa_runner import (
            DEFAULT_TIMEOUT_S,
        )

        assert DEFAULT_TIMEOUT_S == 60

    def test_unknown_ruleset_without_custom_raises(
        self, tmp_path: Path
    ) -> None:
        import asyncio

        async def _go() -> None:
            runner = BpaRunner()
            target = tmp_path / "model.bim"
            target.write_text("")
            with pytest.raises(EngineError):
                await runner.run(
                    target, ruleset_name="not-a-real-ruleset"
                )

        asyncio.run(_go())
