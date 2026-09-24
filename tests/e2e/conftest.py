"""Shared fixtures for E2E tests.

Each `engine_*_path` fixture returns the path to a real engine binary
if available, otherwise it skips the test with a clear reason. This
allows the suite to run on any machine (the binary is downloaded in
the GitHub Actions workflow via the SHA256-pinned release assets).

The `temp_pbip` fixture creates a fresh copy of the load-bearing
fixture PBIP for each test, so tests don't share mutable state on
disk (critical because `apply_plan` mutates the PBIP).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURE_PBIP_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "load_bearing_pip"
)


@pytest.fixture(scope="session")
def fixture_pbip_source() -> Path:
    """Path to the source load-bearing PBIP fixture.

    See `tests/fixtures/README.md` for the spec (4 tables, ~15
    measures, 4 visuals, RLS, baseline WCAG, 1 intentional DAX
    error).
    """
    if not FIXTURE_PBIP_SOURCE.exists():
        pytest.skip(
            f"Fixture PBIP source not found at {FIXTURE_PBIP_SOURCE}. "
            f"See tests/fixtures/README.md for how to generate it."
        )
    return FIXTURE_PBIP_SOURCE


@pytest.fixture
def temp_pbip(fixture_pbip_source: Path, tmp_path: Path) -> Path:
    """A fresh copy of the load-bearing fixture PBIP for the test.

    Each test gets its own copy under `tmp_path` so mutations from
    `apply_plan` don't leak across tests.
    """
    dest = tmp_path / "fixture_pbi"
    shutil.copytree(fixture_pbip_source, dest)
    return dest


@pytest.fixture(scope="session")
def engine_te_path() -> Path:
    """Path to the `te` (Tabular Editor) CLI binary.

    Looked up in well-known locations; the GH Actions workflow
    downloads it pinned to the SHA256 listed in
    `specs/qa/e2e-testing-strategy.md` §3.3.
    """
    candidates = [
        Path("/usr/local/bin/te"),
        Path("/usr/bin/te"),
        Path.home() / ".dotnet" / "tools" / "te",
        Path("C:/Program Files/Tabular Editor/te.exe"),
    ]
    for c in candidates:
        if c.exists():
            return c
    pytest.skip(
        "`te` binary not found. Install via `dotnet tool install --global "
        "TabularEditor` or download from "
        "https://github.com/TabularEditor/TabularEditor/releases. "
        "CI workflow downloads it pinned to the SHA256 in "
        "specs/qa/e2e-testing-strategy.md §3.3."
    )


@pytest.fixture(scope="session")
def engine_dscmd_path() -> Path:
    """Path to the `dscmd` (DAX Studio CLI) binary.

    Windows-only. On non-Windows runners this fixture skips.
    """
    import sys

    if sys.platform != "win32":
        pytest.skip("`dscmd` is Windows-only.")
    candidates = [
        Path("C:/Program Files/DAX Studio/dscmd.exe"),
        Path("C:/Program Files (x86)/DAX Studio/dscmd.exe"),
    ]
    for c in candidates:
        if c.exists():
            return c
    pytest.skip(
        "`dscmd` binary not found. Install DAX Studio from "
        "https://daxstudio.org/. CI workflow downloads it pinned to "
        "the SHA256 in specs/qa/e2e-testing-strategy.md §3.3."
    )


@pytest.fixture(scope="session")
def engine_pbip_validator_path() -> Path:
    """Path to the `pbip-validator` Python entry point.

    Installed via `pip install pbip-validator` (Microsoft package).
    """
    import shutil

    path = shutil.which("pbip-validator")
    if path is None:
        pytest.skip(
            "`pbip-validator` not installed. Run `pip install "
            "pbip-validator`. CI installs it pinned to the version "
            "in specs/qa/e2e-testing-strategy.md §3.3."
        )
    return Path(path)
