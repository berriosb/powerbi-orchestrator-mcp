"""Audit guard decorator for state-changing MCP tools.

Every mutating tool invocation must leave a tamper-evident trail covering
success, failure and dry-run outcomes. The audit sink records a SHA-256
hash of the arguments rather than the arguments themselves, so credentials
and row-level data are never persisted.

The trail is written *ahead of time*: the attempt is recorded before the
tool body runs, and the outcome is recorded after. Two consequences:

- A crash in the middle of a mutation leaves a record of what was being
  attempted. Without it, the log would show nothing at all.
- The tool can refuse to act when the audit log cannot be written, which
  is what ``PBI_AUDIT_FAIL_CLOSED=1`` asks for. Failing closed is only
  possible if the check happens before the action.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any

import structlog

from powerbi_orchestrator_mcp.cloud.audit_cloud import CloudAuditLog

_log = structlog.get_logger(__name__)

AUDIT_WARNING_KEY = "_audit_warning"

# Set to 1 to refuse a state-changing call whose attempt could not be
# recorded. Off by default: the local audit log is a forensic trail,
# and a full disk would otherwise block every mutation. With it on, the
# audit log is a precondition, not a report.
AUDIT_FAIL_CLOSED_ENV = "PBI_AUDIT_FAIL_CLOSED"

# The status of the record written before the tool body runs.
RESULT_ATTEMPTED = "attempted"

_SECRET_KEY_HINTS = ("secret", "password", "token", "key", "credential")


class AuditUnavailableError(RuntimeError):
    """The audit log could not be written, so the action was not attempted.

    Only raised when the operator asked for fail-closed auditing with
    ``PBI_AUDIT_FAIL_CLOSED=1``.
    """


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in args.items():
        if any(hint in key.lower() for hint in _SECRET_KEY_HINTS):
            redacted[key] = "[redacted]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            redacted[key] = value
        else:
            redacted[key] = f"<{type(value).__name__}>"
    return redacted


def _classify(result: Any) -> str:
    if isinstance(result, dict):
        if result.get("dry_run") or result.get("status") == "DryRun":
            return "dry_run"
        errors = result.get("errors")
        if errors:
            return "partial" if result.get("publish_ok") or result.get("schedule_ok") else "failed"
        if result.get("rolled_back"):
            return "rolled_back"
    return "success"


def _summarize(result: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    if isinstance(result, dict):
        for key in (
            "item_id",
            "dataset_id",
            "refresh_id",
            "session_id",
            "execution_id",
            "status",
            "plan_id",
        ):
            value = result.get(key)
            if value is not None:
                summary[key] = value
        errors = result.get("errors")
        if errors:
            summary["error_count"] = len(errors) if hasattr(errors, "__len__") else 1
    return summary


def _fail_closed() -> bool:
    """True when the operator asked for fail-closed auditing."""
    return os.environ.get(AUDIT_FAIL_CLOSED_ENV, "").strip().lower() in ("1", "true", "yes")


def audited(
    tool_name: str,
    *,
    target_arg: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Record every invocation of a state-changing tool in the audit log.

    The attempt is recorded before the tool runs and the outcome after
    it, so the trail shows both that something was attempted and how it
    ended. When the attempt cannot be recorded and the operator asked
    for fail-closed auditing, the tool does not run at all.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            call_args = _redact({**kwargs})
            target_id = call_args.get(target_arg) if target_arg else None

            # Write-ahead: the attempt goes in the log before the
            # action, so a crash in the middle leaves a trace of what
            # was being attempted.
            intent_warning = _write(
                tool_name,
                call_args,
                target_id,
                RESULT_ATTEMPTED,
                {"phase": "intent"},
            )
            if intent_warning is not None and _fail_closed():
                # ``intent_warning`` already carries the
                # ``audit_log_write_failed:`` prefix.
                raise AuditUnavailableError(
                    f"{intent_warning}. Set {AUDIT_FAIL_CLOSED_ENV}=0 to act "
                    f"without an audit trail, or free the audit directory."
                )

            try:
                result = await func(*args, **kwargs)
            except BaseException as exc:
                _write(tool_name, call_args, target_id, "failed", {"error": type(exc).__name__})
                raise
            warning = _write(tool_name, call_args, target_id, _classify(result), _summarize(result))
            if warning is not None and isinstance(result, dict):
                result[AUDIT_WARNING_KEY] = warning
            return result

        return wrapper

    return decorator


def _write(
    tool_name: str,
    call_args: dict[str, Any],
    target_id: Any,
    status: str,
    payload: dict[str, Any],
) -> str | None:
    from powerbi_orchestrator_mcp.orchestrator.authorization import effective_principal

    # Who acted goes into a column of its own, and that column is part
    # of the hashed row: the actor cannot be edited afterwards without
    # breaking the chain. A trail that says what happened but not who
    # did it is not a trail.
    try:
        CloudAuditLog().record(
            operation=tool_name,
            target_id=str(target_id) if target_id is not None else None,
            payload=payload,
            result_status=status,
            tool_args=call_args,
            principal=effective_principal(),
        )
    except Exception as exc:
        message = f"audit_log_write_failed: {type(exc).__name__}: {exc}"
        _log.warning("audit.write_failed", tool=tool_name, error=str(exc))
        return message
    return None


__all__ = [
    "AUDIT_FAIL_CLOSED_ENV",
    "AUDIT_WARNING_KEY",
    "RESULT_ATTEMPTED",
    "AuditUnavailableError",
    "audited",
]
