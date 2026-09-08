# Release Notes — v1.3.0 (2026-09-04)

> **Sprint 10 closed.** Three more v2 tools now ship, completing the
> 6/9 v2 milestone set by Sprint 9. Total MCP tools: **21** (was 18).
> All accessions are pure-Python / file-I/O and have **no engine
> dependencies**, safe to use immediately.

**Status:** Beta. Same caveats as v1.0.0/v1.1.0 for cloud paths. The
new tools depend only on stdlib (no PIL/Pillow dependency) and the
PBIR JSON layout.

---

## What's new

### 3 new v2 tools (Sprint 10)

- **`optimize_report_performance`** — heuristic PBIR page analyzer.
  Reads `pages/*/page.json`, classifies cost hotspots per visual
  (pie/donut, scatter high-cardinality, custom visual, conditional
  formatting) and per page (density >5 visuals, wide-page stress).
  Returns a 0-100 `performance_score`, `estimated_total_load_ms`, and
  per-hotspot `est_cost` (low/medium/high) with a `fix_suggestion`
  string. Threshold configurable via `target_load_ms`. (`spec/04-viz-ux.md §4`.)

- **`audit_report_ux_and_storytelling`** — heuristic qualitative
  auditor over 5 categories: hierarchy (KPI top-left for executive),
  density, narrative (KPIs before tables), mobile-readiness, cohesion
  (visual style variety). Audience inferred from page name regex
  (`Executive Summary` → executive, `Drill-Down Analysis` → analyst)
  or set explicitly. `strictness` accepts `lenient|standard|strict`.
  Returns overall + per-category scores and a list of findings with
  `auto_fixable` flag and `suggestion`. (`spec/specs/tools/audit-report-ux-and-storytelling.md`.)

- **`screenshot_report_pages`** — best-effort PBIP page capture.
  Without Power BI Desktop Bridge this tool emits:
  - an SVG wireframe per page (visual positions, types) — openable in
    any browser as a static placeholder;
  - a JSON manifest per page (`<page>.manifest.json`) — deterministic,
    suitable for regression diff.
  Optional `baseline_dir` triggers a coarse structural diff (visual
  count delta in % of baseline) against stored manifests. Real PNG /
  PDF rendering requires `superbi-mcp` (Windows-only); absence is
  reported via `rendering_warnings` (does not fail).
  (`spec/specs/tools/screenshot-report-pages.md`.)

### Tool registration update

MCP server now registers **20 tools** (was 17 in v1.1.0; with safe_rename
via the `plan_change` template the user sees 21 tools).

### Tests

- **+38 unit tests** in `tests/unit/test_v3_tools.py` (564 total, was 526).
  - 14 tests for `optimize_report_performance` (missing PBIP, no
    .Report, empty pages, single page no issues, pie hotspot, density,
    custom visual, conditional formatting, meets_target, multi-page
    aggregation, wide-page penalty, invalid JSON page, classification,
    score bounded).
  - 15 tests for `audit_report_ux_and_storytelling` (missing PBIP, no
    pages, KPI top-left, no-KPI hierarchy finding, density,
    narrative, mobile, pie with too many slices, score reproducible,
    meets_target, audience inferred, audience override, filter page,
    narrative sub-score, strictness threshold).
  - 9 tests for `screenshot_report_pages` (missing PBIP, no pages,
    single page writes SVG + manifest, SVG contains labels, filter
    pages, resolution preset, baseline comparison, manifest
    determinism, output dir auto-creation).

### Documentation refresh

- `docs/status-vs-specs.md` — superseded; Sprint 10 closed; 6/9 v2
  tools DONE; coverage table updated (Capa 2 +10%, Capa 5 +25% from
  Sprint 10 tools); tool count 18 → 21; test count 526 → 564.
- `scripts/verify_mcp_server.py` — `EXPECTED_TOOLS` set expanded
  17 → 20 (adds the 3 Sprint 10 tools).
- `src/powerbi_orchestrator_mcp/tools/__init__.py` — exports updated
  for the 3 new tools + 6 new model types.

---

## Acceptance criteria (SPEC §6.5)

All 9 v1.0.0 acceptance criteria remain passing. No new criteria added
in v1.3.0.

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

| Metric | v1.0.0 | v1.1.0 | v1.3.0 |
|--------|--------|--------|--------|
| Test count | 496 | 526 | **564** |
| MCP tools registered | 14 | 17 | **20** (21 with safe_rename) |
| Source files | 48 | 53 | **56** |
| mypy --strict | clean | clean | clean |
| ruff | clean | clean | clean |
| Capa coverage (weighted) | ~67% | ~73% | **~80%** |
| Tool coverage | 15/27 (56%) | 18/27 (67%) | **21/27 (78%)** |

---

## Upgrade guide

```bash
git pull
pip install -e .                            # refreshes editable install
pytest tests/                                # 564/564 expected
python scripts/verify_mcp_server.py          # 20/20 tools expected, exit 0
```

No breaking API changes. The 3 new tools are purely additive and have
no runtime dependencies beyond Python stdlib.

---

## Known limitations (carried from v1.0.0/v1.1.0)

- **`te` adapter** (Tabular Editor as modeling fallback) not wired.
- **`pbip-validator`** falls back to structural-only validation.
- **Real-binary integration tests** still deferred.
- **Screenshot real PNG/PDF** — `screenshot_report_pages` only emits
  SVG placeholders + JSON manifests on Linux/Mac. Windows users with
  Power BI Desktop Bridge + `superbi-mcp` can wire that engine and
  reroute the call; the tool is engine-injectable by design.

---

## Roadmap (Sprint 11)

- **`promote_in_pipeline`** — dev→test→prod deployment pipeline gates.
- **`setup_rls_and_roles`** — automate roles + members matrix.
- **`create_semantic_model_from_schema`** — declarative spec → TMDL scaffold.

Estimated 3 days of dev senior work. Will close the v2 milestone
(9/9 tools) and then move to v3 (`sync_git_to_workspace`,
`set_sensitivity_labels`).
