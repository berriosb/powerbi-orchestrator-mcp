# Release Notes — v1.7.0 (2026-09-04)

> **Sprint 14 — backlog closed.** Both remaining hardening items from
> the v1.6.0 backlog ship:
>
> 1. **`te` modeling adapter** — concrete TOM adapter + protocol seam
>    in `create_semantic_model_from_schema` and
>    `refactor_to_calculation_groups`.
> 2. **Story variance analysis** — deterministic PNG byte-comparison
>    helper that detects visual regressions between two screenshot
>    directories.
>
> Hardening backlog now empty: **0 items remaining**.

**Status:** Beta. The TE adapter ships with a `mode='skeleton'` default
that returns a synthetic success without spawning a subprocess (real
deployment sets `mode='subprocess'` with a TE binary on PATH). Story
variance is a stand-alone helper; it can be invoked from CI without
any engine dependency.

---

## What's new

### 1. Tabular Editor (TE) modeling adapter — Sprint 14A

`engines/te_adapter.py` provides:

- **`InMemoryModelingAdapter`** — a pure-stdlib `ModelingEngine`
  implementation. Tracks tables, columns, measures, relationships in
  process memory. Implements both the `ModelingEngine` protocol and
  the new `SupportsSpecOps` narrow protocol (`apply_model_spec`,
  `refactor_to_calculation_groups`).
  Use this in tests and in environments without a real TE.

- **`TabularEditorAdapter`** — concrete `JsonRpcSubprocessEngine`
  subclass. Two modes:
  - `mode='skeleton'` (default) — returns the destination path
    without spawning a subprocess. Used when TE is not on PATH.
  - `mode='subprocess'` — real RPC calls to TE's scripting host.
    Wire this in production.

### Wiring into existing tools

- **`create_semantic_model_from_schema`** — when an injected
  `modeling_engine` provides `apply_model_spec`, the tool
  delegates the TMDL write to the engine instead of the
  string-template fallback. Successful path returns the same
  `CreateSemanticModelResult` shape with the engine's
  `changed_files`. Failing engines propagate `success=False` and a
  remediation message via `warnings`.

- **`refactor_to_calculation_groups`** — when `measure_writer`
  exposes `refactor_to_calculation_groups` (a TE adapter), the tool
  delegates the persist step and merges the engine's
  `measure_remappings` into the public result. The legacy
  `write_result = measure_writer(...)` callable path still works
  unchanged for backward compatibility.

### 2. Story variance analysis — Sprint 14B

`validation/story_variance.py`:

- **`compute_story_variance(baseline_dir, current_dir, ...) -> StoryVarianceResult`** —
  walks two directories of PNGs (typically emitted by
  `screenshot_report_pages(..., format='png')`), decodes each via
  the deterministic PNG renderer + stdlib `zlib`, and reports:

  - per-page `status` (`unchanged` / `changed` / `added` / `removed`),
  - `pixel_diff_pct` (RGB difference percentage),
  - `differing_lines` / `total_lines` (rough row-level diff),
  - SHA-256 hashes for both sides (always),
  - `overall_variance_pct` (mean across pages).

  Threshold-driven: a `change_threshold_pct` parameter (default
  `0.1%`) classifies tiny sub-threshold diffs as "unchanged" to
  avoid noisy CI feedback.

- **`variance_to_dict(result)`** — round-trip-friendly JSONable view
  for loggers / MCP wrapper output.

### Tool registration

Unchanged: 26 tools registered. The hardening does not add to the
catalog (the TE adapter is an engine, not a tool; story variance is a
validation helper, not a tool).

### Tests

- **+28 unit tests** (665 total, was 637).
  - **17 tests** for the TE adapter (`TestInMemoryModelingAdapterBasics`,
    `TestInMemoryModelingAdapterSpecOps`,
    `TestCreateSemanticModelDelegatesToEngine`,
    `TestRefactorDelegatesToEngine`,
    `TestTabularEditorAdapterSkeleton`) — covers the in-memory
    adapter's full surface, the delegation path in
    `create_semantic_model_from_schema` and
    `refactor_to_calculation_groups`, the new
    `mode='skeleton' / mode='subprocess'` constructor, engine
    failure handling, the invalid-mode rejection, and the
    legacy-callable backward compat.
  - **11 tests** for story variance (`TestStoryVarianceIdentical`,
    `TestStoryVarianceAddedRemoved`, `TestStoryVarianceContentDiff`,
    `TestStoryVarianceHelpers`, `TestPNGChunkParser`) — covers
    identical dirs, added/removed pages, content-driven diffs,
    threshold classification, the dict round-trip,
    corrupt-PNG warnings, and the chunk parser integration with our
    deterministic PNG renderer.

---

## Acceptance criteria (SPEC §6.5)

All 9 v1.0.0 acceptance criteria remain passing. No new criteria.

---

## Quality metrics

| Metric | v1.5.0 | v1.6.0 | **v1.7.0** |
|--------|--------|--------|--------|
| Test count | 625 | 637 | **665** |
| MCP tools registered | 26 | 26 | **26** (no new; hardening) |
| Source files | 62 | 62 | **64** |
| mypy --strict | clean | clean | clean |
| ruff | clean | clean | clean |
| Hardening backlog | 5 → 2 | 2 | **0** ✅ |

---

## Upgrade guide

```bash
git pull
pip install -e .
pytest tests/                                # 665/665
python scripts/verify_mcp_server.py          # 26/26 tools
```

No breaking API changes. Additive only:

- The `modeling_engine` parameter to
  `create_semantic_model_from_schema` was always present; this
  release makes it useful via the new `apply_model_spec` seam.
- The `measure_writer` parameter to
  `refactor_to_calculation_groups` was always present; this
  release makes it useful via the new
  `refactor_to_calculation_groups` seam.
- `validation.story_variance.compute_story_variance` is a
  brand-new helper; opt-in by importing it.

---

## What's next

With **all hardening backlog items closed**, the natural follow-ups are:

1. **Real-binary integration tests** in CI — exercise the
   TE-binary path against actual `TabularEditor.exe` /
   `powerbi-modeling-mcp` / `dscmd` installations. Requires
   infrastructure work (Windows runners, license boundaries).
2. **Multi-tenant / remote transport** — v4 roadmap item,
   out of current scope.
3. **More granular story variance** — extend the variance helper
   to score per-visual-box rather than per-page, and integrate it
   into `audit_report_ux_and_storytelling` as an opt-in mode.

These are optional; the catalogued feature set is complete.
