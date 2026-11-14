# Release Notes — v1.25.0

**Release date:** 2026-11-13

Minor release completing unattended operational execution, ISO 9001/14001/45001 §7.5 handover generation, end-to-end `ingest_source_table` -> TMDL Power Query M partition wiring, and Spanish numerical parsing reliability on top of `v1.24.0`.

## Added

- **Headless Flow B Report Runner (`powerbi-orchestrator run-report`).**
  - Declarative YAML/JSON job runner (`orchestrator/report_runner.py`: `ReportJobSpec`, `load_report_job_spec`, `run_scheduled_report`) suited for cron / Task Scheduler execution without launching an MCP host or spending LLM tokens.
  - Chains `compute_process_kpis` -> deterministic narrative + `draft_report_summary` check -> `export_kpi_report_bundle` (`.xlsx`/`.pdf` with PII stripping) -> optional `check_report_data_consistency` -> guarded `send_report_email` (`dry_run=True` unless `--live` is specified), recorded in `CloudAuditLog`.
- **Schema Artifact to TMDL Power Query M Partitions (`create_semantic_model_from_schema`).**
  - Added `partition_m` and `schema_artifact_path` on `TableSpec` (bounded via `validate_safe_pbip_path`) and `partitions_attached` on `CreateSemanticModelResult`.
  - Hydrates `.schema.json` artifacts emitted by `ingest_source_table` to attach `partition <Table> = m` blocks in TMDL and infer columns/keys when `columns` is omitted.
- **ISO 9001/14001/45001 §7.5 Handover & Audit Chain Rotation (`export-handoff`, `audit-export`).**
  - Added `powerbi-orchestrator export-handoff` (`orchestrator/iso_handoff.py`: `generate_iso_handoff`) producing `SOLUTION-HANDOFF.md`, `iso-7.5-document-control.csv`, and `iso-7.5-document-control.xlsx` with formula neutralization and SHA-256 digests.
  - Added `--rotate` (verified HMAC chain rollover into `audit-archive-<timestamp>` with genesis link) and `--retention-days` pruning to `audit-export`.

## Fixed

- **Spanish Number Parsing in `draft_report_summary`.**
  - Excluded trailing sentence punctuation from `_NUMBER_TOKEN_RE` (`"0.125."`) and disambiguated Spanish single-dot thousands (`$4.000`, `4.000` with `language="es"`) vs genuine decimals (`0.125`) and English numbers.
- **`audit-export` Subdirectory Coverage & Async CLI Execution.**
  - Fixed `export_audit_bundle` to back up `<home>/audit/audit.db`, `<home>/audit/.audit_anchor`, and `<home>/plans/plans.db` (plus legacy flat fallbacks), and added `_run_coro` in `cli.py` for active event-loop compatibility.
- **Chained E2E & Cloud Adapter Coverage.**
  - Added `tests/integration/test_kpi_reporting_pipeline_e2e.py`, expanded HTTP error/fallback tests across `AsyncFabricBridge` and `send_report_email`, and added `tests/unit/test_mcp_fine_closure.py`.

## Verification

- `ruff check src tests`, `ruff format --check src tests`, `mypy src tests scripts`, `uv lock --check`, `pip-audit --strict --skip-editable .`
- `pytest tests/ --cov=powerbi_orchestrator_mcp --cov-fail-under=80` (1686 passed, 4 Windows-only TE skipped; 87.63% coverage)
- Wheel/sdist build (`python -m build` + `twine check dist/*`) and fresh-venv `verify_mcp_server.py` smoke test
