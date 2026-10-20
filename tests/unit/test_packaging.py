"""Guards against the packaging metadata drifting apart.

The version lives in two places — `pyproject.toml` and
`src/powerbi_orchestrator_mcp/__init__.py` — because the CLI reports
`__version__` at runtime while the build backend reads the project
metadata. They have to agree: a package that installs as one version and
reports another is confusing to debug and misleading in bug reports.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp import __version__

PYPROJECT = Path(__file__).parent.parent.parent / "pyproject.toml"


def _project_version() -> str:
    with PYPROJECT.open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def test_pyproject_exists() -> None:
    assert PYPROJECT.is_file(), f"pyproject.toml not found at {PYPROJECT}"


def test_package_version_matches_pyproject() -> None:
    assert __version__ == _project_version()


def test_version_is_not_a_placeholder() -> None:
    assert __version__ and __version__ != "0.0.0"
    assert " " not in __version__


@pytest.mark.parametrize("field", ["name", "description", "readme"])
def test_pyproject_has_the_required_fields(field: str) -> None:
    with PYPROJECT.open("rb") as handle:
        project = tomllib.load(handle)["project"]
    assert field in project
