# Release Notes — v1.6.0 (2026-09-04)

> **Sprint 13 hardening sprint closed.** Three pieces of post-v3
> hardening ship: real 3-way merge in `sync_git_to_workspace`,
> Dataflow Gen2 support in `commit_workspace_to_git`, and a
> pure-stdlib PNG renderer for `screenshot_report_pages`. No new
> tools; the catalog remains 27 (26 tools + safe_rename via the
> `plan_change` template).

**Status:** Beta. Same caveats as prior releases. The PNG renderer is a
real bitmap (openable in any image viewer / CI artifact store), but it
is a wireframe, not a true visual rendering of the report — wire
`superbi-mcp` on Windows for that.

---

## What's new

### Sprint 13 hardening (v1.6.0)

#### 1. `sync_git_to_workspace` — `auto_merge` mode

A new `conflict_resolution="auto_merge"` mode runs a 3-way merge using
`difflib.SequenceMatcher`. The merge takes:

- `base` — the previous common ancestor (from the workspace item's
  ``base`` field; falls back to empty string when missing),
- `ours` — the live workspace state,
- `theirs` — the local PBIP file.

Outputs fall into one of three buckets:

- **`clean`** — only one side changed; result is the changer's content.
- **`merged`** — both sides changed but hunks don't overlap; their
  hunks are spliced into the ours base.
- **`conflict`** — hunks overlap on the workspace ↔ PBIP boundary;
  the item is added to `items_skipped` for manual review.

This unblocks bulk workspace syncs that previously required manual
intervention for every conflicting item.

#### 2. `commit_workspace_to_git` — Dataflow Gen2 support

Items with ``type`` of `DataflowGen2` or `DataflowGen2Item` are now
written to `DataflowGen2/<name>.pbip` instead of
`DataflowGen2/<type>/<name>.pbip`. The dual type-id handling covers
both naming variants Fabric reports. Classic Dataflows (Gen1) remain
in the `Dataflow/` subtree as before. Regression coverage in
`tests/unit/test_sprint12_git.py::TestCommitWorkspaceGen2Support`.

#### 3. `screenshot_report_pages` — pure-stdlib PNG renderer

When ``format="png"``, the tool now emits a real PNG file (per-page
wireframe with the visual-type color palette) using a custom
100-LOC stdlib-only encoder (`struct.pack` + `zlib` for IDAT
compression; manual PNG chunk construction; CRC32 via `zlib.crc32`).
The output passes the standard PNG signature check and contains valid
IHDR, IDAT, and IEND chunks.

Capabilities:

- Color-tagging per visual type (card, kpi, line, bar, pie, donut,
  scatter, table).
- Deterministic output: same PBIP + same viewport → same PNG bytes.
- Dimension-encoded in IHDR; verifiable by reading the first 16 bytes
  of the file.

What it is **not**:

- No text rendering (no fonts in stdlib).
- No anti-aliasing.
- No real bitmap of the visual content.

These limitations stay the call for `superbi-mcp` on Windows. The
tool's `rendering_warnings` are updated to reflect the new behavior.

### Tool registration

Unchanged: 26 tools registered (same as v1.5.0). The hardening does
not add to the catalog.

### Tests

- **+12 unit tests** (637 total, was 625).
  - **4 tests** for `sync_git_to_workspace` auto-merge (`TestSyncGitAutoMerge`):
    clean-merge when only local changed, conflicting hunks, non-
    overlapping auto-merge success, and `_three_way_merge` unit
    cases covering the 4 paths (equal / only ours / only theirs /
    both changed / non-overlapping).
  - **3 tests** for `commit_workspace_to_git` Dataflow Gen2
    (`TestCommitWorkspaceGen2Support`): canonical `DataflowGen2`
    type writes to `DataflowGen2/` dir, `DataflowGen2Item` alias
    is recognized, classic `Dataflow` (Gen1) keeps its own dir.
  - **5 tests** for `screenshot_report_pages` PNG rendering
    (`TestScreenshotPngRendering`): png file produced + signature
    check, multiple visuals produce valid IHDR width, manifest
    records format, SVG still produced for `format="pdf"`, direct
    call to `_render_png` produces valid bytes.

---

## Acceptance criteria (SPEC §6.5)

All 9 v1.0.0 acceptance criteria remain passing. No new criteria added
in v1.6.0.

---

## Quality metrics

| Metric | v1.4.0 | v1.5.0 | **v1.6.0** |
|--------|--------|--------|--------|
| Test count | 584 | 625 | **637** |
| MCP tools registered | 23 | 26 | **26** (no new; hardening) |
| Source files | 59 | 62 | **62** |
| mypy --strict | clean | clean | clean |
| ruff | clean | clean | clean |
| Capa coverage (weighted) | ~88% | ~95% | **~96%** |
| Hardening backlog | 5 items | 5 items | **2 items** (TE adapter, story variance) |

---

## Upgrade guide

```bash
git pull
pip install -e .
pytest tests/                                # 637/637
python scripts/verify_mcp_server.py          # 26/26 tools, exit 0
```

No breaking API changes. All hardening is either:

- a new option (e.g. `conflict_resolution="auto_merge"`),
- an expanded item-type catalog (Dataflow Gen2), or
- a new output format (PNG) that didn't exist before.

Existing usages continue to work unchanged.

---

## Hardening backlog (post-Sprint 13)

Two items remain in the optional backlog:

1. **`te` modeling adapter** — replace the deterministic string-
   template TMDL renderer in `create_semantic_model_from_schema`
   (and the regex-based skeleton detector in
   `refactor_to_calculation_groups`) with real TOM/TE manipulation
   via Tabular Editor's CLI.
2. **Story variance analysis** in
   `audit_report_ux_and_storytelling` — requires real rendering
   telemetry (currently heuristic-only).

Both ship on an as-needed basis per adoption feedback.

---

## Roadmap

The tool catalog for SPEC §6 is **complete at 27 tools** (26 MCP +
safe_rename via the `plan_change` template). Future sprints focus on:

- Real-binary integration tests (need `te` / `powerbi-modeling-mcp` /
  `dscmd` in CI).
- Optional TE/TOM wiring for `refactor_to_calculation_groups` and
  `create_semantic_model_from_schema`.
- Optional multi-tenant / remote transport (v4, far-future).
