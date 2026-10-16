from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

_TEST_HOME = tempfile.mkdtemp(prefix="pbi_orch_test_home_")
os.environ["PBI_ORCHESTRATOR_HOME"] = _TEST_HOME


@pytest.fixture(scope="session", autouse=True)
def _workspace_root_sandbox(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Bind the write-path allowlist to pytest's base temp directory.

    Write tools refuse paths outside the workspace root. Tests legitimately
    write under ``tmp_path``, so the root is pointed at pytest's basetemp
    for the whole session instead of relying on the process CWD.
    """
    base = Path(str(tmp_path_factory.getbasetemp()))
    previous = os.environ.get("PBI_WORKSPACE_ROOT")
    os.environ["PBI_WORKSPACE_ROOT"] = str(base)
    yield base
    if previous is None:
        os.environ.pop("PBI_WORKSPACE_ROOT", None)
    else:
        os.environ["PBI_WORKSPACE_ROOT"] = previous
