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
    # /run holds the container and systemd runtime state, and with it
    # the mounted secrets of a pod. The workspace boundary already
    # keeps it out of reach; this closes the second layer too, for
    # the deployments that widen the boundary on purpose.
    "/run",
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
    # Holds the credentials of a lot of tools, and it is the one the
    # read-path audit named explicitly.
    ".config",
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

    The precedence is deliberate, and it is the safe one:

    1. ``PBI_WORKSPACE_ROOT`` when the operator set it. That root is
       the boundary, full stop.
    2. Otherwise, the process working directory — the same default the
       write path uses.

    ``PBI_ALLOW_OUTSIDE_WORKSPACE_READ=1`` removes the boundary
    **only when there is no configured root**. It does not weaken a
    root the operator has set: an operator who has declared where
    their workspace is gets exactly that, and a second variable that
    silently widens it would be a footgun in a deployment that sets
    both.

    Reading is how content reaches the model context, so an unbounded
    read path is never the default.
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


def _read_containment_hint() -> str:
    """What to tell an operator whose read was refused.

    The suggestion has to be one that would actually work: setting
    ``PBI_ALLOW_OUTSIDE_WORKSPACE_READ`` does not widen a configured
    ``PBI_WORKSPACE_ROOT``, so telling them to do that would send
    them in a circle.
    """
    if os.environ.get("PBI_WORKSPACE_ROOT"):
        return (
            "Point PBI_WORKSPACE_ROOT at the directory that holds it. "
            "PBI_ALLOW_OUTSIDE_WORKSPACE_READ does not widen a root that "
            "is already set."
        )
    return (
        "The workspace root is the process working directory. Point "
        "PBI_WORKSPACE_ROOT at the directory that holds it, or set "
        "PBI_ALLOW_OUTSIDE_WORKSPACE_READ=1 to read outside it."
    )


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
    the same rule the write path follows.

    A configured ``PBI_WORKSPACE_ROOT`` is the boundary and nothing
    widens it. Without one, an operator who needs to read outside the
    working directory sets ``PBI_ALLOW_OUTSIDE_WORKSPACE_READ=1``.

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
            hint=_read_containment_hint(),
        )

    resolved_str = str(resolved).replace("\\", "/")
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"PBIP path does not exist: {resolved_str}")
    if must_exist and must_be_dir and not resolved.is_dir():
        raise ValueError(f"PBIP path must be a directory: {resolved_str}")
    return resolved
