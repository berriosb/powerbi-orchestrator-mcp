# Release Notes — v1.1.0 (2026-09-04)

> **Sprint 9 closed.** Three v2 tools + viz foundation now ship on top of
> the v1.0.0 MVP+v1.1 baseline. Brings total tools to **18** (12 MVP + 3
> v1.1 + 3 v2 from Sprint 9). Adds Capa 5 (`viz/` module) to the working
> stack and closes the recommendations of [`status-vs-specs.md`](docs/status-vs-specs.md)
> §9.

**Status:** Beta. Same caveats as v1.0.0 for cloud paths. The new v2 tools
are pure-Python / file-I/O only (`viz/`, `python_report` consumers) and
have **no engine dependencies**, so they are safe to use immediately.

---

## What's new vs v1.0.0

### 3 new v2 tools (Sprint 9)

- **`refactor_to_calculation_groups`** — detect DAX measures sharing a
  common structure (e.g. `Total Sales YTD` / `QTD` / `MTD`) and consolidate
  them into a Tabular Editor calculation group. Regex-based skeleton
  detector covers 12 time-variant suffixes (YTD, QTD, MTD, PY, YOY, MOM,
  QOQ, WOW, Y, Q, M, W, D). Supports `min_candidates` filter
  (default 3), `reconcile_strategy` (`strict` default), `preserve_originals`
  flag, and `auto_apply` for dry-run vs commit. Use an injected
  `inspector` to plug into `powerbi-modeling-mcp` or a mock for tests.

- **`select_visuals_for_kpis`** — for each KPI in a JSON list, return a
  ranked primary visual + alternatives (default top-3). Uses
  `viz/visual_suggester` with anti-recommendation penalties for pie/donut
  charts with >5 categories or with time data, KPI visuals with
  multi-measure, etc. Audience preference bumps: executives favor cards,
  analysts favor tables/scatter/bars.

