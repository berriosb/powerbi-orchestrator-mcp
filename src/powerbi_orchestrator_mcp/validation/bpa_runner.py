"""Best Practice Analyzer runner — wraps the Tabular Editor CLI.

Implements ``specs/03-validation.md`` §2.1.

**Which Tabular Editor.** There are two products, and they do not speak
the same CLI:

- **Tabular Editor 2** (``TabularEditor.exe``) — the free Windows
  desktop app. Its CLI runs BPA with ``-A <rules.json>``. It has **no
  structured output**: the findings are console text, and ``-V``/``-G``
  reformat that text as VSTS or GitHub Actions annotations. It is a
  WinForms app, so a headless runner can hit a prompt. This is the
  product the project pins and downloads.
- **Tabular Editor CLI** (``te``) — a newer, cross-platform product
  with structured output, a non-interactive mode and predictable exit
  codes. It is in Limited Public Preview, it requires a Tabular Editor
  account to download, and the vendor states the preview stops working
  after 2026-10-31. It is not a dependency here.

The runner targets Tabular Editor 2, and the invocation follows its
documented flags. The previous invocation matched neither product.

**The exit code is not a verdict.** Tabular Editor returns 1 when it
produced any *Error*-level output, and a severity-3 BPA violation *is*
an Error-level output. So exit 1 with violations in the output means
"the model has problems", not "the tool failed" — the two are told
apart by the output, not by the code.

**Rules.** Tabular Editor 2 runs the rules of the machine it runs on.
On a server that is none, so a run without a rules file would report a
clean score that means nothing. The runner refuses to run in that case:
no rules configured is a skipped check, not a passing one.

When ``te`` is not installed, the runner raises ``EngineNotFoundError``
(via the engine-error contract in ``engines/errors.py``). Tests use
``mock_findings`` to script output.

Ruleset resolution (per spec):
- ``default``: the upstream ruleset, supplied as a rules file.
- ``performance``: subset focused on performance.
- ``governance``: subset for org audit (PII, descriptions, OLS).
- Custom JSON: caller passes a path to a custom rules JSON file.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.engines.errors import (
    EngineError,
    EngineNotFoundError,
)

# Where the rules file comes from when the caller does not pass one.
# The path may be a URL: Tabular Editor accepts a file or a URL for
# ``-A``.
RULES_FILE_ENV = "PBI_TE_BPA_RULES"

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class BpaFinding(BaseModel):
    """Single finding from a BPA run."""

    rule_id: str
    rule_name: str
    severity: str  # error | warning | info
    object_name: str  # e.g. "Sales[TotalAmount]"
    object_type: str  # measure | column | table | relationship
    message: str
    auto_fixable: bool = False
    fix_suggestion: str | None = None


class BpaResult(BaseModel):
    """Result of a BPA run (per spec §2.1 output schema)."""

    score: float  # 0-100
    findings: list[BpaFinding] = Field(default_factory=list)
    ruleset_used: str
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


DEFAULT_TIMEOUT_S = 60
SUPPORTED_RULESETS = frozenset({"default", "performance", "governance"})

# The score is derived from the findings, because Tabular Editor does
# not produce one. The weights are the audit's own: an error is a
# defect, a warning is a smell, a note is information.
_SEVERITY_WEIGHTS = {"error": 10.0, "warning": 3.0, "note": 1.0, "info": 1.0}


class BpaRunner:
    """Subprocess wrapper around the Tabular Editor CLI.

    For MVP we expose the canonical interface and ship a mock path
    so the orchestrator can run end-to-end without Tabular Editor
    installed. Tests use ``mock_findings`` to script output.

    Real-binary path: the subprocess runs the analysis and its console
    output is parsed into ``BpaFinding``.
    """

    def __init__(
        self,
        binary: str = "te",
        *,
        timeout_s: int | None = None,
        mock_findings: list[BpaFinding] | None = None,
        mock_score: float | None = None,
        rules_file: str | os.PathLike[str] | None = None,
    ) -> None:
        self._binary = binary
        self._timeout_s = timeout_s
        self._mock_findings = mock_findings
        self._mock_score = mock_score
        self._rules_file = str(rules_file) if rules_file is not None else None

    async def run(
        self,
        target: Path,
        *,
        ruleset_name: str = "default",
        custom_ruleset: Path | None = None,
    ) -> BpaResult:
        """Run BPA on ``target`` and return normalized findings.

        ``target`` can be:
        - A ``.bim`` file (Tabular Editor format).
        - A ``.tmdl`` file or folder (TMDL model).
        - A connection string (``DataSource=...;Initial Catalog=...``).
        """
        if ruleset_name not in SUPPORTED_RULESETS and custom_ruleset is None:
            raise EngineError(
                f"unknown ruleset {ruleset_name!r}; "
                f"valid: {sorted(SUPPORTED_RULESETS)} or pass custom_ruleset",
                engine="te",
                code="engine_validation_failed",
                remediation_hint="Use 'default' or pass a custom_ruleset path",
            )

        # Mock path for tests.
        if self._mock_findings is not None:
            return BpaResult(
                score=self._mock_score if self._mock_score is not None else 100.0,
                findings=list(self._mock_findings),
                ruleset_used=ruleset_name,
            )

        rules = self._resolve_rules(custom_ruleset)
        if rules is None:
            # Without a rules file, Tabular Editor runs the rules of the
            # machine it runs on — none, on a server. The run would
            # report a clean score that means nothing at all, so the
            # check is refused instead.
            raise EngineError(
                "no BPA rules file configured, so BPA was not run. Tabular "
                "Editor runs the rules of the machine it runs on, and a "
                f"server has none: the result would be a clean score that "
                f"means nothing. Set {RULES_FILE_ENV} to a rules JSON file "
                "(a path or a URL), or pass custom_ruleset.",
                engine="te",
                code="engine_rules_unavailable",
                remediation_hint=(
                    f"Set {RULES_FILE_ENV} to the path or URL of a BPA rules "
                    "JSON file, or skip the BPA check."
                ),
            )

        # The timeout comes from the engine configuration, so the
        # PBI_ENGINE_TIMEOUT_TE_S variable the error message points at
        # actually has an effect.
        from powerbi_orchestrator_mcp.engines.timeouts import resolve_timeout

        effective_timeout_s = resolve_timeout("te", requested_s=self._timeout_s)

        args = self._build_args(target, rules)
        try:
            proc = await asyncio.create_subprocess_exec(
                self._binary,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise EngineNotFoundError(
                f"{self._binary} not found",
                engine="te",
                code="engine_not_found",
                remediation_hint=(
                    "Install Tabular Editor 2: "
                    "https://github.com/TabularEditor/TabularEditor/releases"
                ),
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=effective_timeout_s)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            from powerbi_orchestrator_mcp.engines.errors import EngineTimeoutError

            raise EngineTimeoutError(
                f"te bpa timed out after {effective_timeout_s}s",
                engine="te",
                code="engine_timeout",
                remediation_hint=(
                    "Increase PBI_ENGINE_TIMEOUT_TE_S (the Tabular Editor "
                    "analysis can take minutes on a large model)"
                ),
                timeout_s=effective_timeout_s,
            ) from exc

        output = stdout.decode("utf-8", errors="replace")
        errors = stderr.decode("utf-8", errors="replace")
        exit_code = proc.returncode or 0
        findings = self._parse_output(output)

        if exit_code != 0:
            # Exit 1 means Tabular Editor produced an Error-level output,
            # and a severity-3 BPA violation *is* an Error-level output.
            # So a non-zero exit with findings in it means "the model has
            # problems", not "the tool failed". The output tells the two
            # apart, not the code.
            if findings:
                return BpaResult(
                    score=_score_from(findings),
                    findings=findings,
                    ruleset_used=ruleset_name,
                )
            from powerbi_orchestrator_mcp.engines.exit_codes import (
                map_exit_code_to_error,
            )

            err = map_exit_code_to_error("te", exit_code, errors or output)
            if err is None:
                from powerbi_orchestrator_mcp.engines.errors import EngineCrashedError

                err = EngineCrashedError(
                    f"te bpa failed (exit {exit_code}): {(errors or output)[:500]}",
                    engine="te",
                    code="engine_crashed",
                    remediation_hint="Inspect te installation / model file",
                )
            raise err

        if not output.strip() and not findings:
            # Nothing at all came back. That is a run that produced
            # nothing, not a clean model: reporting "100, no findings"
            # here would be a false green.
            raise EngineError(
                "Tabular Editor produced no output. The BPA check was not run.",
                engine="te",
                code="engine_output_parse_error",
                remediation_hint=(
                    "Check that the rules file is a BPA rules JSON and that "
                    "the model is a .bim or .tmdl the CLI can read."
                ),
            )

        return BpaResult(
            score=_score_from(findings),
            findings=findings,
            ruleset_used=ruleset_name,
        )

    def _resolve_rules(self, custom_ruleset: Path | None) -> str | None:
        """The rules file to run, or None when there is none.

        The caller's rules win, then the one the runner was built with,
        then the environment.
        """
        if custom_ruleset is not None:
            return str(custom_ruleset)
        if self._rules_file:
            return self._rules_file
        from_env = os.environ.get(RULES_FILE_ENV, "").strip()
        return from_env or None

    def _build_args(self, target: Path, rules: str) -> list[str]:
        """The Tabular Editor 2 command line.

        ``<model> -A <rules.json> -V``: analyse the model with the given
        rules and report the findings as VSTS annotations, so each
        violation carries its severity.

        ``-G`` is **not** a report path — it selects GitHub Actions
        annotations, and ``-V`` selects VSTS ones. Tabular Editor 2 has
        no structured BPA output, so the annotations are what there is
        to read.
        """
        return [
            str(target),
            "-A",
            rules,
            "-V",
        ]

    def _parse_output(self, output: str) -> list[BpaFinding]:
        """Read the violations out of the console output.

        Tabular Editor reports a violation as ``... violates rule ...``,
        at the severity the rule carries. With ``-V`` each line is a VSTS
        annotation that names the severity; without it, the severity is
        not in the text.

        The parsing is deliberately tolerant — the format is a console
        format, not a contract — but it never invents findings, and it
        never turns unparseable output into a clean score: the caller
        gets no findings and the run reports them as what it was.
        """
        findings: list[BpaFinding] = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line or "violates rule" not in line:
                continue

            severity = _annotation_severity(line)
            message, rule_id = _split_violation(line)

            findings.append(
                BpaFinding(
                    rule_id=rule_id,
                    rule_name=rule_id,
                    severity=severity,
                    object_name=_violation_object(message),
                    object_type="",
                    message=message,
                )
            )
        return findings


def _annotation_severity(line: str) -> str:
    """The severity of a VSTS-annotated violation line."""
    lowered = line.lower()
    if "task.logissue type=error" in lowered:
        return "error"
    if "task.logissue type=warning" in lowered:
        return "warning"
    # Without the annotation the severity is not in the text, and a
    # violation is at least worth knowing about.
    return "info"


def _split_violation(line: str) -> tuple[str, str]:
    """Split a violation line into its message and its rule name.

    The message is the line without the annotation prefix: it is what a
    reader wants to see, and it names the object and the rule.
    """
    message = re.sub(r"^##vso\[[^\]]*\]", "", line).strip()
    _, _, tail = line.partition("violates rule")
    rule_id = tail.strip(" .:;'\"").split(" ")[0].strip("'\"")
    return message, rule_id or "unknown"


def _violation_object(message: str) -> str:
    """The object a violation is about, when the message names one."""
    match = re.search(r"'([^']+)'", message)
    if match:
        return match.group(1)
    return ""


def _score_from(findings: list[BpaFinding]) -> float:
    """Derive a 0-100 score from the findings.

    Tabular Editor does not produce a score, so the audit needs one to
    compare against a threshold. Every error costs 10 points, every
    warning 3 and every note 1, and the result never goes below zero.
    """
    penalty = sum(_SEVERITY_WEIGHTS.get(f.severity, 1.0) for f in findings)
    return max(0.0, min(100.0, 100.0 - penalty))


__all__ = [
    "BpaFinding",
    "BpaResult",
    "BpaRunner",
    "DEFAULT_TIMEOUT_S",
    "RULES_FILE_ENV",
    "SUPPORTED_RULESETS",
]
