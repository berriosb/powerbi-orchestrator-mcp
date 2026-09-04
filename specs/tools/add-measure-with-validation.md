# Tool: `add_measure_with_validation` (v1.1)

> Add a DAX measure to a semantic model with **lint validation** before
> persistence and **runtime check** (best-effort) after.

**Status:** v1.1 (Sprint 8)
**Layer:** 1 (Modeling) + 4 (Validation)
**MVP scope:** Lint gate mandatory, runtime check optional. Blocking by
default; dry-run mode returns findings without writing.

---

## 1. Inputs

| Name | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `target` | Target descriptor | ✅ | — | PBIP folder or Fabric workspace dataset |
| `measure_name` | string | ✅ | — | Valid DAX identifier |
| `table` | string | ✅ | — | Destination table |
| `expression` | string | ✅ | — | DAX formula body (without `:=`) |
| `format_string` | string | ❌ | `None` | e.g. `"$#,##0"` |
| `description` | string | ❌ | `None` | Sets measure.description (documentation). |
| `is_hidden` | bool | ❌ | `false` | |
| `lint_rules` | list[string] | ❌ | all 7 patterns | Subset of `DaxLinter.DaxLinter_PATTERNS` |
| `fail_on_severity` | string | ❌ | `"warning"` | One of `error`, `warning`, `info` |
| `dry_run` | bool | ❌ | `false` | Return findings + lint result without writing |
| `runtime_check` | bool | ❌ | `false` | Best-effort: parse via modeling engine, warn if uncertain |

---

## 2. Outputs

| Name | Type | Notes |
|------|------|-------|
| `success` | bool | True iff measure added AND no lint findings above threshold |
| `measure_name` | string | Echoed for verification |
| `lint_findings` | list[dict] | Findings with `rule_id`, `severity`, `line_number`, `message`, `rewrite_suggestion` |
| `runtime_check` | dict | `{ran: bool, error: str | None, parsed_ok: bool}` |
| `changed_files` | list[string] | e.g. `["sample.Dataset/definition.tmdl"]` |
| `rollback_handle` | string | For undo (used by apply_plan's rollback) |

---

## 3. Workflow

1. **Lint the expression** via `DaxLinter.lint(expression)`.
2. If any finding has `severity ≥ fail_on_severity`:
   - If `dry_run=False` → return early with `success=False`, no write.
   - If `dry_run=True` → still return findings without writing.
3. Persist via modeling engine (`modeling_mcp.create_measure`).
4. If `runtime_check=True`: ask modeling engine to compile the measure
   (best-effort — depends on engine capability).
5. Return success + changed_files + lint summary.

---

## 4. Acceptance criteria

- [ ] Lint 7 patterns run on every expression.
- [ ] `fail_on_severity="warning"` blocks by default.
- [ ] `dry_run=True` returns findings + `success=False` without writing.
- [ ] Successful create updates `changed_files` correctly.
- [ ] Runtime check gracefully degrades when engine doesn't support
      measure compilation.
- [ ] Audit log entry written on success/failure.

---

## 5. Failure modes

- **Lint fails at severity threshold:** return `success=False` with the
  blocking findings so the agent can show them to the user.
- **Modeling engine unavailable:** `EngineNotFoundError` per spec 06.
- **Measure name collision:** if engine reports the name exists, return
  `success=False` with `error_message="measure already exists"`.
- **Malformed DAX:** modeling engine returns parse error → surface.

---

## 6. Cross-references

- [`../03-validation.md`](../03-validation.md) §2.2 — DAX linter
- [`../01-orchestrator.md`](../01-orchestrator.md) §2.2 — planner integration
- [`../tools/safe-rename.md`](../tools/safe-rename.md) — analogous pattern
