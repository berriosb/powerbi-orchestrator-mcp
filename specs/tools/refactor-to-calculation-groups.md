# Tool: `refactor_to_calculation_groups` (v2)

> Auto-detect DAX measures that share a common structure (e.g.
> `Total Sales YTD`, `Total Sales QTD`, `Total Sales MTD`), consolidate
> them into a Tabular Editor calculation group, and reconcile total
> measures that aggregate the affected measures.

**Status:** v2 outline → implementation (Sprint 9)
**Layer:** 1 (Modeling) + 4 (Validation)

---

## 1. Inputs

| Name | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `target` | Target descriptor | ✅ | — | PBIP folder (`.Dataset/`) or Fabric workspace |
| `min_candidates` | int | ❌ | `3` | Min measures sharing structure to trigger refactor |
| `reconcile_strategy` | string | ❌ | `"strict"` | `strict` (no drift tolerance) vs `tolerance` (configurable) |
| `preserve_originals` | bool | ❌ | `false` | If true, keep originals as aliases to the new calc-group items |
| `auto_apply` | bool | ❌ | `false` | If false, plan only (dry-run); require explicit commit |
| `inspector` | Any | ❌ | None | Injected: modeling engine in production, mock in tests |

---

## 2. Outputs

| Name | Type | Notes |
|------|------|-------|
| `groups_created` | list[dict] | Each: name, items, reconciliation strategy |
| `measures_remapped` | dict[str, str] | Map of original measure name → new calc-group item reference |
| `reconciliation_diffs` | list[dict] | Per total measure: pre/post values, max drift % |
| `dry_run` | bool | True if `auto_apply=False` |
| `changed_files` | list[string] | `.tmdl` files modified (if `auto_apply=True`) |
| `rollback_handle` | string | For `apply_plan` rollback (only if `auto_apply=True`) |
| `warnings` | list[string] | E.g. "candidate measure X has dynamic scope, skipping" |

---

## 3. Workflow

1. **Inventory**: call `inspector.list_measures()` and group by common
   expression skeleton (strip year/quarter/month tokens via regex).
2. **Filter**: keep groups with `≥ min_candidates` measures.
3. **Synthesize calc-group**:
   - For each group: extract the varying token (e.g. "YTD", "QTD",
     "MTD", "PY") as the item name.
   - Build the calc-group DAX using `SELECTEDMEASURE()` and
     `ISINSCOPE()` patterns.
4. **Reconcile totals**: for each total measure (e.g. "Total Sales")
   that aggregates the affected measures, run the original + new
   versions against a sample dataset and compare within tolerance.
5. **Persist** (if `auto_apply=True`): write the calc-group TMDL file;
   mark original measures as aliases if `preserve_originals=True`.

---

## 4. Acceptance criteria

- [ ] Group detection finds ≥3 measures sharing structure (regex-based,
      no AST parsing in MVP).
- [ ] Calc-group generated with one item per variant.
- [ ] Reconciliation runs against test data; `max_drift_pct` reported.
- [ ] `dry_run=True` returns the plan without writing files.
- [ ] `preserve_originals=True` keeps originals as aliases (verified via
      `inspector.list_measures()` after refactor).
- [ ] Atomic write via temp-then-rename.
- [ ] Audit log entry on success.

---

## 5. Failure modes

- **Group < min_candidates:** return empty `groups_created`; warning
  explains why.
- **Reconciliation drift > tolerance:** return the diff; don't write
  unless `auto_apply=True` AND `reconcile_strategy="tolerance"` is
  set with `tolerance_pct` explicitly chosen.
- **Original measure used outside the group:** flag as a `dependency`
  warning; user must explicitly choose `preserve_originals=True`.

---

## 6. Cross-references

- [`../03-validation.md`](../03-validation.md) — DAX regression for
  reconciliation drift.
- [`../tools/safe-rename.md`](../tools/safe-rename.md) — analogous
  pattern for atomic edits with rollback.
