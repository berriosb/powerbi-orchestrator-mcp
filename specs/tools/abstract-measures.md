# Tool: `abstract_measures` (v1.21)

> Find the logic that is written **more than once** in a semantic model,
> and say where exactly it differs.

**Status:** v1.21
**Layer:** 1 (Modeling) + 4 (Validation)
**MVP scope:** Analysis only. It does not write anything, and it does
not apply the abstraction.

---

## 1. Inputs

| Name | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `pbip_path` | string | ✅ | — | Root of the .pbip directory. |
| `table` | string | ❌ | `None` | Only look at the measures of this table. |
| `min_group_size` | int | ❌ | `2` | How many measures have to share a shape. Two is the minimum. |

---

## 2. Outputs

| Name | Type | Notes |
|------|------|-------|
| `success` | bool | |
| `measures_examined` | int | How many measures were looked at. |
| `measures_skipped` | int | How many could not be parsed. |
| `skipped` | dict | Measure name → the reason it could not be parsed. |
| `groups` | list[dict] | See below. |
| `group_count` | int | |
| `error_message` | string | |

Each group has:

| Name | Type | Notes |
|------|------|-------|
| `kind` | string | `identical` or `same_shape`. |
| `measures` | list[string] | The measures that share it. |
| `measure_count` | int | |
| `expression` | string | The shared expression, for the `identical` case. |
| `skeleton` | string | The shape, with literals and columns as placeholders. |
| `slots` | list[dict] | Each place where the measures differ: `index`, `kind`, `values`, `differs`. |
| `differing_slot_count` | int | How many of the slots actually differ. |
| `tables` | list[string] | The tables the measures belong to. |
| `suggestion` | string | What the duplication looks like, in one sentence. |

---

## 3. Workflow

1. Read the measures from the project.
2. Parse each one. A measure that cannot be parsed is **counted, not
   dropped** — so a caller can tell the difference between "no
   duplication" and "we could not look".
3. Replace the literals and the column references with placeholders.
   Two expressions with the same shape differ only in what was
   replaced.
4. Group the measures by shape, and report each group with the slot
   by slot difference.

---

## 4. The two kinds of duplication

**`identical`** — the expressions are the same. One measure can serve
both, and the references to the others have to be repointed.

**`same_shape`** — the expressions differ only in their literals or
their columns. This is the case a calculation group usually answers,
and the tool says so in `suggestion`.

---

## 5. What it does not do

It does not apply the abstraction, and that is not a limitation to
apologise for. **A Tabular measure cannot take a parameter**, so "one
measure with an argument" is not something that can be written down as
a DAX expression. The answer is a calculation group, a helper table,
or a rewrite of the measures in place — and which one is right depends
on the model.

The tool reports the duplication and the shape of the answer, and
leaves that choice to the caller.

---

## 6. Acceptance criteria

- [ ] Two measures with the same expression are an `identical` group.
- [ ] Two measures that differ only in a literal are a `same_shape`
      group, and the differing slot is reported with the value each
      measure has in it.
- [ ] The slots the measures agree on are not reported as differences.
- [ ] A measure that cannot be parsed is counted and named in
      `skipped`.
- [ ] `min_group_size` below two is refused.
- [ ] Nothing is written to disk.
