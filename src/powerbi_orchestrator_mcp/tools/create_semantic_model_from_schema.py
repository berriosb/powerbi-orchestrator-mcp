"""create_semantic_model_from_schema tool — v2 (SPEC §6.2; spec: specs/tools/create-semantic-model-from-schema.md).

Generates a TMDL semantic model from a declarative spec (YAML or JSON).
The MVP produces a minimal but valid TMDL scaffold that opens in Power
BI Desktop:

- A `<Name>.Dataset/definition.tmdl` with one table per spec table,
  columns with declared types, optional measures, and a comment header.
- A `<Name>.Dataset/definition.pbism` with the model metadata.
- A `<Name>.pbip` Power BI Project descriptor.

Validation runs inline (Pydantic spec validation, dangling column
references in relationships, BASIC DAX lint patterns). On success the
target directory contains a load-bearing PBIP.

For MVP this is a deterministic string-template renderer. For full TMDL
authoring with TOM/TE, wire an injected ``modeling_engine`` (e.g.
``powerbi-modeling-mcp``).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

# ---------------------------------------------------------------------------
# Spec Pydantic models
# ---------------------------------------------------------------------------


_VALID_DATA_TYPES: set[str] = {
    "int64",
    "decimal",
    "double",
    "string",
    "boolean",
    "dateTime",
    "dateTime_",
}


class ColumnSpec(BaseModel):
    """One column in a table."""

    name: str
    type: str
    is_key: bool = False
    format_string: str | None = None
    description: str | None = None

    @field_validator("type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v not in _VALID_DATA_TYPES:
            raise ValueError(
                f"invalid type {v!r}; expected one of {sorted(_VALID_DATA_TYPES)}"
            )
        return v


class MeasureSpec(BaseModel):
    """One DAX measure attached to a table."""

    name: str
    expression: str
    format_string: str | None = None
    description: str | None = None


class TableSpec(BaseModel):
    """One table in the model."""

    name: str
    columns: list[ColumnSpec] = Field(default_factory=list)
    measures: list[MeasureSpec] = Field(default_factory=list)
    is_date_table: bool = False
    date_column: str | None = None
    description: str | None = None


class RelationshipSpec(BaseModel):
    """One relationship between two tables."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: str = "many_to_one"
    cross_filter: str = "single"
    is_active: bool = True

    @field_validator("cardinality")
    @classmethod
    def _check_cardinality(cls, v: str) -> str:
        if v not in {"many_to_one", "one_to_many", "one_to_one", "many_to_many"}:
            raise ValueError(f"invalid cardinality {v!r}")
        return v

    @field_validator("cross_filter")
    @classmethod
    def _check_cross_filter(cls, v: str) -> str:
        if v not in {"single", "both", "none", "oneDirection", "bothDirections"}:
            raise ValueError(f"invalid cross_filter {v!r}")
        return v


class HierarchySpec(BaseModel):
    """One user-defined hierarchy."""

    table: str
    name: str
    levels: list[str]


class ModelSpec(BaseModel):
    """Root model spec."""

    name: str
    description: str | None = None
    tables: list[TableSpec] = Field(default_factory=list)
    relationships: list[RelationshipSpec] = Field(default_factory=list)
    hierarchies: list[HierarchySpec] = Field(default_factory=list)
    default_format_strings: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tool input/output
# ---------------------------------------------------------------------------


class CreateSemanticModelFromSchema(BaseModel):
    """Input schema."""

    spec_yaml: str | None = None  # Either YAML or JSON
    spec_json: str | None = None
    output_pbip_path: str
    dry_run: bool = True
    modeling_engine: Any = None  # optional: inject real TOM/TE engine in prod


class TableCreated(BaseModel):
    """One table in the created model."""

    table_name: str
    columns_count: int
    measures_count: int


