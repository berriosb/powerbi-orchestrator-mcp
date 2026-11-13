"""Shared bounded tabular I/O and PII / formula-injection helpers.

Used by ``ingest_source_table``, ``compute_process_kpis``,
``export_kpi_report_bundle``, and ``check_report_data_consistency`` so file
containment, Excel/CSV parsing, SHA-256 hashing, PII column stripping, and
spreadsheet formula neutralization share one auditable path.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from powerbi_orchestrator_mcp.validation.path_safety import validate_safe_pbip_path

MAX_TABULAR_ROWS_DEFAULT = 100_000

DEFAULT_PII_COLUMN_HINTS: tuple[str, ...] = (
    "rut",
    "run",
    "dni",
    "pasaporte",
    "email",
    "correo",
    "mail",
    "telefono",
    "celular",
    "phone",
    "sueldo",
    "salario",
    "remuneracion",
    "direccion",
    "domicilio",
    "cuenta_bancaria",
)

_NUMERIC_NEG_RE = re.compile(r"^-\d+(\.\d+)?$")
_FORMULA_TRIGGERS = ("=", "+", "@", "\t", "\r")


@dataclass
class TabularData:
    """Bounded in-memory representation of a CSV or Excel worksheet."""

    headers: list[str]
    rows: list[dict[str, Any]]
    source_sha256: str
    sheet_used: str | None = None
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)


def escape_excel_formula(value: Any) -> Any:
    """Neutralize spreadsheet formula injection payloads when exporting strings.

    Numeric types (``int``, ``float``) and negative numeric strings (e.g.
    ``"-12.5"``) stay unquoted; string values starting with ``=``, ``+``,
    ``@``, ``\\t``, ``\\r``, or a non-numeric ``-`` are prefixed with ``'``.
    """
    if not isinstance(value, str) or not value:
        return value
    if value[0] in _FORMULA_TRIGGERS:
        return "'" + value
    if value[0] == "-" and not _NUMERIC_NEG_RE.match(value.strip()):
        return "'" + value
    return value


def is_pii_column(column_name: str, extra_hints: tuple[str, ...] = ()) -> bool:
    """Return True when a column name indicates personal or sensitive data."""
    normalized = re.sub(r"[^a-z0-9_]", "_", column_name.strip().lower())
    tokens = {tok for tok in normalized.split("_") if tok}
    for hint in (*DEFAULT_PII_COLUMN_HINTS, *extra_hints):
        if hint in tokens or normalized == hint or normalized.startswith(f"{hint}_"):
            return True
    return False


def strip_pii_columns(
    headers: list[str],
    rows: list[dict[str, Any]],
    *,
    mask_value: str | None = None,
) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    """Drop (or mask) PII columns from headers and row dicts."""
    pii_cols = [h for h in headers if is_pii_column(h)]
    if not pii_cols:
        return list(headers), list(rows), []
    pii_set = set(pii_cols)
    if mask_value is None:
        clean_headers = [h for h in headers if h not in pii_set]
        clean_rows = [{k: v for k, v in row.items() if k not in pii_set} for row in rows]
        return clean_headers, clean_rows, pii_cols
    masked_rows = [{k: (mask_value if k in pii_set else v) for k, v in row.items()} for row in rows]
    return list(headers), masked_rows, pii_cols


def compute_file_sha256(path: Path) -> str:
    """Stream-compute SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_tabular_file(
    source_path: str,
    *,
    sheet_name: str | None = None,
    max_rows: int = MAX_TABULAR_ROWS_DEFAULT,
) -> TabularData:
    """Load a ``.csv`` or ``.xlsx``/``.xlsm`` table inside ``PBI_WORKSPACE_ROOT``."""
    safe_path = validate_safe_pbip_path(source_path, must_be_dir=False)
    if not safe_path.exists() or not safe_path.is_file():
        raise FileNotFoundError(f"source table does not exist: {source_path}")

    source_sha = compute_file_sha256(safe_path)
    suffix = safe_path.suffix.lower()
    if suffix == ".csv":
        return _load_csv(safe_path, source_sha=source_sha, max_rows=max_rows)
    if suffix in {".xlsx", ".xlsm"}:
        return _load_xlsx(
            safe_path,
            source_sha=source_sha,
            sheet_name=sheet_name,
            max_rows=max_rows,
        )
    raise ValueError(f"unsupported tabular extension {suffix!r}; expected .csv, .xlsx, or .xlsm")


def _load_csv(path: Path, *, source_sha: str, max_rows: int) -> TabularData:
    rows: list[dict[str, Any]] = []
    truncated = False
    warnings: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = [h.strip() for h in (reader.fieldnames or []) if h and h.strip()]
        for idx, raw_row in enumerate(reader):
            if idx >= max_rows:
                truncated = True
                warnings.append(f"input truncated at max_rows={max_rows}")
                break
            rows.append({k.strip(): v for k, v in raw_row.items() if k is not None and k.strip()})
    return TabularData(
        headers=headers,
        rows=rows,
        source_sha256=source_sha,
        sheet_used=None,
        truncated=truncated,
        warnings=warnings,
    )


def _load_xlsx(
    path: Path,
    *,
    source_sha: str,
    sheet_name: str | None,
    max_rows: int,
) -> TabularData:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        target_sheet = sheet_name or (wb.sheetnames[0] if wb.sheetnames else None)
        if not target_sheet or target_sheet not in wb.sheetnames:
            raise ValueError(
                f"sheet {sheet_name!r} not found in {path.name}; available: {wb.sheetnames}"
            )
        ws = wb[target_sheet]
        row_iter = ws.iter_rows(values_only=True)
        try:
            raw_headers = next(row_iter)
        except StopIteration:
            return TabularData(
                headers=[],
                rows=[],
                source_sha256=source_sha,
                sheet_used=target_sheet,
            )

        headers = [
            str(h).strip() if h is not None and str(h).strip() else f"col_{idx + 1}"
            for idx, h in enumerate(raw_headers)
        ]
        rows: list[dict[str, Any]] = []
        truncated = False
        warnings: list[str] = []
        for idx, raw_vals in enumerate(row_iter):
            if idx >= max_rows:
                truncated = True
                warnings.append(f"input truncated at max_rows={max_rows}")
                break
            if raw_vals is None or all(v is None or v == "" for v in raw_vals):
                continue
            row_dict = {
                headers[col_idx]: (raw_vals[col_idx] if col_idx < len(raw_vals) else None)
                for col_idx in range(len(headers))
            }
            rows.append(row_dict)
        return TabularData(
            headers=headers,
            rows=rows,
            source_sha256=source_sha,
            sheet_used=target_sheet,
            truncated=truncated,
            warnings=warnings,
        )
    finally:
        wb.close()


__all__ = [
    "DEFAULT_PII_COLUMN_HINTS",
    "MAX_TABULAR_ROWS_DEFAULT",
    "TabularData",
    "compute_file_sha256",
    "escape_excel_formula",
    "is_pii_column",
    "load_tabular_file",
    "strip_pii_columns",
]
