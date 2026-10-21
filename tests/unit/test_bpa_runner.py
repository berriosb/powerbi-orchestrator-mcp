"""Tests for the subprocess paths in BpaRunner.

The real-binary code paths: the command line, the SARIF report, the
missing binary, the timeout, the non-zero exit and the malformed
report.

The command line and the report format follow **Tabular Editor 2**:
``<model> -A <rules.json> -G <report.sarif>``, with the results in
SARIF. The previous invocation and output schema matched no real
Tabular Editor, which is why nothing here had ever been run against
the binary.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.engines.errors import (
    EngineAuthError,
    EngineError,
    EngineNotFoundError,
    EngineOutputParseError,
    EngineTimeoutError,
    EngineValidationError,
)
from powerbi_orchestrator_mcp.validation.bpa_runner import (
    RULES_FILE_ENV,
    SUPPORTED_RULESETS,
    BpaRunner,
)


def _sarif(results: list[dict], rules: list[dict] | None = None) -> str:
    return json.dumps(
        {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "Tabular Editor",
                            "rules": rules
                            if rules is not None
                            else [
                                {
                                    "id": "DoNotSummarizeByDefault",
                                    "shortDescription": {"text": "Do not summarize by default"},
                                }
                            ],
                        }
                    },
                    "results": results,
                }
            ],
        }
    )


class TestBuildArgs:
    def test_the_command_line_follows_the_documented_flags(self) -> None:
        runner = BpaRunner()
        args = runner._build_args(  # noqa: SLF001
            Path("model.bim"), "/rules.json", Path("/out/bpa.sarif")
        )
        assert args == [
            "model.bim",
            "-A",
            "/rules.json",
            "-G",
            "/out/bpa.sarif",
        ]

    def test_the_model_is_the_first_argument(self) -> None:
        """Tabular Editor takes the model positionally, before the flags."""
        runner = BpaRunner()
        args = runner._build_args(  # noqa: SLF001
            Path("model.bim"), "/rules.json", Path("/out/bpa.sarif")
        )
        assert args[0] == "model.bim"

    def test_the_rules_file_is_passed_with_dash_a(self) -> None:
        runner = BpaRunner()
        args = runner._build_args(  # noqa: SLF001
            Path("model.bim"), "/rules.json", Path("/out/bpa.sarif")
        )
        assert args[args.index("-A") + 1] == "/rules.json"

    def test_the_sarif_report_is_passed_with_dash_g(self) -> None:
        runner = BpaRunner()
        args = runner._build_args(  # noqa: SLF001
            Path("model.bim"), "/rules.json", Path("/out/bpa.sarif")
        )
        assert args[args.index("-G") + 1] == "/out/bpa.sarif"


class TestRulesResolution:
    def test_the_environment_provides_the_rules(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(RULES_FILE_ENV, "/env/rules.json")
        runner = BpaRunner()

        assert runner._resolve_rules(None) == "/env/rules.json"  # noqa: SLF001

    def test_the_constructor_wins_over_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(RULES_FILE_ENV, "/env/rules.json")
        runner = BpaRunner(rules_file="/ctor/rules.json")

        assert runner._resolve_rules(None) == "/ctor/rules.json"  # noqa: SLF001

    def test_the_caller_wins_over_everything(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(RULES_FILE_ENV, "/env/rules.json")
        runner = BpaRunner(rules_file="/ctor/rules.json")

        assert runner._resolve_rules(tmp_path / "custom.json") == str(  # noqa: SLF001
            tmp_path / "custom.json"
        )

    def test_without_rules_there_is_nothing(self) -> None:
        runner = BpaRunner()

        assert runner._resolve_rules(None) is None  # noqa: SLF001


class TestNoRulesMeansNoRun:
    async def test_the_check_is_refused_rather_than_reporting_a_clean_score(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without rules the result would be a clean score that means nothing.

        Tabular Editor runs the rules of the machine it runs on, and a
        server has none. Reporting "100, no findings" would be a false
        green, so the check is refused instead.
        """
        monkeypatch.delenv(RULES_FILE_ENV, raising=False)
        runner = BpaRunner(binary="te")
        target = tmp_path / "model.bim"
        target.write_text("")

        with pytest.raises(EngineError) as ei:
            await runner.run(target)

        assert ei.value.code == "engine_rules_unavailable"
        assert RULES_FILE_ENV in str(ei.value)


