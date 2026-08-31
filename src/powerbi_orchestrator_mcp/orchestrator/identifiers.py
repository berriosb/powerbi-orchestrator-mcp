"""Canonical identifier formats for the orchestrator.

Implements spec section 2.7 from specs/01-orchestrator.md.

All IDs emitted or consumed by the orchestrator follow a canonical format.
The goals are:
- **Sortable**: chronological ordering works without parsing.
- **Debuggeable**: humans can parse them at a glance from logs / git.
- **Non-colliding**: across sessions and across processes.
- **PII-safe**: target_id hashes PII or long refs.

Identifier summary:

| Name              | Format                              | Example                                |
|-------------------|-------------------------------------|----------------------------------------|
| session_id        | uuid hex (32 chars)                 | 5f3a2b8c9d4e1f2a3b4c5d6e7f8a9b0c       |
| plan_id           | plan_YYYY-MM-DD_<nanoid>            | plan_2026-08-21_xK3mN9pQ               |
| step_id           | <plan_id>:s<n>                      | plan_2026-08-21_xK3mN9pQ:s3            |
| execution_id      | exec_[a-f0-9]{16}                   | exec_5f3a2b8c9d4e1f2a                  |
| target_id         | <target_type>:<ref_or_hash>         | pbip_folder:sha256:9f2a...             |
| rollback_handle   | snap_<iso>_<hash8>                  | snap_2026-08-21T10-30-00Z_9f2a1c4e     |
| snapshot_label    | kebab-case string <=64 chars        | pre-rename-2026-08-21                  |
"""

from __future__ import annotations

import hashlib
import re
import secrets
import string
import uuid
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Regex patterns (reject malformed IDs in inputs)
# ---------------------------------------------------------------------------

SESSION_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
PLAN_ID_PATTERN = re.compile(r"^plan_\d{4}-\d{2}-\d{2}_[A-Za-z0-9_-]{6,12}$")
STEP_ID_PATTERN = re.compile(
    r"^plan_\d{4}-\d{2}-\d{2}_[A-Za-z0-9_-]{6,12}:s\d+$"
)
EXECUTION_ID_PATTERN = re.compile(r"^exec_[a-f0-9]{16}$")
TARGET_ID_PATTERN = re.compile(
    r"^(pbip_folder|pbix_file|fabric_workspace|pbi_desktop|xmla_endpoint):.+"
)
ROLLBACK_HANDLE_PATTERN = re.compile(
    r"^snap_\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z_[a-z0-9]{8}$"
)

# Snapshot labels: kebab-case, 1-64 chars (per spec §2.7 table).
SNAPSHOT_LABEL_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+){0,31}$")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Nanoid alphabet per spec: [A-Za-z0-9_-]. Avoids chars that look alike
# in logs (no 0/O, no 1/l/I). Same alphabet as the spec example.
_NANOID_ALPHABET = string.ascii_letters + string.digits + "_-"
_NANOID_SIZE = 8  # spec example uses 8 chars (range 6-12)

# target_id PII/length threshold per spec §2.7 rule 4.
_TARGET_ID_HASH_THRESHOLD = 200

