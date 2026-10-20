from __future__ import annotations

import os
import re
from pathlib import Path

FORBIDDEN_SYSTEM_PATHS = {
    "/etc",
    "/proc",
    "/sys",
    "/dev",
    "/root",
    "/boot",
    "/bin",
    "/sbin",
    "/usr",
    "/lib",
    "/lib64",
    "/private/etc",
}

FORBIDDEN_WINDOWS_DIRS = {
    "windows",
    "winnt",
    "system32",
    "program files",
    "program files (x86)",
    "programdata",
}

FORBIDDEN_SENSITIVE_DIRS = {
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
    ".docker",
}


def resolve_write_root(root: str | Path | None = None) -> Path:
    """Return the directory that bounds every write an agent may perform.

    Uses ``PBI_WORKSPACE_ROOT`` when the operator set it, otherwise falls
    back to the process working directory so containment is safe-by-default
    instead of unbounded.
    """
    configured = root or os.environ.get("PBI_WORKSPACE_ROOT")
    if configured:
        return Path(configured).resolve()
    return Path.cwd().resolve()


def _resolve_guarded(path_str: str) -> Path:
    """Resolve ``path_str`` and reject system or credential-bearing targets.

    Symlinks are resolved before any check runs, so a symlink cannot be used
    to step outside the denylist.
    """
    if not path_str or not str(path_str).strip():
        raise ValueError("path cannot be empty")
    clean = str(path_str).replace("\\", "/").strip()
    if clean.startswith("//"):
        raise ValueError(f"network paths are forbidden: {path_str!r}")
    for forbidden in FORBIDDEN_SYSTEM_PATHS:
        if clean == forbidden or clean.startswith(forbidden + "/"):
            raise ValueError(f"access to system directory {path_str!r} is forbidden")
    if re.match(
        r"^[a-zA-Z]:/(windows|winnt|system32|program files|program files \(x86\)|programdata)(/.*)?$",
        clean,
        re.IGNORECASE,
    ):
        raise ValueError(f"access to system directory {path_str!r} is forbidden")
    resolved = Path(path_str).resolve()
    resolved_str = str(resolved).replace("\\", "/")
    for forbidden in FORBIDDEN_SYSTEM_PATHS:
        if resolved_str == forbidden or resolved_str.startswith(forbidden + "/"):
            raise ValueError(f"access to system directory {resolved_str!r} is forbidden")
    if resolved_str.startswith("/private/"):
        unprefixed = resolved_str[len("/private") :]
        for forbidden in FORBIDDEN_SYSTEM_PATHS:
            if unprefixed == forbidden or unprefixed.startswith(forbidden + "/"):
                raise ValueError(f"access to system directory {resolved_str!r} is forbidden")
    if len(resolved.parts) >= 2 and resolved.parts[1].lower() in FORBIDDEN_WINDOWS_DIRS:
        raise ValueError(f"access to system directory {resolved_str!r} is forbidden")
    for part in resolved.parts:
        if part.lower() in FORBIDDEN_SENSITIVE_DIRS:
            raise ValueError(f"access to sensitive directory {resolved_str!r} is forbidden")
    return resolved


def _enforce_contained(resolved: Path, ws_path: Path, *, hint: str = "") -> None:
    resolved_str = str(resolved).replace("\\", "/")
    try:
        contained = resolved.is_relative_to(ws_path)
    except AttributeError:
        contained = str(resolved).startswith(str(ws_path))
    if not contained:
        message = f"path {resolved_str!r} is outside allowed workspace root {str(ws_path)!r}"
        if hint:
            message = f"{message}. {hint}"
        raise ValueError(message)


def validate_write_path(
    path_str: str,
    *,
    root: str | Path | None = None,
    must_exist: bool = False,
) -> Path:
    """Validate a path a tool is about to write to.

    Unlike :func:`validate_safe_pbip_path`, containment is never optional:
    the root defaults to the process working directory. An agent therefore
    cannot write outside the project unless the operator explicitly widened
    ``PBI_WORKSPACE_ROOT``.
    """
    resolved = _resolve_guarded(path_str)
    _enforce_contained(resolved, resolve_write_root(root))
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"path does not exist: {resolved}")
    return resolved


def resolve_read_root(root: str | Path | None = None) -> Path | None:
    """Return the directory that bounds every read, or None to allow any.

    Uses ``PBI_WORKSPACE_ROOT`` when the operator set it, otherwise falls
    back to the process working directory — the same default the write
    path uses.

    An operator who genuinely needs to read a project outside that root
    sets ``PBI_ALLOW_OUTSIDE_WORKSPACE_READ=1``. The allowance is
    explicit on purpose: reading is how content reaches the model
    context, so an unbounded read path should never be the default.
    """
    configured = root or os.environ.get("PBI_WORKSPACE_ROOT")
    if configured:
        return Path(configured).resolve()
    if os.environ.get("PBI_ALLOW_OUTSIDE_WORKSPACE_READ", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return None
    return Path.cwd().resolve()


def validate_safe_pbip_path(
    path_str: str,
    *,
    must_exist: bool = True,
    must_be_dir: bool = True,
    workspace_root: str | Path | None = None,
) -> Path:
    """Validate a path a tool is about to read.

    Containment is enforced by default, against ``PBI_WORKSPACE_ROOT``
    when it is set and against the process working directory otherwise —
    the same rule the write path follows. An operator who needs to read
    outside that root sets ``PBI_ALLOW_OUTSIDE_WORKSPACE_READ=1``.

    The system and credential directories are refused either way.
    """
    if not path_str or not str(path_str).strip():
        raise ValueError("pbip_path cannot be empty")
    resolved = _resolve_guarded(path_str)

    effective_ws = resolve_read_root(workspace_root)
    if effective_ws is not None:
        _enforce_contained(
            resolved,
            effective_ws,
            hint=(
                "Point PBI_WORKSPACE_ROOT at the directory that holds it, or set "
                "PBI_ALLOW_OUTSIDE_WORKSPACE_READ=1 to read outside the root"
            ),
        )

    resolved_str = str(resolved).replace("\\", "/")
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"PBIP path does not exist: {resolved_str}")
    if must_exist and must_be_dir and not resolved.is_dir():
        raise ValueError(f"PBIP path must be a directory: {resolved_str}")
    return resolved
