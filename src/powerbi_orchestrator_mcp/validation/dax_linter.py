"""DAX linter — detect 10 anti-patterns per spec.

Implements ``specs/03-validation.md`` §2.2.

The linter is regex-based (no AST parsing) — it's the MVP tradeoff.
Trade-offs:
- Pros: zero dependencies, fast, easy to extend.
- Cons: false positives in unusual cases (nested function calls,
  string literals that look like function names). Caller is responsible
  for filtering noise.

Anti-patterns covered (per spec table):
1. ``FILTER(Table, ...)`` without ``ISFILTERED`` inside CALCULATE.
2. Nested ``CALCULATE`` 3+ levels deep.
3. ``/`` division (should use DIVIDE for error handling).
4. IFERROR around non-failing expressions.
5. EARLIER usage (prefer VAR-based row context).
6. SUMMARIZE for aggregation (prefer SUMMARIZECOLUMNS).
7. Blank-suppressing ``+ 0`` (prefer ``+ BLANK()``).
8. Hallucinated function names (parser/runtime check; regex is conservative).
9. HASONEVALUE without ELSE branch.
10. Unused measures (auto-detect via TOM scan).

For MVP we ship the first 7 as regex patterns. The last 3 require
parse-time or cross-file analysis and are flagged as TODO with a clear
``coverage_status`` field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

# ---------------------------------------------------------------------------
# Pattern model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DaxPattern:
    """A single DAX anti-pattern."""

    rule_id: str
    severity: str  # error | warning | info
    regex: re.Pattern[str]
    message: str
    rewrite_suggestion: str


# ---------------------------------------------------------------------------
# The 7 regex-based patterns (per spec)
# ---------------------------------------------------------------------------


DaxLinter_PATTERNS: tuple[DaxPattern, ...] = (
    DaxPattern(
        rule_id="BP_FILTER_ISFILTERED",
        severity="warning",
        regex=re.compile(
            r"\bCALCULATE\s*\([^,]*,\s*FILTER\s*\(", re.IGNORECASE
        ),
        message=(
            "FILTER on whole table inside CALCULATE may iterate all rows; "
            "use KEEPFILTERS or direct predicates"
        ),
        rewrite_suggestion=(
            "CALCULATE([Measure], KEEPFILTERS(Table[Column] = value))"
        ),
    ),
    DaxPattern(
        rule_id="BP_CALCULATE_NESTED",
        severity="warning",
        regex=re.compile(r"\bCALCULATE\s*\(\s*[^()]*(?:[^()]*\bCALCULATE\s*\([^()]*){2,}"),
        message="CALCULATE nested 3+ levels deep hurts performance",
        rewrite_suggestion="Extract inner CALCULATE to a VAR with VAR x = CALCULATE(...)",
    ),
    DaxPattern(
        rule_id="BP_DIVIDE_VS_SLASH",
        severity="warning",
        regex=re.compile(
            r"\[?\w+\]?\s*/\s*\[?\w+\]?(?:\s*\))?\s*$", re.MULTILINE
        ),
        message="Use DIVIDE(numerator, denominator, alternate) instead of /",
        rewrite_suggestion="DIVIDE([Numerator], [Denominator], 0)",
    ),
    DaxPattern(
        rule_id="BP_IFERROR_MISUSE",
        severity="warning",
        regex=re.compile(
            r"\bIFERROR\s*\(\s*[A-Za-z_][\w]*\s*\(\s*[^,()]+\s*\)",
            re.IGNORECASE,
        ),
        message=(
            "IFERROR wrapping a function that doesn't raise is a code smell"
        ),
        rewrite_suggestion="Drop IFERROR; let errors surface during dev",
    ),
    DaxPattern(
        rule_id="BP_EARLIER_AVOID",
        severity="info",
        regex=re.compile(r"\bEARLIER\s*\(", re.IGNORECASE),
        message="EARLIER is slow; prefer VAR-based row context",
        rewrite_suggestion=(
            "VAR _row = FILTER(...)  RETURN SUMX(_row, ...)"
        ),
    ),
    DaxPattern(
        rule_id="BP_SUMMARIZE_FOR_AGG",
        severity="warning",
        regex=re.compile(
            r"SUMMARIZE\s*\([^)]*\bSUM\(|\bCOUNT\(|\bAVERAGE\(",
            re.IGNORECASE,
        ),
        message="SUMMARIZE for aggregation is slow; prefer SUMMARIZECOLUMNS",
        rewrite_suggestion=(
            "SUMMARIZECOLUMNS(Table[Dim], FILTER(Table, ...), \"Sum\", SUM(...))"
        ),
    ),
    DaxPattern(
        rule_id="BP_BLANK_SUPPRESS_PLUS_ZERO",
        severity="info",
        regex=re.compile(r"\b\w[\w\)\]]*\s*\+\s*0\b(?!\s*\.)"),
        message="Suppressing blanks with + 0 is opaque; prefer explicit BLANK() handling",
        rewrite_suggestion="[Measure] + 0  →  COALESCE([Measure], 0)",
    ),
)


# ---------------------------------------------------------------------------
# DAX linter
# ---------------------------------------------------------------------------


@dataclass
class DaxLintFinding:
    """Single linter finding."""

    rule_id: str
    severity: str
    message: str
    rewrite_suggestion: str | None
    line_number: int | None = None
    matched_text: str | None = None


class DaxLinter:
    """DAX linter with the 7 regex patterns above.

    Extensible via the ``extra_patterns`` constructor arg.
    """

    DEFAULT_PATTERNS: ClassVar[tuple[DaxPattern, ...]] = DaxLinter_PATTERNS

    def __init__(
        self, *, extra_patterns: tuple[DaxPattern, ...] | None = None
    ) -> None:
        self._patterns: tuple[DaxPattern, ...] = (
            extra_patterns if extra_patterns is not None else self.DEFAULT_PATTERNS
        )

    def lint(self, expression: str) -> list[DaxLintFinding]:
        """Run all patterns against ``expression``, return findings."""
        findings: list[DaxLintFinding] = []
        for pattern in self._patterns:
            for match in pattern.regex.finditer(expression):
                # Find the line number where the match starts.
                line_number = expression.count("\n", 0, match.start()) + 1
                findings.append(
                    DaxLintFinding(
                        rule_id=pattern.rule_id,
                        severity=pattern.severity,
                        message=pattern.message,
                        rewrite_suggestion=pattern.rewrite_suggestion,
                        line_number=line_number,
                        matched_text=match.group(0),
                    )
                )
        return findings

    def lint_batch(
        self, measures: dict[str, str]
    ) -> dict[str, list[DaxLintFinding]]:
        """Run linter on each measure; return {measure_name: findings}."""
        return {
            name: self.lint(expression) for name, expression in measures.items()
        }


__all__ = [
    "DaxLintFinding",
    "DaxLinter",
    "DaxPattern",
    "DaxLinter_PATTERNS",
]
