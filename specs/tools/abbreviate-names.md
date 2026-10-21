# Tool: `abbreviate_names` (v1.21)

> Propose **shorter names** for the measures and columns that have
> grown too long to read.

**Status:** v1.21
**Layer:** 1 (Modeling)
**MVP scope:** Analysis only. It does not rename anything.

---

## 1. Inputs

| Name | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `pbip_path` | string | ✅ | — | Root of the .pbip directory. |
| `kind` | string | ❌ | `"both"` | `measures`, `columns` or `both`. |
| `max_length` | int | ❌ | `40` | Above this length, a name is a candidate. Minimum 8. |
| `abbreviate_words` | bool | ❌ | `true` | Shorten the words that have a standard abbreviation. |
| `drop_filler_words` | bool | ❌ | `true` | Drop the words that carry no meaning. |

---

## 2. Outputs

| Name | Type | Notes |
|------|------|-------|
| `success` | bool | |
| `names_examined` | int | |
| `names_with_a_proposal` | int | |
| `names_needing_a_name_by_hand` | int | Names too long to shorten safely. |
| `proposals` | list[dict] | One per name. |
| `rename_map` | dict | `old name → proposed name`. Ready for `plan_change(intent="safe_rename")`. |
| `error_message` | string | |

Each proposal has:

| Name | Type | Notes |
|------|------|-------|
| `name` | string | The name as it is. |
| `kind` | string | `measure` or `column`. |
| `table` | string | The table it belongs to, for a column. |
| `proposed` | string | The shorter name, when one was found. |
| `reason` | string | Why, in one line. |
| `changes` | list[dict] | Each word that changed: `from`, `to`, `rule`. |
| `length_before` / `length_after` | int | |
| `needs_a_name_by_hand` | bool | No safe shortening was found. |
| `collision_resolved` | bool | The proposal was adjusted to avoid a collision. |

---

## 3. Workflow

1. Read the measures and columns from the project.
2. For each name longer than `max_length`, work out a shorter form:
   - a word that has a standard abbreviation is shortened to it
     (`Amount` → `Amt`, `Quantity` → `Qty`, `Revenue` → `Rev`);
   - a filler word that carries no meaning is dropped (`of`, `the`,
     `in`).
3. If the result is still too long, do not propose anything: report
   that the name needs a name by hand.
4. If the proposal would collide with a name that already exists,
   adjust it and say so.

---

## 4. What it refuses to do

**It does not truncate.** A name that cannot be shortened without
changing what it says is reported as needing a name by hand, because
`Total Revenue Amt Incl Tax 2` says less than the original and looks
like a mistake.

**It does not propose a colliding name.** A proposal that collides
with one that already exists is adjusted, and says so.

**It does not rename anything.** The result is a rename map, which is
what `plan_change(intent="safe_rename")` takes — so the rename goes
through the same path as every other rename: a plan, a review, and a
rollback if it goes wrong.

---

## 5. Acceptance criteria

- [ ] A long name gets a proposal, and the proposal says which words
      changed and why.
- [ ] A name within the limit is left alone.
- [ ] A name that cannot be shortened safely is reported as needing a
      name by hand, with no proposal.
- [ ] A proposal that would collide with an existing name is adjusted.
- [ ] Two proposals never collide with each other.
- [ ] `rename_map` is empty when nothing needs renaming.
- [ ] Nothing is written to disk.
