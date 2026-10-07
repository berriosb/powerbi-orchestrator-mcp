from __future__ import annotations

import os
from pathlib import Path


def get_orchestrator_home() -> Path:
    env_path = os.environ.get("PBI_ORCHESTRATOR_HOME")
    if env_path:
        return Path(env_path)
    return Path.home() / ".powerbi-orchestrator-mcp"