- **`design_report_page_from_requirements`** — synthesize a complete PBIR
  page from an NL brief (e.g. *"KPI Total Sales, MoM% trend, Top 10
  products by Region"*). Extracts 4 KPI patterns via regex
  (`single_value`, `trend`, `composition`, `ranking`), applies an
  F-pattern layout (2-column row, top-to-bottom), and writes the
  `page.json` atomically via `tempfile.mkstemp` + `os.replace`. Side
  effect: creates a `theme.json` if missing (Okabe-Ito 8-color
  palette by default).

### New module: `viz/` (Capa 5 foundation)

- **`viz/visual_registry.py`** — catalog of 8 native Power BI visual types
  (card, kpi, lineChart, barChart, donutChart, pieChart, scatterChart,
  tableEx) with `cardinality_range`, `supports_time`, `color_safe_default`,
  and SQLBI references. Lookup by id, filter by criteria, all categories
  mirrored from SQLBI classification.

- **`viz/visual_suggester.py`** — deterministic ranking algorithm:
  1. Filter registry by `semantic_type` match.
  2. Filter by `cardinality` fit (in-range bonus; out-of-range penalty).
  3. Penalize visuals without `supports_time` when KPI is time-driven.
  4. Apply audience preference bump (executive/analyst/operational).
  5. Subtract anti-recommendation penalties (`card_high`, `with_time`,
     `multi_measure`).
  6. Sort by score + `priority_default` tie-breaker.

### Tool registration update

The MCP server now registers **17 tools** (was 14 in v1.0.0; gain: 3 v2
tools above; **safe_rename** accessible via the `plan_change` template =
18 tools total).

### Tests

- **+30 unit tests** in `tests/unit/test_v2_tools.py` (526 total, was 496).
  - 7 tests for `viz/visual_registry` (count, lookup, semantic matching,
    cardinality filtering, color-safety flag).
  - 6 tests for `viz/visual_suggester` (single value → card, trend →
    line, pie penalty for high cardinality, pie penalty with time,
    audience preference, `suggest_many`).
  - 7 tests for `refactor_to_calculation_groups` (variant list, 3-member
    grouping, below-threshold, dry-run, remapping, no-inspector warning,
    no-time-variant measures ignored).
  - 4 tests for `select_visuals_for_kpis` (per-KPI suggestion, invalid
    JSON, empty list, unknown semantic fallback).
  - 6 tests for `design_report_page_from_requirements` (missing PBIP,
    KPIs create visuals, no-KPI warning, atomic overwrite, mobile
    layout, rationale text).

### Documentation refresh

- `docs/MVP-STATUS.md` — Sprint 9 marked DONE; coverage table updated
  (Capa 1 +5%, Capa 2 +15%, Capa 5 +20%); test count 496 → 526.
- `docs/status-vs-specs.md` — superseded; 3/9 v2 tools DONE; Sprint 10–11
  roadmap.
- `scripts/verify_mcp_server.py` — `EXPECTED_TOOLS` set expanded from
  14 → 17 (adds the 3 v1.1 tools that were missing from the v1.0.0 list).

---

## Acceptance criteria (SPEC §6.5)

All 9 v1.0.0 acceptance criteria remain passing. No new criteria added
in v1.1.0; Sprint 9 is "bonus track" beyond MVP per SPEC §6.3.

| # | Criterion | Status |
|---|-----------|--------|
| 1 | `pip install` cross-platform | ✅ unchanged |
| 2 | Config JSON en 3 clientes MCP | ✅ unchanged |
| 3 | Sub-flujo MVP workflow 01 end-to-end | ✅ unchanged |
| 4 | `safe_rename` propagation + rollback | ✅ unchanged |
| 5 | `audit_model_and_report` reproducible score | ✅ unchanged |
| 6 | `deploy_to_workspace` mock + real paths | ✅ unchanged |
| 7 | Audit log SQLite + HMAC chain | ✅ unchanged |
| 8 | Test coverage >80% in layers 4 + 6 | ✅ unchanged |
| 9 | `mypy --strict` + `ruff check` clean | ✅ unchanged |

---

## Quality metrics

| Metric | v1.0.0 | v1.1.0 |
|--------|--------|--------|
| Test count | 496 | **526** (+30) |
| MCP tools registered | 14 (15 with safe_rename via template) | **17** (18 with safe_rename) |
| Source files | 48 | **53** (+`viz/{visual_registry,visual_suggester}.py` + 3 tool files already present in src/ from v1.0.0 plumbing) |
| mypy --strict | clean | clean |
| ruff | clean | clean |
| Capa coverage (weighted) | ~67% | **~73%** |
| Tool coverage | 15/27 (56%) | **18/27 (67%)** |

---

## Upgrade guide

```bash
git pull
pip install -e .                          # refreshes editable install
pytest tests/                             # 526/526 expected
python scripts/verify_mcp_server.py       # 17/17 tools expected, exit 0
```

No breaking API changes. The 3 new tools are purely additive.

---

## Known limitations (carried from v1.0.0)

- **`te` adapter** (Tabular Editor as modeling fallback) not wired;
  `refactor_to_calculation_groups` ships with regex-only detection (no
  full TMDL parse) and the `measure_writer` injection seam for
  production use.
- **`pbip-validator`** falls back to structural-only validation.
- **Real-binary integration tests** still deferred (require `te` /
  `powerbi-modeling-mcp` / `dscmd` installed in CI).
- **`design_report_page_from_requirements`** writes `page.json` with
  `projections` based on the suggested visual's `expected_fields`. Field
  resolution against the actual model's column names is delegated to the
  injected `inspector`; if absent, the tool writes literal field names
  from the brief and emits a warning that LLM-side reconciliation may be
  needed.

---

## Roadmap (Sprints 10+)

- **Sprint 10** — `audit_report_ux_and_storytelling`, `screenshot_report_pages`, `optimize_report_performance` (latter has no spec yet — write outline first).
- **Sprint 11** — `promote_in_pipeline`, `setup_rls_and_roles`, `create_semantic_model_from_schema`.
- **Sprint 12+** — v3 (`sync_git_to_workspace`, `set_sensitivity_labels`).
