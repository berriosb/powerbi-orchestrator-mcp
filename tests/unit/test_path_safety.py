from __future__ import annotations

from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.validation.path_safety import validate_safe_pbip_path


class TestPathSafety:
    def test_empty_path_raises(self) -> None:
        with pytest.raises(ValueError, match="pbip_path cannot be empty"):
            validate_safe_pbip_path("")

    def test_posix_system_directory_blocked(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_safe_pbip_path("/etc", must_exist=False)

    def test_posix_system_subdir_blocked(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_safe_pbip_path("/etc/shadow", must_exist=False)

    def test_windows_system_directory_blocked(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_safe_pbip_path("C:\\Windows\\System32", must_exist=False)

    def test_windows_program_files_blocked(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_safe_pbip_path("C:/Program Files/malicious", must_exist=False)

    def test_unc_path_blocked(self) -> None:
        with pytest.raises(ValueError, match="network paths are forbidden"):
            validate_safe_pbip_path("\\\\server\\share\\repo.pbip", must_exist=False)

    def test_valid_temp_directory_accepted(self, tmp_path: Path) -> None:
        res = validate_safe_pbip_path(str(tmp_path), must_exist=True, must_be_dir=True)
        assert res == tmp_path.resolve()
