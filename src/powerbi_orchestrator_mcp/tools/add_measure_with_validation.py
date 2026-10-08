"""add_measure_with_validation tool — v1.1 (SPEC §6.3).

Adds a DAX measure with mandatory lint validation before persistence,
plus optional runtime check. Wraps DaxLinter + (in production) the
modeling engine's create_measure method.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from powerbi_orchestrator_mcp.validation.dax_linter import (
    DaxLinter,
)


class AddMeasureWithValidation(BaseModel):
    """Input schema for ``add_measure_with_validation`` (SPEC §6.3 #1)."""

    target: str  # PBIP path or fabric workspace dataset id
    measure_name: str
    table: str
    expression: str
    format_string: str | None = None
    description: str | None = None
    is_hidden: bool = False
    fail_on_severity: str = "warning"  # error | warning | info
    # Safety default: writes require an explicit dry_run=False.
    dry_run: bool = True
    runtime_check: bool = False
    # In production, the modeling engine is wired via the orchestrator's
    # step_executor registry. Tests inject `measure_writer` (a callable
    # accepting (target, table, name, expression, format_string, description,
    # is_hidden) → dict with changed_files and error keys).
    measure_writer: Any = None


SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2}


def add_measure_with_validation(
    target: str,
    measure_name: str,
    table: str,
    expression: str,
    *,
    format_string: str | None = None,
    description: str | None = None,
    is_hidden: bool = False,
    fail_on_severity: str = "warning",
    dry_run: bool = True,
    runtime_check: bool = False,
    measure_writer: Any = None,
) -> dict[str, Any]:
    """Lint the expression, gate on severity, then create the measure.

    Returns:
        dict with keys: success, measure_name, lint_findings,
        runtime_check, changed_files, error_message.
    """
    if fail_on_severity not in SEVERITY_ORDER:
        raise ValueError(
            f"fail_on_severity must be one of {sorted(SEVERITY_ORDER)}, got {fail_on_severity!r}"
        )

    # 1. Lint.
    linter = DaxLinter()
    lint_findings_raw = linter.lint(expression)

    lint_findings: list[dict[str, Any]] = [
        {
            "rule_id": f.rule_id,
            "severity": f.severity,
            "message": f.message,
            "rewrite_suggestion": f.rewrite_suggestion,
            "line_number": f.line_number,
            "matched_text": f.matched_text,
        }
        for f in lint_findings_raw
    ]

    # 2. Gate on severity.
    threshold = SEVERITY_ORDER[fail_on_severity]
    blocking = [f for f in lint_findings if SEVERITY_ORDER.get(f["severity"], 0) >= threshold]

    if blocking and not dry_run:
        return {
            "success": False,
            "measure_name": measure_name,
            "lint_findings": lint_findings,
            "runtime_check": {"ran": False},
            "changed_files": [],
            "error_message": (
                f"lint found {len(blocking)} blocking finding(s) at severity ≥ {fail_on_severity}"
            ),
            "dry_run": False,
        }

    if dry_run:
        return {
            "success": not blocking,
            "measure_name": measure_name,
            "lint_findings": lint_findings,
            "runtime_check": {"ran": False},
            "changed_files": [],
            "error_message": (
                f"dry-run: {len(blocking)} blocking finding(s)" if blocking else None
            ),
            "dry_run": True,
        }

    # 3. Write via injected measure_writer.
    if measure_writer is None:
        # No writer wired: we cannot persist anything. Report FAILURE —
        # returning success=True here would tell the caller (and any LLM
        # driving this tool) that the measure was created when nothing was
        # written to disk.
        return {
            "success": False,
            "measure_name": measure_name,
            "lint_findings": lint_findings,
            "runtime_check": {"ran": False},
            "changed_files": [],
            "error_message": (
                "no measure_writer available; measure was NOT persisted. "
                "Lint passed but no engine is wired for this target — "
                "use dry_run=True or connect a modeling engine first."
            ),
            "dry_run": False,
        }

    try:
        write_result = measure_writer(
            target=target,
            table=table,
            name=measure_name,
            expression=expression,
            format_string=format_string,
            description=description,
            is_hidden=is_hidden,
        )
        changed_files = write_result.get("changed_files", [])
        error_message = write_result.get("error_message")
    except Exception as exc:
        return {
            "success": False,
            "measure_name": measure_name,
            "lint_findings": lint_findings,
            "runtime_check": {"ran": False},
            "changed_files": [],
            "error_message": f"write failed: {exc}",
            "dry_run": False,
        }

    # 4. Optional runtime check (best-effort).
    # `ran` is only True when something actually executed. Reporting
    # ran=True alongside an "unsupported" error would read as "validation
    # ran and failed" to any caller — including an LLM — when in fact no
    # engine was ever invoked.
    runtime: dict[str, Any] = {"ran": False, "supported": False}
    if runtime_check:
        runtime = {
            "ran": False,
            "supported": False,
            "error": (
                "runtime_check is not supported: no live engine is wired "
                "for this target (requires modeling engine integration)"
            ),
        }

    return {
        "success": error_message is None,
        "measure_name": measure_name,
        "lint_findings": lint_findings,
        "runtime_check": runtime,
        "changed_files": changed_files,
        "error_message": error_message,
        "dry_run": False,
    }
