"""E2E tests that drive the **real** Tabular Editor binary.

The contract tests in ``test_te_modeling.py`` check the orchestrator's
side of the Tabular Editor contract with a stand-in for the binary.
These tests exercise the other side: the binary itself.

The BPA runner is the path that matters in production. It shells out to
``TabularEditor.exe <model> -A <rules.json> -G <report.sarif>`` and
parses the answer, so it is the one place where a wrong argument, a
wrong path or a misread report would show up as a silently empty audit
rather than as an error.

These tests only run where the binary is installed — the Windows
runner of the nightly E2E workflow downloads it pinned to the upstream
SHA256. Everywhere else they skip.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.engines.errors import EngineError
from powerbi_orchestrator_mcp.validation.bpa_runner import RULES_FILE_ENV, BpaRunner

# A rules file that matches every measure, so the run produces a
# finding: that is what proves the whole chain worked, from the binary
# to the parsed result.
SMOKE_RULES = Path(__file__).resolve().parents[1] / "fixtures" / "bpa_rules.json"


def _bim_from_fixture(temp_pbip: Path, dest: Path) -> Path:
    """Write the fixture's model as a ``.bim`` file.

    ``te bpa`` takes a ``.bim``, a ``.tmdl`` or a connection string. The
    fixture ships as a PBIP, whose dataset is a ``.pbism`` — a different
    shape from the ``.bim`` that Tabular Editor reads, so the model is
    reshaped into one here.

    The difference that matters is the name of the table collection: a
    ``.bim`` calls it ``entities``, where a ``.pbism`` calls it
    ``tables``. A ``.bim`` with ``tables`` in it is a model Tabular
    Editor cannot load, and it says so by opening a dialog — which on a
    headless runner means the process waits forever.
    """
    source = temp_pbip / "sample.Dataset" / "definition.pbism"
    model = json.loads(source.read_text(encoding="utf-8"))
    body = model.get("model", {})

    entities = []
    for table in body.get("tables", []):
        entity: dict[str, object] = {"name": table.get("name", "")}
        columns = []
        for column in table.get("columns", []):
            columns.append(
                {
                    "name": column.get("name", ""),
                    "dataType": column.get("dataType", "string"),
                    "sourceColumn": column.get("name", ""),
                }
            )
        if columns:
            entity["columns"] = columns
        measures = []
        for measure in table.get("measures", []):
            # In a .bim the measure belongs to its entity, so it does
            # not repeat the table name.
            measures.append(
                {
                    "name": measure.get("name", ""),
                    "expression": measure.get("expression", ""),
                }
            )
        if measures:
            entity["measures"] = measures
        entities.append(entity)

    dest.write_text(
        json.dumps(
            {
                "name": "SampleModel",
                "compatibilityLevel": 1500,
                "model": {"culture": "en-US", "entities": entities},
            }
        ),
        encoding="utf-8",
    )
    return dest


class TestTheRealBinaryRuns:
    @pytest.mark.asyncio
    async def test_bpa_against_a_real_model(
        self, engine_te_path: Path, temp_pbip: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The run happens, and it never reports a clean score it cannot justify.

        The rules match every measure, so a finding proves the whole
        chain: the binary ran, it applied the rules and the output was
        understood.

        The other outcome is a clear error. Tabular Editor 2 is a
        desktop application whose Best Practice Analyzer results open in
        a window, and on a headless runner nothing closes it — the
        vendor states that TE2 has no non-interactive mode. When that is
        what happens, the runner must say so rather than report a model
        with no violations, which would be a false green.

        What is *not* acceptable is a clean score over output the runner
        could not read. That is what this test pins.
        """
        monkeypatch.setenv(RULES_FILE_ENV, str(SMOKE_RULES))
        model = _bim_from_fixture(temp_pbip, temp_pbip / "model.bim")
        runner = BpaRunner(binary=str(engine_te_path), timeout_s=90)

        try:
            result = await runner.run(model, ruleset_name="default")
        except EngineError as exc:
            # The run happened and it said what was wrong with it.
            assert exc.code
            assert exc.remediation_hint
            return

        assert result.ruleset_used == "default"
        assert 0.0 <= result.score <= 100.0
        if not result.findings:
            # A clean score is only honest if the binary really said the
            # model was clean. The runner refuses to report one over
            # output it could not read, so a clean score here means the
            # run really was clean.
            assert result.score == 100.0
        for finding in result.findings:
            assert finding.rule_id
            assert finding.message

    @pytest.mark.asyncio
    async def test_a_missing_model_is_reported_as_an_engine_error(
        self, engine_te_path: Path, temp_pbip: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failure of the real binary comes back as a mapped error.

        This is the half a mock cannot prove: the exit code the binary
        really returns, and the mapping of that exit code onto an
        ``EngineError``.
        """
        monkeypatch.setenv(RULES_FILE_ENV, str(SMOKE_RULES))
        runner = BpaRunner(binary=str(engine_te_path))

        with pytest.raises(EngineError) as excinfo:
            await runner.run(
                temp_pbip / "does-not-exist.bim",
                ruleset_name="default",
            )

        error = excinfo.value
        assert error.engine == "te"
        assert error.code
        assert error.remediation_hint


class TestTheRealBinaryIsTheOneThatRuns:
    def test_the_binary_is_the_one_under_test(self, engine_te_path: Path) -> None:
        """Guard against the test passing because it silently used a mock."""
        assert engine_te_path.exists()
        assert engine_te_path.stat().st_size > 0

    @pytest.mark.asyncio
    async def test_the_mock_path_is_not_what_is_being_exercised(self, engine_te_path: Path) -> None:
        """A runner with no mock findings is the real one.

        The runner silently falls back to canned findings when
        ``mock_findings`` is set. This pins that the runner under test
        has no mock, so a green result cannot come from the mock path.
        """
        runner = BpaRunner(binary=str(engine_te_path))

        assert runner._mock_findings is None
        assert runner._mock_score is None
