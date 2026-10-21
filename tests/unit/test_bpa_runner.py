"""Tests for the subprocess paths in BpaRunner.

The real-binary code paths: the command line, the console output, the
missing binary, the timeout, the non-zero exit and the output that says
nothing.

The command line and the output follow **Tabular Editor 2**:
``<model> -A <rules.json> -V``. Tabular Editor 2 has no structured BPA
output, so the findings are read out of the console annotations. The
previous invocation and output schema matched no real Tabular Editor,
which is why nothing here had ever been run against the binary.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.engines.errors import (
    EngineAuthError,
    EngineError,
    EngineNotFoundError,
    EngineTimeoutError,
    EngineValidationError,
)
from powerbi_orchestrator_mcp.validation.bpa_runner import (
    RULES_FILE_ENV,
    SUPPORTED_RULESETS,
    BpaRunner,
)


def _violation(
    severity: str = "error",
    obj: str = "Sales[TotalAmount]",
    rule: str = "DoNotSummarizeByDefault",
) -> str:
    """A VSTS-annotated violation line, the way Tabular Editor writes it."""
    return f"##vso[task.logissue type={severity};]The column '{obj}' violates rule '{rule}'."


class TestBuildArgs:
    def test_the_command_line_follows_the_documented_flags(self) -> None:
        runner = BpaRunner()
        args = runner._build_args(Path("model.bim"), "/rules.json")  # noqa: SLF001
        assert args == [
            "model.bim",
            "-A",
            "/rules.json",
            "-V",
        ]

    def test_the_model_is_the_first_argument(self) -> None:
        """Tabular Editor takes the model positionally, before the flags."""
        runner = BpaRunner()
        args = runner._build_args(Path("model.bim"), "/rules.json")  # noqa: SLF001
        assert args[0] == "model.bim"

    def test_the_rules_file_is_passed_with_dash_a(self) -> None:
        runner = BpaRunner()
        args = runner._build_args(Path("model.bim"), "/rules.json")  # noqa: SLF001
        assert args[args.index("-A") + 1] == "/rules.json"

    def test_the_output_is_vsts_annotations(self) -> None:
        """``-V`` is VSTS annotations. ``-G`` would be GitHub's, and
        neither of them is a report file."""
        runner = BpaRunner()
        args = runner._build_args(Path("model.bim"), "/rules.json")  # noqa: SLF001
        assert "-V" in args
        assert "-G" not in args


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


class TestParseOutput:
    def test_no_output_is_no_findings(self) -> None:
        runner = BpaRunner()
        assert runner._parse_output("") == []  # noqa: SLF001

    def test_output_without_violations_is_no_findings(self) -> None:
        runner = BpaRunner()
        assert runner._parse_output("Tabular Editor 2.20.0\n") == []  # noqa: SLF001

    def test_a_violation_is_normalised(self) -> None:
        runner = BpaRunner()
        output = _violation("error", "Sales[TotalAmount]", "DoNotSummarizeByDefault")

        findings = runner._parse_output(output)  # noqa: SLF001

        assert len(findings) == 1
        finding = findings[0]
        assert finding.rule_id == "DoNotSummarizeByDefault"
        assert finding.severity == "error"
        assert finding.object_name == "Sales[TotalAmount]"
        assert "violates rule" in finding.message

    def test_the_severity_comes_from_the_annotation(self) -> None:
        runner = BpaRunner()

        error = runner._parse_output(_violation("error"))  # noqa: SLF001
        warning = runner._parse_output(_violation("warning"))  # noqa: SLF001

        assert error[0].severity == "error"
        assert warning[0].severity == "warning"

    def test_a_violation_without_an_annotation_is_still_a_finding(self) -> None:
        """The severity is not in the text, but the violation is."""
        runner = BpaRunner()
        output = "The column 'Sales[Amount]' violates rule 'DoNotSummarizeByDefault'."

        findings = runner._parse_output(output)  # noqa: SLF001

        assert len(findings) == 1
        assert findings[0].severity == "info"

    def test_several_violations_are_all_read(self) -> None:
        runner = BpaRunner()
        output = "\n".join(
            [
                _violation("error", "Sales[Amount]", "RuleOne"),
                _violation("warning", "Sales[Quantity]", "RuleTwo"),
            ]
        )

        findings = runner._parse_output(output)  # noqa: SLF001

        assert [f.rule_id for f in findings] == ["RuleOne", "RuleTwo"]


class TestRunSubprocess:
    def _fake_process(self, exit_code: int, stdout: bytes = b"", stderr: bytes = b"") -> object:
        class FakeProc:
            def __init__(self) -> None:
                self.returncode = exit_code

            async def communicate(self) -> tuple[bytes, bytes]:
                return (stdout, stderr)

            async def wait(self) -> None:
                return None

            def kill(self) -> None:
                pass

        return FakeProc()

    async def test_missing_binary_raises_engine_not_found(self, tmp_path: Path) -> None:
        runner = BpaRunner(binary="/nonexistent/te-binary", rules_file="/rules.json")
        target = tmp_path / "model.bim"
        target.write_text("")

        with pytest.raises(EngineNotFoundError) as ei:
            await runner.run(target)

        assert "/nonexistent/te-binary" in str(ei.value)

    async def test_a_clean_run_with_no_violations_scores_full(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_exec(*_a: object, **_kw: object) -> object:
            return self._fake_process(0, stdout=b"Tabular Editor 2.20.0\n")

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        runner = BpaRunner(binary="te2", rules_file="/rules.json")
        target = tmp_path / "model.bim"
        target.write_text("")

        result = await runner.run(target)

        assert result.score == 100.0
        assert result.findings == []

    async def test_a_violation_is_a_finding_not_a_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tabular Editor returns 1 when it produced an Error-level output.

        A severity-3 BPA violation *is* an Error-level output, so exit 1
        with the violation in the output means "the model has problems",
        not "the tool failed". Treating it as a failure would throw away
        the findings the run just produced.
        """
        output = _violation("error").encode("utf-8")

        async def fake_exec(*_a: object, **_kw: object) -> object:
            return self._fake_process(1, stdout=output)

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        runner = BpaRunner(binary="te2", rules_file="/rules.json")
        target = tmp_path / "model.bim"
        target.write_text("")

        result = await runner.run(target)

        assert len(result.findings) == 1
        assert result.findings[0].rule_id == "DoNotSummarizeByDefault"
        assert result.score < 100.0

    async def test_a_non_zero_exit_without_violations_is_a_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 1 with nothing in the output is the tool failing."""

        async def fake_exec(*_a: object, **_kw: object) -> object:
            return self._fake_process(1, stdout=b"", stderr=b"te said no")

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        runner = BpaRunner(binary="te2", rules_file="/rules.json")
        target = tmp_path / "model.bim"
        target.write_text("")

        with pytest.raises(EngineError):
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
        async def fake_exec(*_a: object, **_kw: object) -> object:
            return self._fake_process(exit_code, stderr=b"te said no")

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        runner = BpaRunner(binary="te", rules_file="/rules.json")
        target = tmp_path / "model.bim"
        target.write_text("")

        with pytest.raises(expected):
            await runner.run(target)

    async def test_a_run_that_says_nothing_is_not_a_clean_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Silence is not a pass. Reporting "100, no findings" here would
        be a false green."""

        async def fake_exec(*_a: object, **_kw: object) -> object:
            return self._fake_process(0, stdout=b"", stderr=b"")

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        runner = BpaRunner(binary="te2", rules_file="/rules.json")
        target = tmp_path / "model.bim"
        target.write_text("")

        with pytest.raises(EngineError) as ei:
            await runner.run(target)

        assert ei.value.code == "engine_output_parse_error"

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

        async def fake_exec(*_a: object, **_kw: object) -> object:
            return FakeProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        runner = BpaRunner(binary="te2", timeout_s=1, rules_file="/rules.json")
        target = tmp_path / "model.bim"
        target.write_text("")

        with pytest.raises(EngineTimeoutError) as ei:
            await runner.run(target)

        assert ei.value.timeout_s == 1


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

    def test_the_score_is_derived_from_the_findings(self) -> None:
        """Tabular Editor produces no score, so the audit derives one."""
        from powerbi_orchestrator_mcp.validation.bpa_runner import (
            BpaFinding,
            _score_from,
        )

        findings = [
            BpaFinding(
                rule_id="R",
                rule_name="R",
                severity="error",
                object_name="X",
                object_type="",
                message="boom",
            ),
            BpaFinding(
                rule_id="R",
                rule_name="R",
                severity="warning",
                object_name="Y",
                object_type="",
                message="smell",
            ),
        ]

        # 100 - 10 (error) - 3 (warning)
        assert _score_from(findings) == 87.0
        assert _score_from([]) == 100.0

    def test_the_json_shape_is_not_used_anymore(self) -> None:
        """The old invented shape is gone, not just unused."""
        source = Path(
            "/home/bastianberrios/Proyectos/powerbi-orchestrator-mcp/"
            "src/powerbi_orchestrator_mcp/validation/bpa_runner.py"
        ).read_text(encoding="utf-8")
        assert "sarif" not in source.lower()
        assert json.dumps({"score": 1})
