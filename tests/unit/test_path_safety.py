from __future__ import annotations

from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.validation.path_safety import validate_safe_pbip_path


class TestPathSafety:
    def test_empty_path_raises(self) -> None:
        with pytest.raises(ValueError, match="pbip_path cannot be empty"):
            validate_safe_pbip_path("")

    def test_whitespace_path_raises(self) -> None:
        with pytest.raises(ValueError, match="pbip_path cannot be empty"):
            validate_safe_pbip_path("   ")

    def test_posix_system_directory_blocked(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_safe_pbip_path("/etc", must_exist=False)

    def test_posix_system_subdir_blocked(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_safe_pbip_path("/etc/shadow", must_exist=False)

    def test_macos_private_directory_blocked(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_safe_pbip_path("/private/etc", must_exist=False)

    def test_macos_private_subdir_blocked(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_safe_pbip_path("/private/etc/hosts", must_exist=False)

    def test_windows_system_directory_blocked(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_safe_pbip_path("C:\\Windows\\System32", must_exist=False)

    def test_windows_program_files_blocked(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_safe_pbip_path("C:/Program Files/malicious", must_exist=False)

    def test_unc_path_blocked(self) -> None:
        with pytest.raises(ValueError, match="network paths are forbidden"):
            validate_safe_pbip_path("\\\\server\\share\\repo.pbip", must_exist=False)

    def test_sensitive_ssh_directory_blocked(self, tmp_path: Path) -> None:
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        with pytest.raises(ValueError, match="sensitive directory"):
            validate_safe_pbip_path(str(ssh_dir), must_exist=False)

    def test_sensitive_aws_directory_blocked(self, tmp_path: Path) -> None:
        aws_dir = tmp_path / ".aws"
        aws_dir.mkdir()
        with pytest.raises(ValueError, match="sensitive directory"):
            validate_safe_pbip_path(str(aws_dir), must_exist=False)

    def test_workspace_root_allows_descendants(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()
        sub = ws / "my_report.pbip"
        sub.mkdir()
        res = validate_safe_pbip_path(str(sub), workspace_root=str(ws))
        assert res == sub.resolve()

    def test_workspace_root_blocks_outside(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()
        outside = tmp_path / "outside.pbip"
        outside.mkdir()
        with pytest.raises(ValueError, match="outside allowed workspace root"):
            validate_safe_pbip_path(str(outside), workspace_root=str(ws))

    def test_workspace_root_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ws = tmp_path / "workspace_env"
        ws.mkdir()
        sub = ws / "my_report.pbip"
        sub.mkdir()
        monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(ws))
        res = validate_safe_pbip_path(str(sub))
        assert res == sub.resolve()

    def test_nonexistent_path_raises_file_not_found(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "does_not_exist.pbip"
        with pytest.raises(FileNotFoundError, match="does not exist"):
            validate_safe_pbip_path(str(nonexistent), must_exist=True)

    def test_file_when_dir_required_raises_value_error(self, tmp_path: Path) -> None:
        file_path = tmp_path / "file.txt"
        file_path.write_text("hello")
        with pytest.raises(ValueError, match="must be a directory"):
            validate_safe_pbip_path(str(file_path), must_exist=True, must_be_dir=True)

    def test_valid_temp_directory_accepted(self, tmp_path: Path) -> None:
        res = validate_safe_pbip_path(str(tmp_path), must_exist=True, must_be_dir=True)
        assert res == tmp_path.resolve()
