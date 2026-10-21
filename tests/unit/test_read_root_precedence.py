"""Tests for the precedence between the read root and the read opt-out.

The precedence is a safety decision, so it is pinned here: a
``PBI_WORKSPACE_ROOT`` that the operator has set is the boundary, and
nothing widens it. An opt-out that silently loosened it would be a
footgun in a deployment that sets both.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.validation.path_safety import (
    resolve_read_root,
    validate_safe_pbip_path,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No inherited configuration leaks into these tests."""
    for name in ("PBI_WORKSPACE_ROOT", "PBI_ALLOW_OUTSIDE_WORKSPACE_READ"):
        monkeypatch.delenv(name, raising=False)


class TestTheRootWins:
    def test_a_configured_root_is_the_boundary(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(tmp_path))

        assert resolve_read_root() == tmp_path.resolve()

    def test_the_opt_out_does_not_widen_a_configured_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Both set: the root still holds.

        An operator who has declared where their workspace is gets
        exactly that.
        """
        root = tmp_path / "ws"
        root.mkdir()
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(root))
        monkeypatch.setenv("PBI_ALLOW_OUTSIDE_WORKSPACE_READ", "1")

        outside = tmp_path / "outside.pbip"
        outside.mkdir()

        with pytest.raises(ValueError, match="outside allowed workspace root"):
            validate_safe_pbip_path(str(outside))


class TestWithoutARoot:
    def test_the_working_directory_is_the_default(self, tmp_path: Path) -> None:
        assert resolve_read_root() == Path.cwd().resolve()

    def test_the_opt_out_removes_the_boundary(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PBI_ALLOW_OUTSIDE_WORKSPACE_READ", "1")

        assert resolve_read_root() is None

    @pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE"])
    def test_the_documented_spellings(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("PBI_ALLOW_OUTSIDE_WORKSPACE_READ", value)

        assert resolve_read_root() is None


class TestTheHint:
    def test_it_does_not_suggest_something_that_would_not_work(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """With a root set, the opt-out would not help.

        Telling the operator to set it would send them in a circle.
        """
        root = tmp_path / "ws"
        root.mkdir()
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(root))
        outside = tmp_path / "outside.pbip"
        outside.mkdir()

        with pytest.raises(ValueError) as exc:
            validate_safe_pbip_path(str(outside))

        message = str(exc.value)
        assert "PBI_WORKSPACE_ROOT" in message
        assert "does not widen a root that is already set" in message

    def test_without_a_root_it_mentions_both_ways_out(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        monkeypatch.chdir(workspace)
        outside = tmp_path / "outside.pbip"
        outside.mkdir()

        with pytest.raises(ValueError) as exc:
            validate_safe_pbip_path(str(outside))

        message = str(exc.value)
        assert "PBI_WORKSPACE_ROOT" in message
        assert "PBI_ALLOW_OUTSIDE_WORKSPACE_READ=1" in message


class TestTheSystemDirectories:
    @pytest.mark.parametrize(
        "target",
        ["/etc/passwd", "/run/secrets", "/root/.ssh", "/home/someone/.config/gcloud"],
    )
    def test_they_are_refused_even_with_the_opt_out(
        self, monkeypatch: pytest.MonkeyPatch, target: str
    ) -> None:
        """The opt-out removes the workspace boundary, not the denylist."""
        monkeypatch.setenv("PBI_ALLOW_OUTSIDE_WORKSPACE_READ", "1")

        with pytest.raises(ValueError):
            validate_safe_pbip_path(target, must_exist=False)


def test_the_environment_is_not_read_at_import_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The root is resolved per call, so a reconfigured env takes effect."""
    monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(tmp_path))
    first = resolve_read_root()

    monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(tmp_path.parent))
    second = resolve_read_root()

    assert first != second
    assert os.environ["PBI_WORKSPACE_ROOT"] == str(tmp_path.parent)
