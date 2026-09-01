"""Best Practice Analyzer runner — wraps ``te bpa`` subprocess.

Implements ``specs/03-validation.md`` §2.1.

Tabular Editor CLI's ``bpa`` command runs Best Practice Analyzer rules
against a Tabular Model file (``.bim``) or a database connection, and
emits JSON with findings. We wrap this subprocess and normalize the
output into our ``BpaResult`` model.

When ``te`` is not installed, the runner returns ``EngineNotFoundError``
(via the existing engine-error contract in
``engines/errors.py``). Tests use ``mock_responses`` to script output.

Ruleset resolution (per spec):
- ``default``: 89 rules from Tabular Editor 3 (the upstream ruleset).
- ``performance``: subset focused on performance.
- ``governance``: subset for org audit (PII, descriptions, OLS).
- Custom JSON: caller passes a path to a custom rules JSON file.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.engines.errors import (
    EngineError,
    EngineNotFoundError,
)

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


class BpaRunner:
    """Subprocess wrapper around ``te bpa``.

    For MVP we expose the canonical interface and ship a mock path
    so the orchestrator can run end-to-end without ``te`` installed.
    Tests use ``mock_findings`` to script output.

    Real-binary path (``te`` installed): the subprocess emits JSON to
    stdout; we parse it and map to ``BpaFinding``.
    """

    def __init__(
        self,
        binary: str = "te2",
        *,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        mock_findings: list[BpaFinding] | None = None,
        mock_score: float | None = None,
    ) -> None:
        self._binary = binary
        self._timeout_s = timeout_s
        self._mock_findings = mock_findings
        self._mock_score = mock_score

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

        # Real binary path.
        args = self._build_args(target, ruleset_name, custom_ruleset)
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
                    "Install Tabular Editor CLI: "
                    "https://github.com/TabularEditor/TabularEditor/releases"
                ),
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout_s
            )
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            from powerbi_orchestrator_mcp.engines.errors import EngineTimeoutError

            raise EngineTimeoutError(
                f"te bpa timed out after {self._timeout_s}s",
                engine="te",
                code="engine_timeout",
                remediation_hint="Increase PBI_ENGINE_TIMEOUT_TE_S",
                timeout_s=self._timeout_s,
            ) from exc

        if proc.returncode != 0:
            from powerbi_orchestrator_mcp.engines.errors import EngineCrashedError

            raise EngineCrashedError(
                f"te bpa failed (exit {proc.returncode}): "
                f"{stderr.decode('utf-8', errors='replace')[:500]}",
                engine="te",
                code="engine_crashed",
                remediation_hint="Inspect te installation / model file",
            )

        return self._parse_output(stdout.decode("utf-8"), ruleset_name)

    def _build_args(
        self,
        target: Path,
        ruleset_name: str,
        custom_ruleset: Path | None,
    ) -> list[str]:
        args: list[str] = ["bpa", str(target)]
        if custom_ruleset is not None:
            args += ["--ruleset", str(custom_ruleset)]
        else:
            args += ["--ruleset", ruleset_name]
        args += ["--format", "json"]
        return args

    def _parse_output(self, stdout: str, ruleset_name: str) -> BpaResult:
        """Parse te bpa JSON output into BpaResult.

        Expected schema (per TE docs):
        ``{"score": 87.5, "findings": [{"RuleId": "...", ...}]}``
        """
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            from powerbi_orchestrator_mcp.engines.errors import (
                EngineOutputParseError,
            )

            raise EngineOutputParseError(
                f"te bpa output not JSON: {exc}",
                engine="te",
                code="engine_output_parse_error",
                remediation_hint="Upgrade te or check model file integrity",
            ) from exc

        findings_raw = data.get("findings", [])
        findings = [
            BpaFinding(
                rule_id=f.get("RuleId", f.get("rule_id", "")),
                rule_name=f.get("RuleName", f.get("rule_name", "")),
                severity=f.get("Severity", f.get("severity", "info")),
                object_name=f.get("ObjectName", f.get("object_name", "")),
                object_type=f.get("ObjectType", f.get("object_type", "")),
                message=f.get("Message", f.get("message", "")),
                auto_fixable=bool(f.get("CanFix", f.get("auto_fixable", False))),
                fix_suggestion=f.get("FixExpression") or f.get("fix_suggestion"),
            )
            for f in findings_raw
        ]

        return BpaResult(
            score=float(data.get("score", 100.0)),
            findings=findings,
            ruleset_used=ruleset_name,
        )


__all__ = [
    "BpaFinding",
    "BpaResult",
    "BpaRunner",
    "DEFAULT_TIMEOUT_S",
    "SUPPORTED_RULESETS",
]
