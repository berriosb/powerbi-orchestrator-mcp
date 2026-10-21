# Tool: `simplify_dax_measure` (v1.21)

> Rewrite a DAX expression in its shorter, **equivalent** form, and say
> which rewrites produced it.

**Status:** v1.21
**Layer:** 1 (Modeling) + 4 (Validation)
**MVP scope:** Analysis only. It does not write anything.

---

## 1. Inputs

| Name | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `expression` | string | ❌ | `None` | The DAX expression to simplify. Mutually exclusive with `pbip_path`. |
| `pbip_path` | string | ❌ | `None` | A PBIP project to read the measures from. |
| `measure` | string | ❌ | `None` | The measure to simplify. Requires `pbip_path`. |
| `all_measures` | bool | ❌ | `false` | Simplify every measure in the project. Requires `pbip_path`. |
| `apply_lint` | bool | ❌ | `true` | Include the linter verdict before and after. |

Exactly one of these has to be given: an expression, or a project plus
either a measure name or `all_measures`.

---

## 2. Outputs

| Name | Type | Notes |
|------|------|-------|
| `success` | bool | False when the expression could not be parsed, or the arguments did not make sense. |
| `mode` | string | `expression`, `measure` or `project`. |
| `original_expression` | string | Echoed for verification. |
| `simplified_expression` | string | The rewritten expression. Unchanged when nothing applied. |
| `rewrites` | list[dict] | `rule_id`, `description`, `before`, `after`. |
| `changed` | bool | Whether the expression changed. |
| `equivalence` | string | `structural` — see §4. `not_analysed` when the expression could not be parsed. |
| `lint_before` / `lint_after` | list[dict] | The linter verdict on each version. |
| `measures_examined` | int | In `project` mode. |
| `measures_simplifiable` | int | In `project` mode: how many could be written shorter. |
| `error_message` | string | Why it failed, when it did. |

---

## 3. Workflow

1. Resolve the expression: from the argument, from the named measure,
   or from every measure in the project.
2. Parse it. An expression the parser does not understand is returned
   unchanged, with `success: false` and the reason.
3. Apply the rewrites until the expression stops changing.
4. Lint both versions, so a rewrite that silences a finding — or
   creates one — is visible.
5. Return the expression and the list of rewrites.

---

## 4. What "equivalent" means here

Every rewrite is **structural**: it changes the shape of the expression
and not what it computes. The expression is never evaluated against
data, so the result says `equivalence: "structural"` rather than
claiming an equivalence it cannot demonstrate.

The rewrites, and why each one holds:

| Original | Rewritten | Why |
|----------|-----------|-----|
| `IF(ISBLANK(x), 0, x)` | `COALESCE(x, 0)` | Both return 0 for a blank and the value otherwise. |
| `IF(ISERROR(e), fallback, e)` | `IFERROR(e, fallback)` | Same result, and `e` is evaluated once instead of twice. |
| `IF(c, TRUE(), FALSE())` | `c` | `IF` with two boolean branches returns the condition. |
| `IF(c, a, a)` | `a` | Both branches are the same, so the condition does not matter. |
| `IF(ISBLANK(x), BLANK(), x)` | `x` | A blank in gives a blank out. |
| `IF(a = 1, x, IF(a = 2, y, z))` | `SWITCH(TRUE(), a = 1, x, a = 2, y, z)` | A chain of tests on the same expression. |
| `x AND TRUE()` | `x` | A tautology on either side. |
| `x OR FALSE()` | `x` | A tautology on either side. |
| `NOT(NOT(x))` | `x` | Double negation. |
| `a + a` | `VAR _a = a RETURN _a + _a` | The same expression, computed once. |

The last one has a subtlety, so it has the most care: occurrences
inside a `CALCULATE` modifier are left alone, because there the same
text can mean a different thing depending on the filter context, and
hoisting it would change the result.

---

## 5. What it does not do

- It does not write anything. To apply the result, pass the simplified
  expression to `add_measure_with_validation`, or build a plan.
- It does not touch an expression it could not parse.
- It does not claim the rewritten expression is equivalent in the
  mathematical sense.

---

## 6. Acceptance criteria

- [ ] `IF(ISBLANK([A]), 0, [A])` → `COALESCE([A], 0)`.
- [ ] `IF(ISERROR(e), 0, e)` → `IFERROR(e, 0)`, with `e` appearing once.
- [ ] A three-step `IF` chain on the same column → one `SWITCH`.
- [ ] A chain on two different columns is left alone.
- [ ] `SUM(X[Amt]) + CALCULATE(SUM(X[Amt]), T[F] = 1)` is left alone.
- [ ] An expression the parser cannot read comes back unchanged, with
      `success: false` and the reason.
- [ ] Simplifying twice gives the same answer.
- [ ] Nothing is written to disk.