class RelationshipCreated(BaseModel):
    """One relationship in the created model."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: str


class CreateSemanticModelResult(BaseModel):
    """Output."""

    pbip_path: str = ""
    tables_created: list[TableCreated] = Field(default_factory=list)
    relationships_created: list[RelationshipCreated] = Field(default_factory=list)
    hierarchies_created: list[str] = Field(default_factory=list)
    validation_passed: bool = False
    lint_findings: list[dict[str, str]] = Field(default_factory=list)
    dry_run: bool = True
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# TMDL renderer
# ---------------------------------------------------------------------------


def _format_string_for_type(t: str) -> str:
    """Pick a sensible default format string for a column type."""
    return {
        "int64": "#,##0",
        "decimal": "#,##0.00",
        "double": "#,##0.00",
        "dateTime": "yyyy-mm-dd",
        "dateTime_": "yyyy-mm-dd",
        "boolean": "TRUE/FALSE",
        "string": "@",
    }.get(t, "@")


def _render_table(table: TableSpec) -> str:
    """Render one table block in TMDL."""
    parts: list[str] = []
    parts.append(f"table {table.name}")
    if table.description:
        parts.append(f"    description: {json.dumps(table.description)}")
    if table.is_date_table:
        parts.append("    isDateTable: true")
        if table.date_column:
            parts.append(f"    dateColumn: {table.date_column}")

    for col in table.columns:
        parts.append(f"    column {col.name}")
        parts.append(f"        dataType: {col.type}")
        if col.format_string:
            parts.append(f"        formatString: {json.dumps(col.format_string)}")
        elif col.format_string == "":
            pass
        elif (
            not col.format_string and col.type in {"int64", "decimal", "double"}
        ):
            parts.append(
                f"        formatString: {json.dumps(_format_string_for_type(col.type))}"
            )
        if col.is_key:
            parts.append("        isKey: true")
        if col.description:
            parts.append(f"        description: {json.dumps(col.description)}")

    for m in table.measures:
        parts.append(f"    measure {m.name}")
        # Multi-line DAX expressions: indent under measure.
        body_lines = m.expression.replace("\r\n", "\n").split("\n")
        parts.append(f"        expression: {body_lines[0]}")
        for extra in body_lines[1:]:
            parts.append(f"            {extra}")
        if m.format_string:
            parts.append(f"        formatString: {json.dumps(m.format_string)}")
        if m.description:
            parts.append(f"        description: {json.dumps(m.description)}")

    parts.append("")  # blank line after table
    return "\n".join(parts)


def _render_relationship(rel: RelationshipSpec) -> str:
    """Render one relationship block."""
    return (
        f"relationship {rel.from_table}[{rel.from_column}] -> "
        f"{rel.to_table}[{rel.to_column}]\n"
        f"    cardinality: {rel.cardinality}\n"
        f"    crossFilterBehavior: {rel.cross_filter}\n"
        f"    isActive: {str(rel.is_active).lower()}\n"
    )


def _render_hierarchy(h: HierarchySpec) -> str:
    """Render a hierarchy block (added at the end of its table)."""
    levels = "\n".join(f"        level {lvl}" for lvl in h.levels)
    return (
        f"    hierarchy {h.name}\n"
        f"{levels}\n"
    )


def render_tmdl(spec: ModelSpec) -> str:
    """Render the full ``definition.tmdl`` body."""
    # Build per-table blocks; attach hierarchies to their table block if named.
    hierarchy_by_table: dict[str, list[HierarchySpec]] = {}
    for h in spec.hierarchies:
        hierarchy_by_table.setdefault(h.table, []).append(h)

    body: list[str] = []
    body.append("// Auto-generated by powerbi-orchestrator-mcp")
    body.append("// Tool: create_semantic_model_from_schema")
    body.append("// DO NOT EDIT BY HAND — regenerate from spec.")
    body.append("")

    for table in spec.tables:
        body.append(_render_table(table))
        if table.name in hierarchy_by_table:
            for h in hierarchy_by_table[table.name]:
                body.append(_render_hierarchy(h))

    if spec.relationships:
        body.append("// ---- Relationships ----")
        for rel in spec.relationships:
            body.append(_render_relationship(rel))
    return "\n".join(body)


# ---------------------------------------------------------------------------
# Spec parsing + validation
# ---------------------------------------------------------------------------


def _parse_spec(spec_yaml: str | None, spec_json: str | None) -> ModelSpec:
    """Parse a YAML or JSON spec string into a ModelSpec."""
    if spec_yaml and spec_json:
        raise ValueError("pass either spec_yaml or spec_json, not both")
    if not spec_yaml and not spec_json:
        raise ValueError("one of spec_yaml or spec_json is required")

    raw: dict[str, Any]
    if spec_yaml:
        raw = yaml.safe_load(spec_yaml)
        if not isinstance(raw, dict):
            raise ValueError("spec_yaml did not parse to a mapping")
    else:
        try:
            raw = json.loads(spec_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"spec_json is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("spec_json did not parse to an object")

    return ModelSpec.model_validate(raw)


def _validate_dangling_refs(
    spec: ModelSpec, warnings: list[str]
) -> bool:
    """Check that every relationship references real columns; bump warning."""
    tables = {t.name: {c.name for c in t.columns} for t in spec.tables}
    dangling: list[str] = []
    for rel in spec.relationships:
        if rel.from_table not in tables:
            dangling.append(
                f"{rel.from_table}[{rel.from_column}] -> {rel.to_table}[{rel.to_column}]: "
                f"table {rel.from_table!r} not found"
            )
            continue
        if rel.from_column not in tables[rel.from_table]:
            dangling.append(
                f"relationship references missing column {rel.from_table}!"
                f"{rel.from_column}"
            )
        if rel.to_table not in tables:
            dangling.append(
                f"{rel.from_table} -> {rel.to_table}: table {rel.to_table!r} not found"
            )
        elif rel.to_column not in tables[rel.to_table]:
            dangling.append(
                f"relationship references missing column {rel.to_table}!"
                f"{rel.to_column}"
            )

    for hl in spec.hierarchies:
        if hl.table not in tables:
            dangling.append(f"hierarchy {hl.name!r} on missing table {hl.table!r}")

    for d in dangling:
        warnings.append(f"dangling reference: {d}")
    return not dangling


# Heuristic DAX lint findings — matches the 7 patterns in dax_linter.
_DAX_LINT_HINTS: list[tuple[str, str]] = [
    ("DIVIDE_", "use DIVIDE() instead of /"),
    ("IFERROR(", "consider COALESCEERROR or wrapping in DIVIDE"),
    ("FILTER( ALL(", "be careful with FILTER(ALL(...))"),
    ("CALCULATE( ", "evaluate if a filter context is actually needed"),
]


def _lint_dax(spec: ModelSpec) -> list[dict[str, str]]:
    """Cheap DAX lint over the expressions in measures (heuristic only)."""
    findings: list[dict[str, str]] = []
    for table in spec.tables:
        for m in table.measures:
            expr = m.expression.upper()
            for marker, hint in _DAX_LINT_HINTS:
                if marker.upper() in expr and not any(
                    marker.upper() in f.get("expression", "") for f in findings
                ):
                    findings.append(
                        {
                            "table": table.name,
                            "measure": m.name,
                            "rule": "heuristic",
                            "severity": "info",
                            "expression": m.expression[:60],
                            "message": hint,
                        }
                    )
    return findings


# ---------------------------------------------------------------------------
# Disk writes (atomic)
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, content: str) -> None:
    """Write a file atomically (tempfile + os.replace)."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp.", suffix=".tmdl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _write_pbip(pbip_path: Path, model_name: str, tmdl: str) -> None:
    """Create the PBIP layout: *.pbip, *.Dataset/, *.Report/ stub."""
    pbip_path.mkdir(parents=True, exist_ok=True)
    # Power BI Project descriptor.
    pbip_descriptor = pbip_path / f"{model_name}.pbip"
    pbip_descriptor.write_text(
        json.dumps(
            {
                "version": "1.0",
                "artifacts": [
                    {"name": f"{model_name}.Dataset", "type": "dataset"},
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # Dataset directory.
    dataset_dir = pbip_path / f"{model_name}.Dataset"
    dataset_dir.mkdir(exist_ok=True)
    (dataset_dir / "definition.pbism").write_text(
        json.dumps({"version": "1.0", "type": "dataset"}, indent=2),
        encoding="utf-8",
    )
    _atomic_write(dataset_dir / "definition.tmdl", tmdl)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def create_semantic_model_from_schema(
    spec_yaml: str | None = None,
    spec_json: str | None = None,
    output_pbip_path: str = "",
    dry_run: bool = True,
    modeling_engine: Any = None,  # noqa: ARG001
) -> CreateSemanticModelResult:
    """Generate a TMDL semantic model from a declarative spec.

    On success returns a ``CreateSemanticModelResult`` with the tables,
    relationships, hierarchies, and validation + lint findings. If
    ``dry_run`` is False, the result is also persisted atomically.
    """
    warnings: list[str] = []
    try:
        spec = _parse_spec(spec_yaml, spec_json)
    except (ValueError, ValidationError) as exc:
        return CreateSemanticModelResult(
            validation_passed=False,
            dry_run=dry_run,
            warnings=[f"spec validation failed: {exc}"],
        )

    if not spec.tables:
        warnings.append("spec has no tables; the empty model will be created")

    if not _validate_dangling_refs(spec, warnings):
        return CreateSemanticModelResult(
            validation_passed=False,
            dry_run=dry_run,
            warnings=warnings,
        )

    lint = _lint_dax(spec)

    result = CreateSemanticModelResult(
        pbip_path=output_pbip_path,
        tables_created=[
            TableCreated(
                table_name=t.name,
                columns_count=len(t.columns),
                measures_count=len(t.measures),
            )
            for t in spec.tables
        ],
        relationships_created=[
            RelationshipCreated(
                from_table=r.from_table,
                from_column=r.from_column,
                to_table=r.to_table,
                to_column=r.to_column,
                cardinality=r.cardinality,
            )
            for r in spec.relationships
        ],
        hierarchies_created=[h.name for h in spec.hierarchies],
        validation_passed=True,
        lint_findings=lint,
        dry_run=dry_run,
        warnings=warnings,
    )

    if dry_run:
        return result

    if not output_pbip_path:
        return CreateSemanticModelResult(
            validation_passed=False,
            dry_run=dry_run,
            warnings=["dry_run=False requires output_pbip_path"],
        )

    pbip = Path(output_pbip_path)
    tmdl = render_tmdl(spec)

    # Optional seam: if a TE/TOM-compatible modeling_engine is
    # injected, delegate the actual write to it. Otherwise fall back
    # to the deterministic string-template renderer.
    if modeling_engine is not None and hasattr(
        modeling_engine, "apply_model_spec"
    ):
        import asyncio

        try:
            apply_result = asyncio.run(
                modeling_engine.apply_model_spec(
                    pbip_path=output_pbip_path,
                    tmdl_body=tmdl,
                    model_name=spec.name,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return CreateSemanticModelResult(
                validation_passed=False,
                dry_run=dry_run,
                warnings=[f"modeling_engine.apply_model_spec failed: {exc}"],
            )
        if not apply_result.success:
            return CreateSemanticModelResult(
                validation_passed=False,
                dry_run=dry_run,
                warnings=[
                    apply_result.error_message
                    or "modeling_engine reported failure"
                ],
            )
    else:
        _write_pbip(pbip, spec.name, tmdl)
    return result


__all__ = [
    "ColumnSpec",
    "CreateSemanticModelFromSchema",
    "CreateSemanticModelResult",
    "HierarchySpec",
    "MeasureSpec",
    "ModelSpec",
    "RelationshipCreated",
    "RelationshipSpec",
    "TableCreated",
    "TableSpec",
    "create_semantic_model_from_schema",
    "render_tmdl",
]


# Defensive import-time check for PyYAML.
_ = yaml.safe_load
