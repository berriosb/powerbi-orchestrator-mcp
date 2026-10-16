"""Audit guard decorator for state-changing MCP tools.

Every mutating tool invocation must leave a tamper-evident trail covering
success, failure and dry-run outcomes. The audit sink records a SHA-256
hash of the arguments rather than the arguments themselves, so credentials
and row-level data are never persisted.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

import structlog

from powerbi_orchestrator_mcp.cloud.audit_cloud import CloudAuditLog

_log = structlog.get_logger(__name__)

AUDIT_WARNING_KEY = "_audit_warning"

_SECRET_KEY_HINTS = ("secret", "password", "token", "key", "credential")


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


def audited(
    tool_name: str,
    *,
    target_arg: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Record every invocation of a state-changing tool in the audit log."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            call_args = _redact({**kwargs})
            target_id = call_args.get(target_arg) if target_arg else None
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
    try:
        CloudAuditLog().record(
            operation=tool_name,
            target_id=str(target_id) if target_id is not None else None,
            payload=payload,
            result_status=status,
            tool_args=call_args,
        )
    except Exception as exc:
        message = f"audit_log_write_failed: {type(exc).__name__}: {exc}"
        _log.warning("audit.write_failed", tool=tool_name, error=str(exc))
        return message
    return None


__all__ = ["AUDIT_WARNING_KEY", "audited"]
