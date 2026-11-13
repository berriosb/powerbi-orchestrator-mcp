# Release Notes — v1.24.0

**Release date:** 2026-11-13

Minor release expanding the MCP orchestrator from semantic-model/PBIP governance to deterministic process KPI tracking, schema-validated tabular ingestion, verified executive reporting (Excel/PDF), guarded email delivery, source-vs-dataset reconciliation, and production Fabric REST/LRO reliability.

## Added

- **KPI Catalog & Deterministic Evaluation (`define_kpi_catalog`, `ingest_source_table`, `compute_process_kpis`).**
  - Repository-backed YAML KPI catalog (`define_kpi_catalog`) with 8 operational process KPI presets, diff preview, and versioned ownership metadata.
  - Schema-validated Excel/CSV ingestion (`ingest_source_table`) generating normalized staged CSVs, Power Query M expressions, duplicate/null key diagnostics, and formula-injection escaping (`'`), bounded to `PBI_WORKSPACE_ROOT`.
  - Deterministic KPI evaluation (`compute_process_kpis`) using `Decimal` arithmetic over CSV/XLSX tables (stripping PII-named columns) or Power BI `executeQueries` DAX calls (`impersonated_user_name` gated by `Tools.Write`).
- **Verified Reporting, Reconciliation & Delivery (`export_kpi_report_bundle`, `draft_report_summary`, `send_report_email`, `check_report_data_consistency`).**
  - Pure-Python `.xlsx` (`openpyxl`) and `.pdf` (`reportlab`) report exporter with SHA-256 checksums, formula escaping, and default PII stripping.
  - Numeric verification guardrail (`draft_report_summary`) verifying that numbers in LLM-generated executive prose match deterministic KPI figures.
  - Audited report email delivery (`send_report_email`) via Microsoft Graph or SMTP with `PBI_EMAIL_ALLOWED_DOMAINS`, `confirm_external_send=True` for external recipients, attachment size caps, and SHA-256 recipient hashing.
  - Live dataset reconciliation (`check_report_data_consistency`) comparing expected/source KPIs against `executeQueries` DAX scalars and latest refresh status.
- **Compliance & Cloud Integration (`AsyncFabricBridge`, `PBI_STRICT_COMPLIANCE`, `cli.py audit-export`).**
  - Added `AsyncFabricBridge` and wired async wrappers into `server.py` to eliminate nested `asyncio.run()` calls inside the active MCP event loop.
  - Added `PBI_STRICT_COMPLIANCE=1` readiness preset and `powerbi-orchestrator audit-export <dest>` CLI subcommand with SHA-256 manifest.

## Fixed

- **Cloud REST & 202 LRO handling.** Updated `RefreshOrchestrator` fallback paths to `/groups/{workspace_id}/datasets/{dataset_id}/refreshes`, switched `deploy_to_workspace` from `PowerBIDataset` to `SemanticModel` with HTTP `202 Accepted` LRO polling, and added `poll_operation`, `list_pipeline_stages`, `deploy_pipeline`, `get_item_definition`, `update_item_definition`, `update_from_git`, and `admin_set_labels` to `FabricClient`.
- **Read-only & write-scope gaps.** Enforced `PBI_READONLY=1` refusal on `screenshot_report_pages` and `generate_data_dictionary(output_path=...)`, and required `Tools.Write` when `generate_data_dictionary` writes `output_path`.

## Verification

- `ruff check src tests`, `ruff format --check src tests`, `mypy src tests scripts`, `uv lock --check`, `pip-audit --strict --skip-editable .`
- `pytest tests/ --cov=powerbi_orchestrator_mcp --cov-fail-under=80`
- Wheel/sdist build (`python -m build` + `twine check dist/*`) and fresh-venv `verify_mcp_server.py` smoke test
