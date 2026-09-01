"""CloudAuditLog — wraps the general audit log with mandatory redaction.

Implements ``specs/02-cloud-fabric.md`` §5 (Tier-B Cloud audit log).

Behavior per spec:
- Reuses the same SQLite + HMAC chain as ``orchestrator/audit.py``.
- ``tool_name`` is prefixed with ``"cloud:"`` so cloud-specific rows
  are filterable (``SELECT * FROM audit_log WHERE tool_name LIKE 'cloud:%'``).
- Adds required redaction patterns to the payload before persisting:
  - JWT bearer tokens (``Bearer XXXX``)
  - OAuth ``code=`` query params (used in refresh URLs)
  - Connection strings (``Server=``, ``Data Source=``, ``Password=``)
  - Standalone JWTs (long base64-dot-separated)
  - Email addresses (for RLS EffectiveIdentity) → sha256[:8]
- Retention: 365 days (vs indefinite for orchestration rows).

For MVP we keep the implementation minimal: a thin wrapper that calls
``AuditLog.insert()`` after redaction. When real-world PII starts
appearing in payloads, we add per-pattern tests and tune.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from powerbi_orchestrator_mcp.orchestrator.audit import AuditEntry, AuditLog

# ---------------------------------------------------------------------------
# Redaction patterns (per spec §5 table)
# ---------------------------------------------------------------------------

REDACTION_PATTERNS = [
    # Bearer tokens
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"), "Bearer [REDACTED]"),
    # OAuth code query params (refresh URLs)
    (re.compile(r"code=[A-Za-z0-9_\-]{20,}"), "code=[REDACTED]"),
    # Connection string keys (case-insensitive, value until ;)
    (re.compile(r"(?i)(Server|Data Source)=[^;]+"), r"\1=[REDACTED]"),
    # Password= in connection strings
    (re.compile(r"(?i)Password=[^;]+"), "Password=[REDACTED]"),
    # Standalone JWT (3 base64url segments separated by dots)
    (
        re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
        "[JWT_REDACTED]",
    ),
]

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply redaction patterns to all string values in the payload dict.

    Strings are searched for the patterns; matches are replaced.
    Email addresses are replaced with a sha256[:8] hash (per spec —
    EffectiveIdentity emails must be redactable but distinguishable).

    Lists are recursed; nested dicts too. Non-string values pass through.
    """
    if isinstance(payload, str):
        return _redact_string(payload)
    if isinstance(payload, dict):
        return {k: redact_payload(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    return payload


def _redact_string(value: str) -> str:
    out = value
    for pattern, replacement in REDACTION_PATTERNS:
        out = pattern.sub(replacement, out)
    out = EMAIL_PATTERN.sub(_email_hash, out)
    return out


def _email_hash(match: re.Match[str]) -> str:
    email = match.group(0)
    return f"<email_hash:{hashlib.sha256(email.encode()).hexdigest()[:8]}>"


# ---------------------------------------------------------------------------
# CloudAuditLog
# ---------------------------------------------------------------------------


class CloudAuditLog:
    """Wrapper around AuditLog with mandatory redaction.

    Usage::

        base = AuditLog()
        cloud = CloudAuditLog(base)
        cloud.record(
            operation="dataset.refresh",
            workspace_id="ws-abc",
            dataset_id="ds-123",
            payload={"refresh_id": "abc", "effective_identity": "alice@acme.test"},
        )

    The row appears in the same SQLite table as orchestration rows but
    with ``tool_name="cloud:dataset.refresh"`` so it can be filtered.
    """

    DEFAULT_RETENTION_DAYS = 365

    def __init__(
        self,
        base: AuditLog | None = None,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        self._base = base or AuditLog()
        self._retention_days = retention_days

    def record(
        self,
        operation: str,
        *,
        workspace_id: str | None = None,
        dataset_id: str | None = None,
        target_id: str | None = None,
        payload: dict[str, Any] | None = None,
        result_status: str = "success",
        tool_args: dict[str, Any] | None = None,
    ) -> AuditEntry:
        """Record a cloud operation with mandatory redaction.

        ``operation`` becomes ``tool_name="cloud:<operation>"``.
        ``payload`` is redacted via ``redact_payload()`` before persisting.
        """
        redacted_payload = redact_payload(payload or {})
        # Include the structured fields in payload for forensic queries.
        if workspace_id:
            redacted_payload.setdefault("workspace_id", workspace_id)
        if dataset_id:
            redacted_payload.setdefault("dataset_id", dataset_id)

        return self._base.insert(
            tool_name=f"cloud:{operation}",
            tool_args=tool_args or {"operation": operation},
            result_status=result_status,
            target_id=target_id,
            payload={
                **redacted_payload,
                "retention_days": self._retention_days,
            },
        )


__all__ = [
    "EMAIL_PATTERN",
    "CloudAuditLog",
    "REDACTION_PATTERNS",
    "redact_payload",
]
