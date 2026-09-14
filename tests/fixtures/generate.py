"""Generate the synthetic PBIP fixture (tests/fixtures/sample.pbip).

Idempotent — running this twice with the same seed produces the same
output. Deterministic — uses a fixed seed so tests are reproducible.

Spec: tests/fixtures/README.md

Usage:
    python -m tests.fixtures.generate
    python -m tests.fixtures.generate --output /tmp/foo.pbip --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

FIXTURE_VERSION = "0.1.0"
SEED = 42


def _random_measures(rng: random.Random) -> list[dict[str, str]]:
    """A small set of DAX measures — 5 clean + 1 with an anti-pattern."""
    return [
        {
            "name": "Total Sales",
            "table": "FactSales",
            "expression": "SUM(FactSales[TotalAmount])",
        },
        {
            "name": "YTD Sales",
            "table": "FactSales",
            "expression": "TOTALYTD([Total Sales], DimDate[Date])",
        },
        {
            "name": "Prior Month Sales",
            "table": "FactSales",
            "expression": (
                "CALCULATE([Total Sales], "
                "DATEADD(DimDate[Date], -1, MONTH))"
            ),
        },
        {
            "name": "Order Count",
            "table": "FactSales",
            "expression": "COUNTROWS(FactSales)",
        },
        {
            "name": "Avg Price",
            "table": "FactSales",
            "expression": (
                "DIVIDE("
                "SUM(FactSales[UnitPrice]), "
                "SUM(FactSales[Units]))"
            ),
        },
        # One DAX linter anti-pattern (BP_DIVIDE_VS_SLASH).
        {
            "name": "Avg Price Bad",
            "table": "FactSales",
            "expression": (
                "SUM(FactSales[UnitPrice]) / "
                "SUM(FactSales[Units])"
            ),
        },
    ]


def _make_pages() -> list[dict]:
    """Two pages: Overview (with KPI + bar) + Detail (empty)."""
    return [
        {
            "name": "Overview",
            "displayName": "Overview",
            "width": 1280,
            "height": 720,
            "visualContainers": [
                {
                    "$type": "visualContainer",
                    "id": "v1",
                    "x": 0,
                    "y": 0,
                    "width": 400,
                    "height": 150,
                    "altText": "KPI card showing YTD Sales",
                    "visual": {
                        "$type": "card",
                        "id": "v1",
                        "projections": {
                            "Values": [{"queryRef": "[YTD Sales]"}]
                        },
                    },
                    "tabOrder": 0,
                },
                {
                    "$type": "visualContainer",
                    "id": "v2",
                    "x": 420,
                    "y": 0,
                    "width": 800,
                    "height": 300,
                    "altText": "Trend chart showing sales over time",
                    "visual": {
                        "$type": "lineChart",
                        "id": "v2",
                        "projections": {
                            "Category": [
                            {"queryRef": "DimDate[MonthName]"}
                            ],
                            "Y": [{"queryRef": "[Total Sales]"}],
                        },
                    },
                    "tabOrder": 1,
                },
            ],
        },
        {
            "name": "Detail",
            "displayName": "Detail",
            "width": 1280,
            "height": 720,
            "visualContainers": [],
        },
    ]


def _make_dataset_definition() -> dict:
    """Minimal TMDL-style dataset definition."""
    return {
        "model": {
            "tables": [
                {
                    "name": "FactSales",
                    "columns": [
                        {"name": "SaleKey", "dataType": "int64"},
                        {"name": "DateKey", "dataType": "int64"},
                        {"name": "ProductKey", "dataType": "int64"},
                        {"name": "RegionKey", "dataType": "int64"},
                        {"name": "Units", "dataType": "int64"},
                        {"name": "UnitPrice", "dataType": "decimal"},
                        {"name": "Discount", "dataType": "decimal"},
                        {"name": "TotalAmount", "dataType": "decimal"},
                    ],
                    "measures": _random_measures(random.Random(SEED)),
                },
                {
                    "name": "DimDate",
                    "columns": [
                        {"name": "Date", "dataType": "dateTime"},
                        {"name": "Year", "dataType": "int64"},
                        {"name": "Month", "dataType": "int64"},
                        {"name": "MonthName", "dataType": "string"},
                    ],
                },
                {
                    "name": "DimProduct",
                    "columns": [
                        {"name": "ProductKey", "dataType": "int64"},
                        {"name": "ProductName", "dataType": "string"},
                        {"name": "Category", "dataType": "string"},
                    ],
                },
                {
                    "name": "DimRegion",
                    "columns": [
                        {"name": "RegionKey", "dataType": "int64"},
                        {"name": "Region", "dataType": "string"},
                        {
                            "name": "ManagerEmail",
                            "dataType": "string",
                            "description": (
                                "PII: email del gerente regional"
                            ),
                        },
                    ],
                },
            ]
        }
    }


def _make_report_definition() -> dict:
    """Minimal report definition."""
    return {"$type": "powerbi-desktop-report", "version": "1.0", "name": "Sample"}


def _make_report_json() -> dict:
    """Legacy report.json (kept for validator compatibility)."""
    return {"name": "Sample Report", "version": "1.0"}


def generate(output_dir: Path, seed: int = SEED) -> None:
    """Generate the fixture. Idempotent for the same seed."""
    output_dir.mkdir(parents=True, exist_ok=True)
    name = output_dir.name
    if name.endswith(".pbip"):
        name = name[: -len(".pbip")]
    report_dir = output_dir / f"{name}.Report"
    dataset_dir = output_dir / f"{name}.Dataset"

    # Metadata file.
    (output_dir / f"{name}.pbip").write_text(
        json.dumps(
            {
                "version": "1.0",
                "name": name,
                "fixture_version": FIXTURE_VERSION,
                "seed": seed,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # Dataset.
    dataset_dir.mkdir(exist_ok=True)
    (dataset_dir / "definition.pbism").write_text(
        json.dumps(_make_dataset_definition(), indent=2),
        encoding="utf-8",
    )

    # Report.
    report_dir.mkdir(exist_ok=True)
    (report_dir / "definition.pbir").write_text(
        json.dumps(_make_report_definition(), indent=2),
        encoding="utf-8",
    )
    (report_dir / "report.json").write_text(
        json.dumps(_make_report_json(), indent=2),
        encoding="utf-8",
    )

    # Pages.
    pages_dir = report_dir / "pages"
    pages_dir.mkdir(exist_ok=True)
    for page in _make_pages():
        page_dir = pages_dir / page["name"]
        page_dir.mkdir(exist_ok=True)
        (page_dir / "page.json").write_text(
            json.dumps(page, indent=2), encoding="utf-8"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the sample.pbip test fixture."
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).parent / "sample.pbip"),
        help="Output directory for the generated PBIP.",
    )
    parser.add_argument(
        "--seed", type=int, default=SEED, help="Random seed (default: 42)."
    )
    args = parser.parse_args()
    out = Path(args.output)
    generate(out, seed=args.seed)
    print(f"Generated fixture at {out} (seed={args.seed}, version={FIXTURE_VERSION})")


if __name__ == "__main__":
    main()
