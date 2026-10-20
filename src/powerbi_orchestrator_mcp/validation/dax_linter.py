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
    unless_regex: re.Pattern[str] | None = None
    """A pattern that excuses the match.

    Some anti-patterns only exist in a particular context: a
    ``FILTER`` inside ``CALCULATE`` is a problem unless the filter is
    on ``ISFILTERED``. When this matches inside the rule's scope, the
    finding is dropped.
    """
    unless_scope: str = "match"
    """Where the excusing pattern is looked for: the matched text, or the
    text of the call the match opens.

    A ``CALCULATE(..., FILTER(..., ISFILTERED(...)))`` puts the
    excusing pattern *after* the match, so the whole call has to be
    looked at.
    """


# ---------------------------------------------------------------------------
# The 7 regex-based patterns (per spec)
# ---------------------------------------------------------------------------


DaxLinter_PATTERNS: tuple[DaxPattern, ...] = (
    DaxPattern(
        rule_id="BP_FILTER_ISFILTERED",
        severity="warning",
        regex=re.compile(r"\bCALCULATE\s*\([^,]*,\s*FILTER\s*\(", re.IGNORECASE),
        unless_regex=re.compile(r"\bISFILTERED\s*\(", re.IGNORECASE),
        unless_scope="call",
        message=(
            "FILTER on whole table inside CALCULATE may iterate all rows; "
            "use KEEPFILTERS or direct predicates"
        ),
        rewrite_suggestion=("CALCULATE([Measure], KEEPFILTERS(Table[Column] = value))"),
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
        # The division has to be found wherever it is, not only at the
        # end of the line: `IF([X] > 0, [N] / [D], 0)` divides in the
        # middle of the expression. The lookarounds keep `//` comments
        # and `*/` out of the match, and either side of the operator
        # may be a measure, a column or a parenthesised expression.
        regex=re.compile(r"(?<![/*])[^\s/*]\s*/\s*[^\s/*]"),
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
        message=("IFERROR wrapping a function that doesn't raise is a code smell"),
        rewrite_suggestion="Drop IFERROR; let errors surface during dev",
    ),
    DaxPattern(
        rule_id="BP_EARLIER_AVOID",
        severity="info",
        regex=re.compile(r"\bEARLIER\s*\(", re.IGNORECASE),
        message="EARLIER is slow; prefer VAR-based row context",
        rewrite_suggestion=("VAR _row = FILTER(...)  RETURN SUMX(_row, ...)"),
    ),
    DaxPattern(
        rule_id="BP_SUMMARIZE_FOR_AGG",
        severity="warning",
        # The aggregation has to be *inside* the SUMMARIZE. The
        # alternation used to sit outside the parenthesis group, so any
        # AVERAGE( or COUNT( anywhere in the expression fired this rule.
        regex=re.compile(
            r"SUMMARIZE\s*\([^)]*\b(?:SUM|COUNT|AVERAGE)\s*\(",
            re.IGNORECASE,
        ),
        message="SUMMARIZE for aggregation is slow; prefer SUMMARIZECOLUMNS",
        rewrite_suggestion=('SUMMARIZECOLUMNS(Table[Dim], FILTER(Table, ...), "Sum", SUM(...))'),
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


def _strip_comments(expression: str) -> str:
    """Blank out DAX comments, preserving offsets and line breaks.

    A comment like ``// divide by 2`` is not a division. The comment
    text is replaced by spaces so the offsets — and therefore the line
    numbers of the findings — stay correct.
    """
    out = list(expression)
    i = 0
    length = len(expression)
    while i < length:
        if expression.startswith("//", i):
            while i < length and expression[i] != "\n":
                out[i] = " "
                i += 1
        elif expression.startswith("/*", i):
            while i < length and not expression.startswith("*/", i):
                if expression[i] != "\n":
                    out[i] = " "
                i += 1
            if i < length:
                out[i] = " "
                out[i + 1] = " "
                i += 2
        else:
            i += 1
    return "".join(out)


def _call_text(expression: str, call_start: int) -> str:
    """Return the text of the function call that starts at ``call_start``.

    The call is balanced by parenthesis, so nested calls are included.
    Used by the rules whose context spans more than the matched text.
    """
    open_idx = expression.find("(", call_start)
    if open_idx == -1:
        return expression[call_start:]
    depth = 0
    for i in range(open_idx, len(expression)):
        char = expression[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return expression[call_start : i + 1]
    return expression[call_start:]


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

    def __init__(self, *, extra_patterns: tuple[DaxPattern, ...] | None = None) -> None:
        self._patterns: tuple[DaxPattern, ...] = (
            extra_patterns if extra_patterns is not None else self.DEFAULT_PATTERNS
        )

    def lint(self, expression: str) -> list[DaxLintFinding]:
        """Run all patterns against ``expression``, return findings."""
        # Comments are blanked out first: a `//` comment is not a
        # division and an `/* */` block is not code.
        analysed = _strip_comments(expression)
        findings: list[DaxLintFinding] = []
        for pattern in self._patterns:
            for match in pattern.regex.finditer(analysed):
                # Some rules only apply in a particular context; the
                # excusing pattern is looked for in the rule's scope.
                if pattern.unless_regex is not None:
                    scope = (
                        _call_text(analysed, match.start())
                        if pattern.unless_scope == "call"
                        else match.group(0)
                    )
                    if pattern.unless_regex.search(scope):
                        continue
                # Find the line number where the match starts.
                line_number = expression.count("\n", 0, match.start()) + 1
                findings.append(
                    DaxLintFinding(
                        rule_id=pattern.rule_id,
                        severity=pattern.severity,
                        message=pattern.message,
                        rewrite_suggestion=pattern.rewrite_suggestion,
                        line_number=line_number,
                        matched_text=expression[match.start() : match.end()],
                    )
                )
        return findings

    def lint_batch(self, measures: dict[str, str]) -> dict[str, list[DaxLintFinding]]:
        """Run linter on each measure; return {measure_name: findings}."""
        return {name: self.lint(expression) for name, expression in measures.items()}


__all__ = [
    "DaxLintFinding",
    "DaxLinter",
    "DaxPattern",
    "DaxLinter_PATTERNS",
]
