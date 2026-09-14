"""Standalone CLI utilities (v1.9.0).

The MCP server runs over stdio and is driven by an LLM. For users who
want to script Power BI / Fabric workflows without launching an LLM
client, these commands give them the same primitives from the
command line.

Subcommands:
- ``validate <pbip>`` — run BPA + DAX lint + WCAG on a PBIP folder
  and print the report. Exits non-zero if the audit score is below
  the threshold (default 70).
- ``inspect <pbip>`` — print a JSON summary of the PBIP structure
  (tables, columns, measures, relationships, RLS roles, visuals).
- ``init <pbip>`` — create a minimal valid PBIP skeleton (TMDL +
  PBIR + 1 page) ready for Power BI Desktop to open.
- ``audit-verify`` — verify the HMAC chain on the audit log
  (equivalent to ``python -m ...orchestrator.audit verify``).
- ``version`` — print the orchestrator version + Python version.
- ``health`` — print the same diagnostics as the ``powerbi_health``
  MCP tool, but as a CLI.

The CLI is intentionally a thin wrapper over the public Python API.
Every subcommand is a single function that takes parsed args + returns
a result; ``main()`` just plumbs argparse to the right one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from powerbi_orchestrator_mcp import __version__
from powerbi_orchestrator_mcp.orchestrator.audit import verify_cli
from powerbi_orchestrator_mcp.tools.audit_model_and_report import (
    AuditCheck,
    audit_model_and_report,
)
from powerbi_orchestrator_mcp.tools.powerbi_health import powerbi_health


def _print_json(obj: Any) -> None:
    """Pretty-print JSON to stdout."""
    print(json.dumps(obj, indent=2, default=str))


async def _cmd_validate(args: argparse.Namespace) -> int:
    """Run a composite audit on a PBIP folder.

    Exits 0 if the score meets ``--min-score``, 1 otherwise.
    """
    pbip_path = Path(args.path)
    if not pbip_path.exists():
        print(f"error: PBIP path does not exist: {pbip_path}", file=sys.stderr)
        return 2

    checks = AuditCheck(
        bpa=not args.skip_bpa,
        dax_lint=not args.skip_dax_lint,
        accessibility=not args.skip_wcag,
        naming=not args.skip_naming,
    )
    result = await audit_model_and_report(
        pbip_path=str(pbip_path),
        bpa_ruleset=args.bpa_ruleset,
        dax_measures=json.loads(args.dax_measures) if args.dax_measures else {},
        checks=checks,
    )

    # Compute a simple composite score from sub-scores.
    scores: dict[str, float] = {}
    if result.bpa_score is not None:
        scores["bpa"] = result.bpa_score
    if result.wcag_score is not None:
        scores["wcag"] = result.wcag_score
    # DAX lint: not scored; just presence of findings.
    if checks.dax_lint:
        scores["dax_lint"] = max(
            0.0, 100.0 - result.dax_lint_findings_count * 5
        )
    composite = (
        sum(scores.values()) / len(scores) if scores else result.overall_score
    )

    summary = {
        "pbip_path": str(pbip_path),
        "composite_score": round(composite, 2),
        "overall_score": result.overall_score,
        "sub_scores": scores,
        "findings_count": len(result.findings),
        "auto_fixable_count": result.auto_fixable_count,
        "warnings": result.warnings,
        "min_score_threshold": args.min_score,
    }

    if args.format == "json":
        _print_json(summary)
    else:
        print(f"PBIP:           {pbip_path}")
        print(f"Composite:      {composite:.1f}")
        for k, v in scores.items():
            print(f"  {k:10s}      {v:.1f}")
        print(f"Findings:       {len(result.findings)}")
        if result.warnings:
            print(f"Warnings:       {result.warnings}")

    return 0 if composite >= args.min_score else 1


async def _cmd_inspect(args: argparse.Namespace) -> int:
    """Print a JSON summary of a PBIP folder structure."""
    from powerbi_orchestrator_mcp.engines.report_python import (
        PythonReportEngine,
    )

    pbip_path = Path(args.path)
    if not pbip_path.exists():
        print(f"error: PBIP path does not exist: {pbip_path}", file=sys.stderr)
        return 2

    report_dir_candidates = list(pbip_path.glob("*.Report"))
    dataset_dir_candidates = list(pbip_path.glob("*.Dataset"))
    metadata_files = list(pbip_path.glob("*.pbip"))

    summary: dict[str, Any] = {
        "pbip_path": str(pbip_path),
        "metadata_files": [str(p.relative_to(pbip_path)) for p in metadata_files],
        "report_dirs": [str(p.relative_to(pbip_path)) for p in report_dir_candidates],
        "dataset_dirs": [
            str(p.relative_to(pbip_path)) for p in dataset_dir_candidates
        ],
    }

    if report_dir_candidates:
        report_dir = report_dir_candidates[0]
        pages_dir = report_dir / "pages"
        summary["pages"] = []
        if pages_dir.exists():
            for page_dir in sorted(pages_dir.iterdir()):
                if page_dir.is_dir():
                    page_json = page_dir / "page.json"
                    if page_json.exists():
                        try:
                            data = json.loads(page_json.read_text(encoding="utf-8"))
                            n_visuals = len(data.get("visualContainers", []))
                            summary["pages"].append(
                                {
                                    "name": page_dir.name,
                                    "path": str(
                                        page_json.relative_to(pbip_path)
                                    ),
                                    "visual_count": n_visuals,
                                    "width": data.get("width", 1280),
                                    "height": data.get("height", 720),
                                }
                            )
                        except (json.JSONDecodeError, OSError):
                            summary["pages"].append(
                                {"name": page_dir.name, "error": "invalid JSON"}
                            )

    # Validate.
    engine = PythonReportEngine()
    validation = await engine.validate_pbir(_dummy_conn(pbip_path))
    summary["validation"] = {
        "valid": validation.valid,
        "findings_count": len(validation.findings),
    }

    _print_json(summary)
    return 0


def _dummy_conn(pbip_path: Path) -> Any:
    from powerbi_orchestrator_mcp.engines.base import ConnectionHandle

    return ConnectionHandle(
        engine="python_report",
        target_type="pbip_folder",
        target_ref=str(pbip_path),
        session_token=str(pbip_path),
    )


def _cmd_init(args: argparse.Namespace) -> int:
    """Create a minimal valid PBIP skeleton."""
    pbip_path = Path(args.path)
    if pbip_path.exists() and not args.force:
        print(
            f"error: {pbip_path} already exists; use --force to overwrite",
            file=sys.stderr,
        )
        return 2

    # ``Power BI Desktop`` saves projects as ``<Name>.pbip`` folders.
    # The folder name (without the .pbip suffix) becomes the model name
    # inside the. ( and .Dataset subfolders.
    name = pbip_path.name
    if name.endswith(".pbip"):
        name = name[: -len(".pbip")]
    if not name:
        print(
            "error: cannot derive project name from path",
            file=sys.stderr,
        )
        return 2
    report_dir = pbip_path / f"{name}.Report"
    dataset_dir = pbip_path / f"{name}.Dataset"

    pbip_path.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(exist_ok=True)
    dataset_dir.mkdir(exist_ok=True)

    # Metadata file (required by Power BI Desktop + our validators).
    (pbip_path / f"{name}.pbip").write_text(
        json.dumps({"version": "1.0", "name": name}, indent=2),
        encoding="utf-8",
    )

    # Model skeleton.
    (dataset_dir / "definition.pbism").write_text(
        json.dumps(
            {
                "$type": "powerbi-encrypted-blob",
                "name": f"{name}SemanticModel",
                "version": "1.0",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # Report skeleton.
    (report_dir / "definition.pbir").write_text(
        json.dumps(
            {
                "$type": "powerbi-desktop-report",
                "version": "1.0",
                "name": name,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # One page.
    page_dir = report_dir / "pages" / "Overview"
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "page.json").write_text(
        json.dumps(
            {
                "$type": "page",
                "name": "Overview",
                "displayName": "Overview",
                "width": 1280,
                "height": 720,
                "visualContainers": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Created PBIP skeleton at {pbip_path}")
    return 0


def _cmd_audit_verify(_args: argparse.Namespace) -> int:
    """Verify the HMAC chain on the audit log."""
    try:
        verify_cli()
        return 0
    except SystemExit as exc:
        return int(exc.code or 1)


async def _cmd_health(_args: argparse.Namespace) -> int:
    """Print powerbi_health diagnostics as JSON."""
    result = await powerbi_health()
    _print_json(result)
    return 0


def _cmd_version(_args: argparse.Namespace) -> int:
    """Print the orchestrator + Python version."""
    import platform

    print(f"powerbi-orchestrator-mcp {__version__}")
    print(f"Python {platform.python_version()} ({platform.system()})")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="powerbi-orchestrator",
        description=(
            "Standalone CLI for the powerbi-orchestrator-mcp package. "
            "Lets you run audits, inspect PBIPs, and verify the audit "
            "log without launching an LLM client."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # validate
    p_validate = sub.add_parser(
        "validate", help="Run a composite audit on a PBIP folder."
    )
    p_validate.add_argument("path", help="Path to the PBIP folder.")
    p_validate.add_argument(
        "--min-score",
        type=float,
        default=70.0,
        help="Exit non-zero if composite score is below this (default: 70).",
    )
    p_validate.add_argument(
        "--bpa-ruleset",
        default="default",
        choices=["default", "performance", "governance"],
        help="BPA ruleset (default: default).",
    )
    p_validate.add_argument(
        "--dax-measures",
        default="{}",
        help="JSON object of DAX measures to lint (default: {}).",
    )
    p_validate.add_argument("--skip-bpa", action="store_true")
    p_validate.add_argument("--skip-dax-lint", action="store_true")
    p_validate.add_argument("--skip-wcag", action="store_true")
    p_validate.add_argument("--skip-naming", action="store_true")
    p_validate.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text).",
    )
    p_validate.set_defaults(func=_cmd_validate)

    # inspect
    p_inspect = sub.add_parser(
        "inspect", help="Print a JSON summary of a PBIP folder."
    )
    p_inspect.add_argument("path", help="Path to the PBIP folder.")
    p_inspect.set_defaults(func=_cmd_inspect)

    # init
    p_init = sub.add_parser(
        "init", help="Create a minimal valid PBIP skeleton."
    )
    p_init.add_argument("path", help="Path for the new PBIP folder.")
    p_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing folder.",
    )
    p_init.set_defaults(func=_cmd_init)

    # audit-verify
    p_audit = sub.add_parser(
        "audit-verify",
        help="Verify the HMAC chain on the audit log.",
    )
    p_audit.set_defaults(func=_cmd_audit_verify)

    # health
    p_health = sub.add_parser(
        "health",
        help="Print powerbi_health diagnostics as JSON.",
    )
    p_health.set_defaults(func=_cmd_health)

    # version
    p_version = sub.add_parser("version", help="Print the orchestrator version.")
    p_version.set_defaults(func=_cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    func = args.func
    if asyncio.iscoroutinefunction(func):
        return int(asyncio.run(func(args)))
    result = func(args)
    return int(result) if result is not None else 0


__all__ = ["main"]


if __name__ == "__main__":
    sys.exit(main())
