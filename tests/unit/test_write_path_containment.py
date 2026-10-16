from __future__ import annotations

import os
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.validation.path_safety import (
    resolve_write_root,
    validate_safe_pbip_path,
    validate_write_path,
)


class TestResolveWriteRoot:
    def test_uses_env_when_set(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(tmp_path))

        assert resolve_write_root() == tmp_path.resolve()

    def test_explicit_arg_wins(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", "/somewhere/else")

        assert resolve_write_root(tmp_path) == tmp_path.resolve()

    def test_falls_back_to_cwd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PBI_WORKSPACE_ROOT", raising=False)

        assert resolve_write_root() == Path.cwd().resolve()


class TestValidateWritePath:
    def test_allows_path_inside_root(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(tmp_path))
        target = tmp_path / "model.pbip"

        assert validate_write_path(str(target)) == target.resolve()

    def test_allows_root_itself(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(tmp_path))

        assert validate_write_path(str(tmp_path)) == tmp_path.resolve()

    def test_rejects_path_outside_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(tmp_path / "allowed"))

        with pytest.raises(ValueError, match="outside allowed workspace root"):
            validate_write_path(str(tmp_path / "elsewhere"))

    def test_rejects_traversal_out_of_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(allowed))

        with pytest.raises(ValueError, match="outside allowed workspace root"):
            validate_write_path(str(allowed / ".." / "escaped"))

    def test_rejects_symlink_pointing_outside_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.json").write_text("{}", encoding="utf-8")
        link = allowed / "link"
        link.symlink_to(outside)
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(allowed))

        with pytest.raises(ValueError, match="outside allowed workspace root"):
            validate_write_path(str(link / "secret.json"))

    def test_rejects_system_dir_without_root_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PBI_WORKSPACE_ROOT", raising=False)

        with pytest.raises(ValueError, match="system directory"):
            validate_write_path("/etc/cron.d/evil")

    def test_rejects_sensitive_dir_without_root_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PBI_WORKSPACE_ROOT", raising=False)

        with pytest.raises(ValueError, match="sensitive directory"):
            validate_write_path(str(Path.home() / ".ssh" / "authorized_keys"))

    def test_rejects_network_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PBI_WORKSPACE_ROOT", raising=False)

        with pytest.raises(ValueError, match="network paths"):
            validate_write_path("//server/share/file")

    def test_rejects_empty_path(self) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_write_path("   ")

    def test_containment_is_not_optional(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Without PBI_WORKSPACE_ROOT the root falls back to CWD, so a write
        outside the project is still refused."""
        monkeypatch.delenv("PBI_WORKSPACE_ROOT", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValueError, match="outside allowed workspace root"):
            validate_write_path(str(tmp_path.parent / "sibling"))

    def test_must_exist_raises_when_absent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(tmp_path))

        with pytest.raises(FileNotFoundError):
            validate_write_path(str(tmp_path / "nope"), must_exist=True)


class TestReadPathUnchanged:
    def test_read_path_root_stays_optional(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("PBI_WORKSPACE_ROOT", raising=False)
        outside = tmp_path / "readable"

        assert validate_safe_pbip_path(str(outside), must_exist=False) == outside.resolve()

    def test_read_path_root_enforced_when_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(tmp_path / "allowed"))

        with pytest.raises(ValueError, match="outside allowed workspace root"):
            validate_safe_pbip_path(str(tmp_path / "elsewhere"), must_exist=False)


class TestWriteToolsAreContained:
    @pytest.mark.parametrize(
        "module_name",
        [
            "commit_workspace_to_git",
            "create_report_from_dataset",
            "create_semantic_model_from_schema",
            "design_report_page_from_requirements",
            "edit_report_visual",
            "run_dax_regression",
            "setup_rls_and_roles",
        ],
    )
    def test_write_tool_modules_use_the_write_guard(self, module_name: str) -> None:
        import importlib

        module = importlib.import_module(f"powerbi_orchestrator_mcp.tools.{module_name}")

        assert "validate_write_path" in dir(module)


def test_env_root_is_restored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(tmp_path))

    assert os.environ["PBI_WORKSPACE_ROOT"] == str(tmp_path)