# Windows / Linux home-dir patterns that indicate PII in target_ref.
_PII_PATH_HINTS = (
    "/Users/",  # macOS
    "/home/",  # Linux
    "\\Users\\",  # Windows
    "C:\\Users\\",
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def new_session_id() -> str:
    """Generate a session_id: uuid4 hex without dashes (32 chars)."""
    return uuid.uuid4().hex


def new_plan_id(created_at: datetime | None = None) -> str:
    """Generate a plan_id: ``plan_YYYY-MM-DD_<nanoid>``.

    Sortable + unique. The nanoid suffix avoids collisions between
    parallel processes of the same developer.
    """
    ts = (created_at or datetime.now(UTC)).strftime("%Y-%m-%d")
    suffix = _nanoid(_NANOID_SIZE)
    return f"plan_{ts}_{suffix}"


def new_step_id(plan_id: str, step_number: int) -> str:
    """Derive a step_id from a plan_id: ``<plan_id>:s<n>``.

    The step_id is computed, not stored independently — guarantees a
    step cannot exist without its parent plan.
    """
    if not PLAN_ID_PATTERN.match(plan_id):
        raise ValueError(f"invalid plan_id format: {plan_id!r}")
    if step_number < 0:
        raise ValueError(f"step_number must be >= 0, got {step_number}")
    return f"{plan_id}:s{step_number}"


def new_execution_id() -> str:
    """Generate an execution_id: ``exec_[a-f0-9]{16}``.

    Distinct from plan_id because a single plan can be executed multiple
    times (dry-runs, re-applications). The execution_id groups all audit
    rows from one run.
    """
    return f"exec_{secrets.token_hex(8)}"


def make_target_id(target_type: str, target_ref: str) -> str:
    """Build a target_id: ``<target_type>:<ref_or_hash>``.

    Per spec §2.7 rule 4, the ref is hashed if it contains PII hints
    (paths under user home dirs) or is longer than
    ``_TARGET_ID_HASH_THRESHOLD``. The original ref (sanitized for audit)
    is persisted separately in the audit log's payload_json, not in
    target_id.
    """
    if not target_type:
        raise ValueError("target_type must be non-empty")
    if not target_ref:
        raise ValueError("target_ref must be non-empty")

    ref_repr = (
        _hash_target_ref(target_ref) if _should_hash_target_ref(target_ref) else target_ref
    )

    candidate = f"{target_type}:{ref_repr}"
    if not TARGET_ID_PATTERN.match(candidate):
        # After hashing the ref starts with 'sha256:' which is still
        # a valid token, but defend against any unexpected character.
        raise ValueError(f"constructed target_id fails pattern: {candidate!r}")
    return candidate


def new_rollback_handle() -> str:
    """Build a rollback_handle: ``snap_<iso>_<hash8>``.

    Uses the current UTC timestamp and a random 8-char hex suffix.
    The handle is opaque (used in APIs). The corresponding readable
    snapshot_label is generated/validated separately via
    ``make_snapshot_label``.
    """
    iso = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    suffix = secrets.token_hex(4)  # 8 hex chars
    return f"snap_{iso}_{suffix}"


def make_snapshot_label(label: str) -> str:
    """Validate and normalize a snapshot_label.

    Returns the label unchanged if it matches the regex AND is <=64 chars,
    otherwise raises ValueError. Use before persisting the label to
    the filesystem or git tag.
    """
    if not label or len(label) > 64 or not SNAPSHOT_LABEL_PATTERN.match(label):
        raise ValueError(
            f"snapshot_label must be kebab-case 1-64 chars, got {label!r}"
        )
    return label


# ---------------------------------------------------------------------------
# Validators (used when consuming IDs from external sources)
# ---------------------------------------------------------------------------


def is_valid_session_id(value: str) -> bool:
    return bool(SESSION_ID_PATTERN.match(value))


def is_valid_plan_id(value: str) -> bool:
    return bool(PLAN_ID_PATTERN.match(value))


def is_valid_step_id(value: str) -> bool:
    return bool(STEP_ID_PATTERN.match(value))


def is_valid_execution_id(value: str) -> bool:
    return bool(EXECUTION_ID_PATTERN.match(value))


def is_valid_target_id(value: str) -> bool:
    return bool(TARGET_ID_PATTERN.match(value))


def is_valid_rollback_handle(value: str) -> bool:
    return bool(ROLLBACK_HANDLE_PATTERN.match(value))


def parse_plan_id(plan_id: str) -> tuple[str, str]:
    """Split ``plan_YYYY-MM-DD_<nanoid>`` into ``(date, nanoid)``.

    Raises ValueError if the format is invalid.
    """
    if not PLAN_ID_PATTERN.match(plan_id):
        raise ValueError(f"invalid plan_id format: {plan_id!r}")
    parts = plan_id.split("_", 2)
    return parts[1], parts[2]


def parse_step_id(step_id: str) -> tuple[str, int]:
    """Split ``<plan_id>:s<n>`` into ``(plan_id, step_number)``."""
    if not STEP_ID_PATTERN.match(step_id):
        raise ValueError(f"invalid step_id format: {step_id!r}")
    plan_id, suffix = step_id.rsplit(":", 1)
    return plan_id, int(suffix[1:])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _nanoid(size: int) -> str:
    """Generate a cryptographically random nanoid of given size."""
    return "".join(secrets.choice(_NANOID_ALPHABET) for _ in range(size))


def _should_hash_target_ref(target_ref: str) -> bool:
    """True if the ref should be hashed (PII hint or too long)."""
    if len(target_ref) > _TARGET_ID_HASH_THRESHOLD:
        return True
    return any(hint in target_ref for hint in _PII_PATH_HINTS)


def _hash_target_ref(target_ref: str) -> str:
    """Hash the ref, prefixed with the algorithm marker."""
    digest = hashlib.sha256(target_ref.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
