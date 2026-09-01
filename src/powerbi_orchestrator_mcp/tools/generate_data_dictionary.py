"""generate_data_dictionary tool — Markdown + Mermaid (SPEC §6.1 #11)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class GenerateDataDictionary(BaseModel):
    """Input schema for ``generate_data_dictionary`` tool (SPEC §6.1 #11)."""

    pbip_path: str
    output_path: str | None = None  # if None, returns Markdown in result
    inspector: Any = None  # injected; produces tables/columns/measures
    format: str = "markdown"  # markdown | html (markdown is default)


class DataDictionaryResult(BaseModel):
    """Output of generate_data_dictionary."""

    markdown: str = ""
    coverage_score: float  # 0-1 (descriptions present)
    tables_documented: int = 0
    columns_documented: int = 0
    columns_missing_description: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _escape_mermaid(text: str) -> str:
    """Escape characters that would break Mermaid syntax."""
    return text.replace('"', "'").replace("\n", " ").replace(";", ",")


def generate_data_dictionary(
    pbip_path: str,
    output_path: str | None = None,
    *,
    inspector: Any = None,
    format: str = "markdown",  # noqa: ARG001
) -> DataDictionaryResult:
    """Generate a Markdown data dictionary for a PBIP folder.

    For MVP, the inspector is injected (tests provide a mock; production
    wraps the modeling engine's list_tables/list_columns/list_measures).

    The output includes:
    - Mermaid ER diagram (tables + relationships).
    - Per-table sections with column details.
    - Coverage score (description presence).
    - List of columns missing description (elicits user to fill in).
    """
    if inspector is None:
        # Without an inspector, return an empty result + warning.
        return DataDictionaryResult(
            markdown=(
                "# Data Dictionary\n\n_No model data available; "
                "pass an ``inspector`` to populate._\n"
            ),
            coverage_score=0.0,
            warnings=[
                "no inspector provided — pass modeling_engine "
                "from production"
            ],
        )

    tables = inspector.list_tables()
    columns_by_table = {
        t["name"]: inspector.list_columns(t["name"]) for t in tables
    }
    measures = inspector.list_measures()
    relationships = inspector.list_relationships()

    warnings: list[str] = []
    coverage_total = 0
    coverage_with_desc = 0
    columns_missing: list[str] = []

    # Build Markdown body.
    lines: list[str] = ["# Data Dictionary", ""]
    lines.append(f"_PBIP: `{pbip_path}`_")
    lines.append("")

    # Mermaid ER diagram.
    lines.append("## Entity-Relationship Diagram")
    lines.append("")
    lines.append("```mermaid")
    lines.append("erDiagram")
    for t in tables:
        tname = _escape_mermaid(t["name"])
        lines.append(f"    {tname} {{")
        for col in columns_by_table[t["name"]]:
            colname = _escape_mermaid(col["name"])
            lines.append(f"        {col['data_type']} {colname}")
        lines.append("    }")
    for r in relationships:
        from_t = _escape_mermaid(r["from_table"])
        to_t = _escape_mermaid(r["to_table"])
        lines.append(f"    {from_t} ||--o{{ {to_t} : relates")
    lines.append("```")
    lines.append("")

    # Tables.
    lines.append("## Tables")
    lines.append("")
    for t in tables:
        tname = t["name"]
        lines.append(f"### {tname}")
        lines.append("")
        desc = t.get("description")
        if desc:
            lines.append(f"_{desc}_")
        else:
            lines.append("_No description._")
            warnings.append(f"table {tname!r} missing description")
        lines.append("")
        lines.append("| Column | Type | Description |")
        lines.append("|--------|------|-------------|")
        for col in columns_by_table[tname]:
            cdesc = col.get("description") or ""
            if cdesc:
                coverage_with_desc += 1
            else:
                columns_missing.append(f"{tname}.{col['name']}")
            coverage_total += 1
            lines.append(
                f"| `{col['name']}` | {col['data_type']} | {cdesc or '_missing_'} |"
            )
        lines.append("")

    # Measures.
    if measures:
        lines.append("## Measures")
        lines.append("")
        lines.append("| Measure | Table | Expression |")
        lines.append("|---------|-------|------------|")
        for m in measures:
            lines.append(
                f"| `{m['name']}` | {m['table']} | "
                f"`{_escape_mermaid(m['expression'])}` |"
            )
        lines.append("")

    coverage_score = coverage_with_desc / coverage_total if coverage_total else 0.0

    markdown = "\n".join(lines)

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")

    return DataDictionaryResult(
        markdown=markdown,
        coverage_score=coverage_score,
        tables_documented=len(tables),
        columns_documented=coverage_with_desc,
        columns_missing_description=columns_missing,
        warnings=warnings,
    )
