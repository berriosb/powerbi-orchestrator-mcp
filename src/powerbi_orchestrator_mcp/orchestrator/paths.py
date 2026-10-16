from __future__ import annotations

import contextlib
import os
from pathlib import Path

DIR_MODE = 0o700
FILE_MODE = 0o600


def get_orchestrator_home() -> Path:
    env_path = os.environ.get("PBI_ORCHESTRATOR_HOME")
    if env_path:
        return Path(env_path)
    return Path.home() / ".powerbi-orchestrator-mcp"


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` if needed and restrict it to the owner.

    Plan, session and audit stores hold workspace identifiers and operation
    history, so they must not be world-readable on a shared host.
    """
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.chmod(DIR_MODE)
    return path


def restrict_file(path: Path) -> None:
    """Restrict an existing file to the owner, ignoring platforms without chmod."""
    with contextlib.suppress(OSError):
        path.chmod(FILE_MODE)


def atomic_write_private(path: Path, data: str) -> None:
    """Write ``data`` to ``path`` atomically with owner-only permissions."""
    ensure_private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with open(fd, "w", encoding="utf-8") as handle:
        handle.write(data)
    os.replace(tmp, path)
    restrict_file(path)
