from __future__ import annotations

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
    "/private/var",
}

FORBIDDEN_WINDOWS_DIRS = {
    "windows",
    "winnt",
    "system32",
    "program files",
    "program files (x86)",
    "programdata",
}


def validate_safe_pbip_path(
    path_str: str,
    *,
    must_exist: bool = True,
    must_be_dir: bool = True,
) -> Path:
    if not path_str or not str(path_str).strip():
        raise ValueError("pbip_path cannot be empty")
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
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"PBIP path does not exist: {resolved_str}")
    if must_exist and must_be_dir and not resolved.is_dir():
        raise ValueError(f"PBIP path must be a directory: {resolved_str}")
    return resolved
