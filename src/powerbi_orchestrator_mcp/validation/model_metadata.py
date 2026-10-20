"""Gather semantic-model and report metadata from a PBIP project.

The audit plan needs to know what is inside a PBIP before it can run
checks over it: which tables exist, which measures carry a DAX
expression that can be linted, and how many pages and visuals the
report contains.

Three on-disk layouts are supported:

- ``definition.pbism`` / ``model.bim`` — the JSON model format.
- ``tables/*.tmdl`` — the TMDL format, one file per table.
- ``<name>.Report/pages/<page>/page.json`` — the report layout.

Nothing here is best-effort guessing: a layout that cannot be read is
reported, not silently treated as "nothing found".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# TMDL measure blocks look like:
#     measure 'Total Sales' = SUM(FactSales[TotalAmount])
_TMDL_MEASURE = re.compile(
    r"^\s*measure\s+'(?P<name>[^']+)'\s*=\s*(?P<expression>.*)$",
    re.MULTILINE,
)

# TMDL table blocks look like:
#     table FactSales {
_TMDL_TABLE = re.compile(r"^\s*table\s+(?P<name>[\w]+)", re.MULTILINE)

# TMDL column blocks look like:
#     column SaleKey = int64
_TMDL_COLUMN = re.compile(
    r"^\s*column\s+(?P<name>[\w]+)\s*=\s*(?P<data_type>[^\n]+)$",
    re.MULTILINE,
)


@dataclass
class ModelMetadata:
    """What was found inside a PBIP project."""

    pbip_path: str
    tables: list[str] = field(default_factory=list)
    columns: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    measures: dict[str, str] = field(default_factory=dict)
    pages: list[str] = field(default_factory=list)
    visuals_count: int = 0
    sources: list[str] = field(default_factory=list)
    """Model files that were read, so the caller can show its work."""

    @property
    def is_empty(self) -> bool:
        """True when nothing at all could be read from the project."""
        return not self.tables and not self.pages and not self.sources


class MetadataError(RuntimeError):
    """Raised when the PBIP cannot be read as a project."""


def _model_files(pbip_root: Path) -> list[Path]:
    """Find the semantic-model definition files inside a PBIP root."""
    found: list[Path] = []
    for pattern in (
        "*.Dataset/definition.pbism",
        "*.SemanticModel/definition.pbism",
        "*.Dataset/model.bim",
        "*.SemanticModel/model.bim",
        "definition.pbism",
        "model.bim",
    ):
        found.extend(sorted(pbip_root.glob(pattern)))
    return found


def _tmdl_table_files(pbip_root: Path) -> list[Path]:
    found: list[Path] = []
    for pattern in (
        "*.Dataset/definition/tables/*.tmdl",
        "*.SemanticModel/definition/tables/*.tmdl",
        "definition/tables/*.tmdl",
    ):
        found.extend(sorted(pbip_root.glob(pattern)))
    return found


def _read_json_model(path: Path, meta: ModelMetadata) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetadataError(f"could not read model file {path}: {exc}") from exc
    model = data.get("model") if isinstance(data, dict) else None
    tables = model.get("tables") if isinstance(model, dict) else None
    if not isinstance(tables, list):
        raise MetadataError(f"model file {path} has no 'model.tables' list")
    for table in tables:
        if not isinstance(table, dict):
            continue
        name = str(table.get("name", ""))
        if not name:
            continue
        meta.tables.append(name)
        meta.columns[name] = [
            {
                "name": str(c.get("name", "")),
                "data_type": str(c.get("dataType", c.get("data_type", ""))),
            }
            for c in table.get("columns", [])
            if isinstance(c, dict) and c.get("name")
        ]
        for measure in table.get("measures", []):
            if not isinstance(measure, dict):
                continue
            measure_name = str(measure.get("name", ""))
            expression = str(measure.get("expression", ""))
            if measure_name:
                meta.measures[measure_name] = expression
    meta.sources.append(str(path))


def _read_tmdl_model(paths: list[Path], meta: ModelMetadata) -> None:
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MetadataError(f"could not read TMDL file {path}: {exc}") from exc
        table_match = _TMDL_TABLE.search(text)
        table_name = table_match.group("name") if table_match else path.stem
        if table_name and table_name not in meta.tables:
            meta.tables.append(table_name)
            meta.columns.setdefault(table_name, [])
        for match in _TMDL_COLUMN.finditer(text):
            meta.columns.setdefault(table_name, []).append(
                {
                    "name": match.group("name"),
                    "data_type": match.group("data_type").strip(),
                }
            )
        for match in _TMDL_MEASURE.finditer(text):
            meta.measures[match.group("name")] = match.group("expression").strip()
        meta.sources.append(str(path))


def _read_report(pbip_root: Path, meta: ModelMetadata) -> None:
    report_dirs = sorted(pbip_root.glob("*.Report")) + sorted(pbip_root.glob("*.Dataset"))
    report_dir: Path | None = None
    for candidate in report_dirs:
        if (candidate / "report.json").exists():
            report_dir = candidate
            break
    if report_dir is None:
        return
    pages_dir = report_dir / "pages"
    if not pages_dir.is_dir():
        return
    for page_file in sorted(pages_dir.glob("*/page.json")):
        meta.pages.append(page_file.parent.name)
        try:
            page_data = json.loads(page_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MetadataError(f"could not read page file {page_file}: {exc}") from exc
        containers = page_data.get("visualContainers")
        if isinstance(containers, list):
            meta.visuals_count += len(containers)
    meta.sources.append(str(report_dir))


def gather_pbip_metadata(pbip_path: str | Path) -> ModelMetadata:
    """Collect the metadata of a PBIP project.

    Raises:
        MetadataError: the path does not exist, is not a PBIP project, or
            contains a model file that cannot be read.
    """
    root = Path(pbip_path)
    if not root.exists():
        raise MetadataError(f"PBIP path does not exist: {root}")
    if not root.is_dir():
        raise MetadataError(f"PBIP path is not a directory: {root}")

    meta = ModelMetadata(pbip_path=str(root))

    model_files = _model_files(root)
    if model_files:
        for model_file in model_files:
            _read_json_model(model_file, meta)
    else:
        tmdl_files = _tmdl_table_files(root)
        if tmdl_files:
            _read_tmdl_model(tmdl_files, meta)

    _read_report(root, meta)

    if meta.is_empty:
        raise MetadataError(
            f"{root} does not look like a PBIP project: no .Dataset/.SemanticModel "
            "definition and no .Report folder were found"
        )
    return meta


def pbip_inspector(target_ref: str) -> dict[str, Any]:
    """Inspect a PBIP into the shape the model differ consumes.

    ``ModelDiffer`` expects ``{"tables": [...], "columns": [...],
    "measures": [...], "relationships": [...]}``. Building that from the
    files on disk is what lets ``diff_models`` work for a real caller:
    an MCP client cannot inject a Python callable.
    """
    meta = gather_pbip_metadata(target_ref)
    columns: list[dict[str, Any]] = []
    for table, table_columns in meta.columns.items():
        for column in table_columns:
            columns.append(
                {
                    # Columns are identified by their qualified reference so
                    # two tables can hold a column with the same name.
                    "name": f"{table}[{column['name']}]",
                    "table": table,
                    "column": column["name"],
                    "data_type": column.get("data_type", ""),
                }
            )
    return {
        "tables": [{"name": table} for table in meta.tables],
        "columns": columns,
        "measures": [
            {"name": name, "expression": expression} for name, expression in meta.measures.items()
        ],
        "relationships": [],
    }


__all__ = [
    "MetadataError",
    "ModelMetadata",
    "gather_pbip_metadata",
    "pbip_inspector",
]