class TestParseSarif:
    def test_an_empty_report_scores_full(self) -> None:
        runner = BpaRunner()
        result = runner._parse_sarif(_sarif([]), "default")  # noqa: SLF001

        assert result.score == 100.0
        assert result.findings == []
        assert result.ruleset_used == "default"

    def test_a_finding_is_normalised(self) -> None:
        runner = BpaRunner()
        sarif = _sarif(
            [
                {
                    "ruleId": "DoNotSummarizeByDefault",
                    "level": "warning",
                    "message": {"text": "Column is summarized by default"},
                    "locations": [
                        {"logicalLocations": [{"name": "Sales[TotalAmount]", "kind": "measure"}]}
                    ],
                }
            ]
        )

        result = runner._parse_sarif(sarif, "default")  # noqa: SLF001

        assert len(result.findings) == 1
        finding = result.findings[0]
        assert finding.rule_id == "DoNotSummarizeByDefault"
        assert finding.rule_name == "Do not summarize by default"
        assert finding.severity == "warning"
        assert finding.object_name == "Sales[TotalAmount]"
        assert finding.object_type == "measure"
        assert finding.message == "Column is summarized by default"

    def test_the_severity_comes_from_the_sarif_level(self) -> None:
        runner = BpaRunner()
        sarif = _sarif(
            [
                {
                    "ruleId": "R",
                    "level": "error",
                    "message": {"text": "boom"},
                }
            ]
        )

        result = runner._parse_sarif(sarif, "default")  # noqa: SLF001

        assert result.findings[0].severity == "error"

    def test_a_note_is_info(self) -> None:
        runner = BpaRunner()
        sarif = _sarif([{"ruleId": "R", "level": "note", "message": {"text": "fyi"}}])

        result = runner._parse_sarif(sarif, "default")  # noqa: SLF001

        assert result.findings[0].severity == "info"

    def test_the_score_drops_with_the_severity(self) -> None:
        runner = BpaRunner()
        sarif = _sarif(
            [
                {"ruleId": "R", "level": "error", "message": {"text": "boom"}},
                {"ruleId": "R", "level": "warning", "message": {"text": "smell"}},
            ]
        )

        result = runner._parse_sarif(sarif, "default")  # noqa: SLF001

        # 100 - 10 (error) - 3 (warning)
        assert result.score == 87.0

    def test_the_score_never_goes_below_zero(self) -> None:
        runner = BpaRunner()
        sarif = _sarif(
            [{"ruleId": f"R{i}", "level": "error", "message": {"text": "x"}} for i in range(20)]
        )

        result = runner._parse_sarif(sarif, "default")  # noqa: SLF001

        assert result.score == 0.0

    def test_malformed_json_raises_parse_error(self) -> None:
        runner = BpaRunner()
        with pytest.raises(EngineOutputParseError):
            runner._parse_sarif("{ not json", "default")  # noqa: SLF001


