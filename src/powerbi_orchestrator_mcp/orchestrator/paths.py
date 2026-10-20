from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

DIR_MODE = 0o700
FILE_MODE = 0o600


def get_orchestrator_home() -> Path:
    env_path = os.environ.get("PBI_ORCHESTRATOR_HOME")
    if env_path:
        return Path(env_path).resolve()
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
    """Write ``data`` to ``path`` atomically with owner-only permissions.

    The temporary file has a unique name: a fixed one would let two
    writers racing on the same target clobber each other's partial
    write.
    """
    ensure_private_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.chmod(tmp, FILE_MODE)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    restrict_file(path)
