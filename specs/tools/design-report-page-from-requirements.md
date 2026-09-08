# Tool: `design_report_page_from_requirements` (v2)

> Synthesize a complete PBIR page from a brief (NL or structured) plus
> the dataset schema. Selects visuals per KPI, lays them out (grid +
> mobile), applies theme + WCAG defaults, and writes a complete
> `page.json` ready for review.

**Status:** v2 outline → implementation (Sprint 9)
**Layer:** 2 (Report) + 5 (Viz/UX)

---

## 1. Inputs

| Name | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `pbip_path` | string | ✅ | — | PBIP folder (must exist) |
| `page_name` | string | ❌ | `"Overview"` | Page to create |
| `brief` | string | ✅ | — | NL brief (e.g. "KPI YTD, MoM%, Top 10 products, Sales by region") |
| `audience` | string | ❌ | `"executive"` | `executive` / `analyst` / `operational` |
| `palette` | string | ❌ | `"okabe_ito"` | Color palette |
| `inspector` | Any | ❌ | None | Modeling engine for dataset schema |

---

## 2. Outputs

| Name | Type | Notes |
|------|------|-------|
| `page_name` | string | Echoed |
| `visual_count` | int | Number of visuals placed on the page |
| `visual_types` | list[str] | e.g. `["card", "lineChart", "barChart"]` |
| `rationale` | string | Per-KPI explanation of visual choice |
| `files_changed` | list[string] | `[".Report/pages/<page>/page.json"]` |
| `wcag_score` | float | Post-creation WCAG audit score |
| `warnings` | list[string] | E.g. "failed to detect 1 KPI; using default card" |

---

## 3. Workflow

1. **Parse brief**: regex-based extraction of KPIs (single values:
   `KPI X`, `Total X`; trends: `X over time`, `MoM%`; compositions:
   `X by Y`; rankings: `Top N X`).
2. **Resolve fields**: for each KPI, ask inspector (or default to
   measure references) which fields to use.
3. **Select visuals**: call `select_visuals_for_kpis` per KPI.
4. **Compute layout**: 4-column grid; KPI cards at top (high-priority),
   trends in middle, breakdowns at bottom; mobile-first stack.
5. **Apply theme + alt text**: calls `apply_theme_and_accessibility_rules`
   for the page (via a child helper, not the tool itself).
6. **Write page.json** atomically; report changed files.

---

## 4. Acceptance criteria

- [ ] NL brief → ≥1 visual per detected KPI.
- [ ] Default layout: cards top-left, trends top-right, compositions
      bottom (F-pattern).
- [ ] All visuals have altText and tabOrder.
- [ ] WCAG score ≥ 95 (colorblind-safe palette + alt text).
- [ ] Page opens in Power BI Desktop without warnings.

---

## 5. Failure modes

- **Brief too vague:** use a default page (1 card on a KPI + 1 line
  chart on time) and warn.
- **Inspector missing:** scaffold with placeholder fields; warn that
  visuals reference `[Auto]` until the model is introspected.
- **Existing page:** return error (do NOT overwrite).

---

## 6. Cross-references

- [`../tools/select-visuals-for-kpis.md`](../tools/select-visuals-for-kpis.md)
- [`../tools/apply-theme-and-accessibility-rules.md`](../tools/apply-theme-and-accessibility-rules.md)
- [`../../src/viz/visual_suggester.py`](../../src/viz/visual_suggester.py)