class TestRunSubprocess:
    async def test_missing_binary_raises_engine_not_found(self, tmp_path: Path) -> None:
        runner = BpaRunner(binary="/nonexistent/te-binary", rules_file="/rules.json")
        target = tmp_path / "model.bim"
        target.write_text("")

        with pytest.raises(EngineNotFoundError) as ei:
            await runner.run(target)

        assert "/nonexistent/te-binary" in str(ei.value)

    async def test_binary_exit_nonzero_is_mapped_to_its_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit code 2 is an authentication failure, not a crash."""

        class FakeProc:
            returncode = 2

            async def communicate(self) -> tuple[bytes, bytes]:
                return (b"", b"some te error message")

            async def wait(self) -> None:
                return None

            def kill(self) -> None:
                pass

        async def fake_exec(*_args: object, **_kwargs: object) -> FakeProc:
            return FakeProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        runner = BpaRunner(binary="te2", rules_file="/rules.json")
        target = tmp_path / "model.bim"
        target.write_text("")

        with pytest.raises(EngineAuthError):
            await runner.run(target)

    @pytest.mark.parametrize(
        ("exit_code", "expected"),
        [
            (1, EngineValidationError),
            (2, EngineAuthError),
            (3, EngineTimeoutError),
            (127, EngineNotFoundError),
        ],
    )
    async def test_every_exit_code_maps_to_its_own_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        exit_code: int,
        expected: type[Exception],
    ) -> None:
        class FakeProc:
            returncode = exit_code

            async def communicate(self) -> tuple[bytes, bytes]:
                return (b"", b"te said no")

            async def wait(self) -> None:
                return None

            def kill(self) -> None:
                pass

        async def fake_exec(*_args: object, **_kwargs: object) -> FakeProc:
            return FakeProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        runner = BpaRunner(binary="te", rules_file="/rules.json")
        target = tmp_path / "model.bim"
        target.write_text("")

        with pytest.raises(expected):
            await runner.run(target)

    async def test_the_timeout_env_var_has_an_effect(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The hint the error gives points at a variable that does work."""
        monkeypatch.setenv("PBI_ENGINE_TIMEOUT_TE_S", "120")
        runner = BpaRunner(binary="te")

        assert runner._timeout_s is None  # noqa: SLF001
        from powerbi_orchestrator_mcp.engines.timeouts import resolve_timeout

        assert resolve_timeout("te", requested_s=runner._timeout_s) == 120  # noqa: SLF001

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

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        runner = BpaRunner(binary="te2", timeout_s=1, rules_file="/rules.json")
        target = tmp_path / "model.bim"
        target.write_text("")

        with pytest.raises(EngineTimeoutError) as ei:
            await runner.run(target)

        assert ei.value.timeout_s == 1

    async def test_a_successful_run_reads_the_sarif_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The report is a file, not the process's stdout."""

        class FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return (b"", b"")

            async def wait(self) -> None:
                return None

            def kill(self) -> None:
                pass

        async def fake_exec(*args: object, **_kwargs: object) -> FakeProc:
            # The SARIF path is the argument after -G.
            sarif_path = Path(str(args[args.index("-G") + 1]))
            sarif_path.write_text(_sarif([]), encoding="utf-8")
            return FakeProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        runner = BpaRunner(binary="te2", rules_file="/rules.json")
        target = tmp_path / "model.bim"
        target.write_text("")

        result = await runner.run(target)

        assert result.score == 100.0
        assert result.ruleset_used == "default"

    async def test_a_run_that_writes_no_report_is_a_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No report is not a clean model."""

        class FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return (b"", b"")

            async def wait(self) -> None:
                return None

            def kill(self) -> None:
                pass

        async def fake_exec(*_args: object, **_kwargs: object) -> FakeProc:
            return FakeProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        runner = BpaRunner(binary="te2", rules_file="/rules.json")
        target = tmp_path / "model.bim"
        target.write_text("")

        with pytest.raises(EngineError) as ei:
            await runner.run(target)

        assert ei.value.code == "engine_output_parse_error"


class TestPublicApi:
    def test_supported_rulesets_frozen(self) -> None:
        assert frozenset({"default", "performance", "governance"}) == SUPPORTED_RULESETS

    def test_default_timeout_constant(self) -> None:
        from powerbi_orchestrator_mcp.validation.bpa_runner import (
            DEFAULT_TIMEOUT_S,
        )

        assert DEFAULT_TIMEOUT_S == 60

    def test_unknown_ruleset_without_custom_raises(self, tmp_path: Path) -> None:
        import asyncio

        async def _go() -> None:
            runner = BpaRunner(rules_file="/rules.json")
            target = tmp_path / "model.bim"
            target.write_text("")
            with pytest.raises(EngineError):
                await runner.run(target, ruleset_name="not-a-real-ruleset")

        asyncio.run(_go())
