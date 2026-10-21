"""Tests for the output paths of the tools that write files.

Three tools write to a path the caller chooses: the data dictionary,
the screenshots and the theme. Their output path has to be validated as
a **write**, not as a read.

The difference matters: the read validator is satisfied by the read
opt-out, so validating a write as a read would let a read-scoped
caller write — and overwrite — anywhere the process can reach. The
write validator bounds it to the workspace, always.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.tools.generate_data_dictionary import (
    generate_data_dictionary,
)
from powerbi_orchestrator_mcp.tools.screenshot_report_pages import (
    screenshot_report_pages,
)


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("PBI_WORKSPACE_ROOT", str(root))
    return root


@pytest.fixture()
def pbip(workspace: Path) -> Path:
    """A PBIP with one table, inside the workspace."""
    root = workspace / "sample.pbip"
    dataset = root / "sample.Dataset"
    dataset.mkdir(parents=True)
    (root / "sample.pbip").write_text("{}", encoding="utf-8")
    report = root / "sample.Report"
    page = report / "pages" / "Overview"
    page.mkdir(parents=True)
    (report / "report.json").write_text("{}", encoding="utf-8")
    (page / "page.json").write_text(json.dumps({"visualContainers": []}), encoding="utf-8")
    (dataset / "definition.pbism").write_text(
        json.dumps(
            {
                "model": {
                    "tables": [
                        {
                            "name": "FactSales",
                            "columns": [
                                {"name": "Amount", "dataType": "decimal"},
                            ],
                            "measures": [],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return root


class TestTheDataDictionary:
    def test_it_can_write_inside_the_workspace(self, workspace: Path, pbip: Path) -> None:
        target = workspace / "diccionario.md"

        generate_data_dictionary(pbip_path=str(pbip), output_path=str(target))

        assert target.exists()
        assert target.read_text().startswith("# Data Dictionary")

    def test_it_cannot_write_outside_the_workspace(
        self, workspace: Path, pbip: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "fuera.md"

        with pytest.raises(ValueError, match="outside allowed workspace root"):
            generate_data_dictionary(pbip_path=str(pbip), output_path=str(target))

        assert not target.exists()

    def test_it_cannot_overwrite_a_file_outside_the_workspace(
        self, workspace: Path, pbip: Path, tmp_path: Path
    ) -> None:
        """Overwriting is the sharp edge of an unvalidated write."""
        victim = tmp_path / "modelo.tmdl"
        victim.write_text("EL MODELO ORIGINAL", encoding="utf-8")

        with pytest.raises(ValueError):
            generate_data_dictionary(pbip_path=str(pbip), output_path=str(victim))

        assert victim.read_text() == "EL MODELO ORIGINAL"

    def test_overwriting_inside_the_workspace_is_allowed(self, workspace: Path, pbip: Path) -> None:
        """The boundary is the workspace, not the file.

        The caller named the output path, so replacing that file is
        what they asked for. What the tool refuses is writing outside
        the workspace.
        """
        target = workspace / "diccionario.md"
        target.write_text("UN DICCIONARIO VIEJO", encoding="utf-8")

        generate_data_dictionary(pbip_path=str(pbip), output_path=str(target))

        assert target.read_text().startswith("# Data Dictionary")


class TestTheScreenshots:
    def test_it_can_write_inside_the_workspace(self, workspace: Path, pbip: Path) -> None:
        target = workspace / "shots"

        screenshot_report_pages(pbip_path=str(pbip), output_dir=str(target))

        assert target.is_dir()

    def test_it_cannot_create_a_directory_outside_the_workspace(
        self, workspace: Path, pbip: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "fuera" / "shots"

        with pytest.raises(ValueError, match="outside allowed workspace root"):
            screenshot_report_pages(pbip_path=str(pbip), output_dir=str(target))

        assert not target.exists()


class TestTheOptOutDoesNotApply:
    def test_a_read_opt_out_does_not_widen_a_write(
        self, workspace: Path, pbip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The read opt-out is not a write opt-out.

        This is the hole: the output path was validated with the read
        validator, which the opt-out satisfies.
        """
        monkeypatch.setenv("PBI_ALLOW_OUTSIDE_WORKSPACE_READ", "1")
        target = tmp_path / "fuera.md"

        with pytest.raises(ValueError):
            generate_data_dictionary(pbip_path=str(pbip), output_path=str(target))

        assert not target.exists()
