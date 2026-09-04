# Tool: `edit_report_visual` (v1.1)

> Deterministic CRUD on a single visual: change type, fields, format,
> position, alt text, or hidden flag. Wraps `python_report.update_visual`
> + extends it with field-level merging.

**Status:** v1.1 (Sprint 8)
**Layer:** 2 (Report)
**MVP scope:** Field-level merge; preserves unspecified properties. Atomic
> per-visual: rollback_handle emitted for `apply_plan`.

---

## 1. Inputs

| Name | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `pbip_path` | string | ✅ | — | Path to PBIP folder |
| `page_name` | string | ✅ | — | Page where the visual lives |
| `visual_id` | string | ✅ | — | The `id` field in visualContainer |
| `type` | string | ❌ | `None` | If set, change visual type (e.g. bar → line) |
| `fields_json` | string | ❌ | `None` | JSON of fields to merge (e.g. `{"Values": ["[Sales]"]}`) |
| `format_json` | string | ❌ | `None` | JSON of format/objects to merge |
| `position_json` | string | ❌ | `None` | JSON of `{x, y, width, height}` |
| `alt_text` | string | ❌ | `None` | New alt text |
| `is_hidden` | bool | ❌ | `None` | If set, override hidden flag |

---

## 2. Outputs

| Name | Type | Notes |
|------|------|-------|
| `success` | bool | True iff visual updated |
| `visual_id` | string | Echoed |
| `changes_applied` | list[string] | `["type", "fields", "position", ...]` |
| `page_path` | string | Path to modified page.json |
| `rollback_handle` | string | For apply_plan rollback |

---

## 3. Workflow

1. Resolve report dir + page.json.
2. Read existing page.json.
3. Locate visualContainer by `visual_id`.
4. If not found: `success=False`, error.
5. If found: deep-merge each provided change (type, fields, format,
   position, altText, tabOrder).
6. Atomic write: write to .tmp, rename to page.json.
7. Return success + changes_applied.

---

## 4. Acceptance criteria

- [ ] Missing visual_id → `success=False`, no write.
- [ ] Only fields in the input change; unspecified fields preserved.
- [ ] `altText` change is reflected in WCAG audit delta.
- [ ] Atomic write prevents partial updates on failure.

---

## 5. Failure modes

- **Visual not found:** error "visual <id> not found on page <page>".
- **Invalid position JSON:** error before write.
- **PBIP path missing:** error before write.

---

## 6. Cross-references

- [`../05-engines-adapters.md`](../05-engines-adapters.md) §3 — Python fallback adapter
- [`../tools/safe-rename.md`](../tools/safe-rename.md) — analogous atomic-edit pattern
