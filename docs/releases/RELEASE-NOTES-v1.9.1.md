# Release Notes — v1.9.1 (2026-09-24)

> **Patch release — documentation consistency.** v1.9.0 was the first
> public release on PyPI, but the README bundled in the v1.9.0 wheel
> still described the package as "pendiente publicación en PyPI".
> v1.9.1 ships the corrected README so that the project page on
> https://pypi.org/project/powerbi-orchestrator-mcp/ matches reality.

**Status:** Beta. No code changes. No API changes. No new tools.
Re-install over v1.9.0 is safe and a no-op at the binary level.

---

## What's changed

### Documentation

- **README.md** — `Estado actual (v1.9.0)` section now reflects ✅
  publication on PyPI instead of marking it as a pending item. Link
  to release notes corrected from `v1.8.0` → `v1.9.0`.
- **docs/MVP-STATUS.md** — "Publicación en PyPI" moved out of the
  optional backlog into a new "Hecho en v1.9.0" section.

### What's NOT in v1.9.1

- No new tools. Same 27 tools as v1.9.0.
- No dependency bumps. Lockfile (`uv.lock`) is unchanged.
- No engine adapter changes.
- No breaking API changes.

---

## Upgrade

```bash
pip install --upgrade powerbi-orchestrator-mcp
# verify
pip show powerbi-orchestrator-mcp | grep Version
# -> Version: 1.9.1
```

For development installs (editable):

```bash
pip install -e ".[dev]"
# version comes from pyproject.toml, now 1.9.1
```

No migration steps required. The orchestrator binary, all tool
schemas, the audit log format, the PlanBuilder API and the SQLite
plan store are byte-for-byte identical to v1.9.0. The only
difference between v1.9.0 and v1.9.1 on disk is the `README.md`
embedded as `long_description` in the wheel.

---

## Files changed

- `pyproject.toml` — bump `version = "1.9.0"` → `"1.9.1"`.
- `README.md` — 2 lines (status bullet + release-notes anchor).
- `docs/MVP-STATUS.md` — moved one backlog item to a new "done" section.
- `CHANGELOG.md` — new entry at the top.
- `RELEASE-NOTES-v1.9.1.md` — this file.

Total diff: 2 source-of-truth lines in the README, plus this release
note and changelog entry. No `src/` changes.

---

## Why a patch release for docs only?

The README on the PyPI project page is the first thing new users
see. Leaving it pointing at "pendiente publicación en PyPI" while
the package is in fact published is exactly the kind of small
inconsistency that erodes trust in a project that markets itself
as a polished orchestrator. The fix is small enough that a
full minor bump would be misleading, so v1.9.1 (patch) it is.

If you prefer, you can also force-republish the v1.9.0 wheel with
`twine upload --force dist/powerbi_orchestrator_mcp-1.9.0-py3-none-any.whl`
to overwrite the existing description without bumping the version,
but that is discouraged by PyPI (it shows up in the release
history as a force-push) and the patch release is cleaner.

---

## Verifying this release

```bash
# from a fresh venv
pip install powerbi-orchestrator-mcp==1.9.1
powerbi-orchestrator-mcp --help

# the PyPI project page should now read "✅ Publicado en PyPI"
# in the Status section, not "⏳ Pendiente: publicación en PyPI".
```

---

## Attribution

Same as v1.9.0. No new third-party components.