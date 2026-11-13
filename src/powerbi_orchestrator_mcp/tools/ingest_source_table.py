"""ingest_source_table tool — schema-validated CSV/Excel table ingestion.

Reads an Excel (``.xlsx``/``.xlsm``) or CSV file inside ``PBI_WORKSPACE_ROOT``,
validates expected columns, types, and unique key constraints, generates a
normalized CSV + Power Query M partition expression for the target PBIP
semantic model, and neutralizes spreadsheet formula injection.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.tools._tabular_io import (
    MAX_TABULAR_ROWS_DEFAULT,
    escape_excel_formula,
    load_tabular_file,
)
from powerbi_orchestrator_mcp.validation.path_safety import validate_write_path

_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_ ]{0,80}$")

ExpectedType = Literal["string", "int", "float", "date", "bool"]


class IngestSourceTableInput(BaseModel):
    """Input schema for ``ingest_source_table``."""

    source_path: str
    sheet_name: str | None = None
    target_pbip_dir: str
    table_name: str
    expected_schema: dict[str, ExpectedType]
    key_columns: list[str] = Field(default_factory=list)
    allow_extra_columns: bool = True
    max_rows: int = MAX_TABULAR_ROWS_DEFAULT
    dry_run: bool = True


class IngestSourceTableResult(BaseModel):
    """Output schema for ``ingest_source_table``."""

    status: Literal["ok", "invalid", "dry_run"]
    dry_run: bool
    table_name: str
    source_sha256: str = ""
    row_count: int = 0
    columns_matched: list[str] = Field(default_factory=list)
    missing_columns: list[str] = Field(default_factory=list)
    unexpected_columns: list[str] = Field(default_factory=list)
    type_mismatch_count: int = 0
    duplicate_keys_count: int = 0
    null_key_rows: int = 0
    normalized_artifact_path: str | None = None
    generated_m_expression: str | None = None
    warnings: list[str] = Field(default_factory=list)


def _matches_type(value: Any, expected: ExpectedType) -> bool:
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return True
    if expected == "string":
        return True
    if expected == "bool":
        if isinstance(value, bool):
            return True
        return str(value).strip().lower() in {"true", "false", "0", "1", "si", "sí", "no"}
    if expected == "int":
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        if isinstance(value, float):
            return value.is_integer()
        try:
            int(str(value).strip().replace(",", ""))
            return True
        except ValueError:
            return False
    if expected == "float":
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return True
        try:
            float(str(value).strip().replace(",", "."))
            return True
        except ValueError:
            return False
    if expected == "date":
        if isinstance(value, (dt.date, dt.datetime)):
            return True
        raw = str(value).strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
            try:
                dt.datetime.strptime(raw[:10], fmt)
                return True
            except ValueError:
                continue
        return False
    return False


def _m_type_for(expected: ExpectedType) -> str:
    return {
        "string": "type text",
        "int": "Int64.Type",
        "float": "type number",
        "date": "type date",
        "bool": "type logical",
    }[expected]


def _render_power_query_m(
    *,
    normalized_csv_path: Path,
    expected_schema: dict[str, ExpectedType],
) -> str:
    escaped_path = str(normalized_csv_path).replace('"', '""')
    col_types = ", ".join(
        f'{{"{col}", {_m_type_for(dtype)}}}' for col, dtype in expected_schema.items()
    )
    return "\n".join(
        [
            "let",
            f'    Source = Csv.Document(File.Contents("{escaped_path}"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),',
            "    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),",
            f"    TypedColumns = Table.TransformColumnTypes(PromotedHeaders, {{{col_types}}})",
            "in",
            "    TypedColumns",
        ]
    )


async def ingest_source_table(
    source_path: str,
    target_pbip_dir: str,
    table_name: str,
    expected_schema: dict[str, ExpectedType],
    sheet_name: str | None = None,
    key_columns: list[str] | None = None,
    allow_extra_columns: bool = True,
    max_rows: int = MAX_TABULAR_ROWS_DEFAULT,
    dry_run: bool = True,
) -> IngestSourceTableResult:
    """Validate and stage an Excel/CSV source table for a PBIP semantic model."""
    if not _TABLE_NAME_RE.match(table_name.strip()):
        return IngestSourceTableResult(
            status="invalid",
            dry_run=dry_run,
            table_name=table_name,
            warnings=[f"invalid table_name {table_name!r}"],
        )

    target_dir = validate_write_path(target_pbip_dir, must_exist=False)
    tabular = load_tabular_file(
        source_path,
        sheet_name=sheet_name,
        max_rows=max(1, min(max_rows, MAX_TABULAR_ROWS_DEFAULT)),
    )
    warnings = list(tabular.warnings)

    header_set = set(tabular.headers)
    expected_cols = list(expected_schema.keys())
    matched = [c for c in expected_cols if c in header_set]
    missing = [c for c in expected_cols if c not in header_set]
    unexpected = [c for c in tabular.headers if c not in expected_schema]

    if not allow_extra_columns and unexpected:
        warnings.append(f"unexpected columns not permitted: {unexpected}")

    keys = list(key_columns or [])
    for key_col in keys:
        if key_col not in header_set and key_col not in missing:
            missing.append(key_col)

    type_mismatches = 0
    null_key_rows = 0
    duplicate_keys = 0
    seen_keys: set[tuple[str, ...]] = set()

    for row in tabular.rows:
        for col, expected_type in expected_schema.items():
            if col in row and not _matches_type(row.get(col), expected_type):
                type_mismatches += 1
        if keys:
            key_vals: list[str] = []
            has_null = False
            for k in keys:
                raw_k = row.get(k)
                if raw_k is None or str(raw_k).strip() == "":
                    has_null = True
                    break
                key_vals.append(str(raw_k).strip())
            if has_null:
                null_key_rows += 1
            else:
                kt = tuple(key_vals)
                if kt in seen_keys:
                    duplicate_keys += 1
                else:
                    seen_keys.add(kt)

    has_blocking_issues = bool(
        missing
        or (not allow_extra_columns and unexpected)
        or type_mismatches > 0
        or null_key_rows > 0
        or duplicate_keys > 0
    )

    safe_table_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", table_name.strip())
    data_dir = target_dir / "data"
    out_csv = data_dir / f"{safe_table_slug}.normalized.csv"
    m_expr = _render_power_query_m(
        normalized_csv_path=out_csv,
        expected_schema=expected_schema,
    )

    if has_blocking_issues:
        return IngestSourceTableResult(
            status="invalid",
            dry_run=dry_run,
            table_name=table_name,
            source_sha256=tabular.source_sha256,
            row_count=len(tabular.rows),
            columns_matched=matched,
            missing_columns=missing,
            unexpected_columns=unexpected,
            type_mismatch_count=type_mismatches,
            duplicate_keys_count=duplicate_keys,
            null_key_rows=null_key_rows,
            generated_m_expression=m_expr,
            warnings=warnings,
        )

    if dry_run:
        return IngestSourceTableResult(
            status="dry_run",
            dry_run=True,
            table_name=table_name,
            source_sha256=tabular.source_sha256,
            row_count=len(tabular.rows),
            columns_matched=matched,
            missing_columns=missing,
            unexpected_columns=unexpected,
            type_mismatch_count=type_mismatches,
            duplicate_keys_count=duplicate_keys,
            null_key_rows=null_key_rows,
            normalized_artifact_path=str(out_csv),
            generated_m_expression=m_expr,
            warnings=warnings,
        )

    data_dir.mkdir(parents=True, exist_ok=True)
    export_cols = list(expected_cols) if not allow_extra_columns else list(tabular.headers)
    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=export_cols)
        writer.writeheader()
        for row in tabular.rows:
            writer.writerow({col: escape_excel_formula(row.get(col, "")) for col in export_cols})

    meta_path = data_dir / f"{safe_table_slug}.schema.json"
    meta_path.write_text(
        json.dumps(
            {
                "table_name": table_name,
                "source_sha256": tabular.source_sha256,
                "row_count": len(tabular.rows),
                "expected_schema": expected_schema,
                "key_columns": keys,
                "normalized_csv": str(out_csv),
                "power_query_m": m_expr,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return IngestSourceTableResult(
        status="ok",
        dry_run=False,
        table_name=table_name,
        source_sha256=tabular.source_sha256,
        row_count=len(tabular.rows),
        columns_matched=matched,
        missing_columns=missing,
        unexpected_columns=unexpected,
        type_mismatch_count=type_mismatches,
        duplicate_keys_count=duplicate_keys,
        null_key_rows=null_key_rows,
        normalized_artifact_path=str(out_csv),
        generated_m_expression=m_expr,
        warnings=warnings,
    )


__all__ = [
    "IngestSourceTableInput",
    "IngestSourceTableResult",
    "ingest_source_table",
]
