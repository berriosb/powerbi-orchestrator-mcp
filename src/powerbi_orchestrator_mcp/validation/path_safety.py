from __future__ import annotations

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
}


def validate_safe_pbip_path(
    path_str: str,
    *,
    must_exist: bool = True,
    must_be_dir: bool = True,
) -> Path:
    if not path_str or not str(path_str).strip():
        raise ValueError("pbip_path cannot be empty")
    resolved = Path(path_str).resolve()
    resolved_str = str(resolved)
    for forbidden in FORBIDDEN_SYSTEM_PATHS:
        if resolved_str == forbidden or resolved_str.startswith(forbidden + "/"):
            raise ValueError(f"access to system directory {resolved_str!r} is forbidden")
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"PBIP path does not exist: {resolved_str}")
    if must_exist and must_be_dir and not resolved.is_dir():
        raise ValueError(f"PBIP path must be a directory: {resolved_str}")
    return resolved
