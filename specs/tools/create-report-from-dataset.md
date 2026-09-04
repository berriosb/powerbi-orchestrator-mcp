# Tool: `create_report_from_dataset` (v1.1)

> Scaffold a PBIR (Power BI Report) folder from an existing dataset. Creates
> the `<name>.Report/` directory, a default theme, and a sample page
> with one or two visuals based on the dataset schema.

**Status:** v1.1 (Sprint 8)
**Layer:** 2 (Report)
**MVP scope:** Creates a minimal scaffold (1 page, 2 visuals, default
theme). User customizes afterwards via `edit_report_visual`.

---

## 1. Inputs

| Name | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `pbip_path` | string | ✅ | — | Path to PBIP folder (must exist; must have `<name>.Dataset/`) |
| `page_name` | string | ❌ | `"Overview"` | Name of the page to scaffold |
| `visual_count` | int | ❌ | `2` | Number of sample visuals (1-4 supported) |
| `theme` | string | ❌ | `"okabe_ito"` | Colorblind-safe palette |
| `include_card` | bool | ❌ | `True` | Add a card visual on top of measures |
| `inspector` | Any | ❌ | None | Production: modeling engine; tests: mock |

---

## 2. Outputs

| Name | Type | Notes |
|------|------|-------|
| `success` | bool | True iff scaffold created |
| `page_name` | string | Echoed |
| `files_created` | list[string] | e.g. `["sample.Report/report.json", ".../pages/Overview/page.json"]` |
| `visual_ids` | list[string] | Generated `v_<timestamp>` IDs |
| `warnings` | list[string] | e.g. "PBIP path doesn't have .Dataset/" |
| `rollback_handle` | string | Snapshot handle (for apply_plan) |

---

## 3. Workflow

1. Validate PBIP path: must exist, must contain `<name>.Dataset/`.
2. Locate or create `<name>.Report/` next to `<name>.Dataset/`.
3. Write `report.json` with default values (theme ref, version, etc.).
4. Write `theme.json` with the chosen palette.
5. Call `inspector.list_tables()` and `inspector.list_measures()` to pick
   the visuals to scaffold.
6. Add a page via `python_report.add_page`.
7. Add `visual_count` visuals (alternating card + bar) via
   `python_report.add_visual`.
8. Return file list + warnings.

---

## 4. Acceptance criteria

- [ ] Scaffold works on a PBIP with a `<name>.Dataset/` folder.
- [ ] Report dir is created if missing; existing report.json is NOT
      overwritten (returns warning).
- [ ] Theme.json written with the chosen palette.
- [ ] Page has the configured number of visuals (1-4).
- [ ] Each visual has `altText` (per WCAG).
- [ ] `inspector` optional: without it, scaffold has placeholder visuals.

---

## 5. Failure modes

- **PBIP path missing:** `success=False`, `warnings=["PBIP path doesn't exist"]`.
- **No .Dataset/ subfolder:** `success=False`, error explaining structure required.
- **Existing report.json:** no overwrite; warning + `success=False`.

---

## 6. Cross-references

- [`../tools/apply-theme-and-accessibility-rules.md`](apply-theme-and-accessibility-rules.md) — composes the same theme generator
- [`../01-orchestrator.md`](../01-orchestrator.md) §3.3 — `apply_plan` integration via `snapshot` / `rollback`
