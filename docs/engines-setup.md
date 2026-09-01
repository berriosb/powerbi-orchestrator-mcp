# Engines Setup Guide

> How to install each subprocess engine that `powerbi-orchestrator-mcp`
> can delegate to. The orchestrator is **always available** (it's the
> thin Python coordinator); the engines are **optional subprocess
> dependencies** that enable specific tool operations.

**Audience:** developers setting up the orchestrator on a new machine,
> and contributors adding new engine adapters.

**Source:** `specs/05-engines-adapters.md` §2 + §10 (version pinning),
> `specs/06-engine-error-contracts.md` (error/timeout contracts).

---

## 1. Overview

The orchestrator dispatches operations to subprocess engines via the
`EngineSelector` (see `src/engines/selector.py`). Engine availability is
**per-adapter**: a missing engine means the corresponding operation
falls back or elicits a remediation hint — it doesn't crash the server.

| Engine | Layer | OS | Source | MVP |
|--------|-------|----|----|-----|
| `powerbi-modeling-mcp` | Modeling | Win/Mac/Linux | `npx @microsoft/powerbi-modeling-mcp` | Recommended |
| `te` (Tabular Editor CLI) | Modeling + BPA | Win/Mac/Linux | [Tabular Editor releases](https://github.com/TabularEditor/TabularEditor/releases) | Week 2 fallback |
| `dscmd` (DAX Studio CLI) | DAX trace | Windows only | [DAX Studio releases](https://daxstudio.org/) | Week 2 |
| `pbip-validator` | Validation | Win/Mac/Linux | `pip install pbip-validator` (when published) | Week 2 |
| `superbi-mcp` | Report | Windows primarily | `npx superbi-mcp` | Week 2 upgrade |
| `python_report` (built-in) | Report | Win/Mac/Linux | (built into orchestrator) | ✅ always available |

`python_report` is implemented directly in the orchestrator (`src/engines/report_python.py`)
and does NOT need installation — it's the default fallback for report operations.

---

## 2. Detecting what's installed

The `connect_target` tool runs all five binary probes in parallel and
surfaces availability in `engines_available`. You can also run them
manually:

```python
from powerbi_orchestrator_mcp.orchestrator.engine_detector import detect_all_engines
import asyncio

statuses = asyncio.run(detect_all_engines())
for name, s in statuses.items():
    print(f"{name}: available={s.available} reason={s.reason_unavailable}")
```

Expected output on a fully-loaded dev machine:

```
powerbi-modeling-mcp: available=True version=0.1.9
te: available=True version=3.0.0
dscmd: available=False reason=binary not found in PATH...
pbip-validator: available=True version=0.3.2
superbi-mcp: available=False reason=binary not found in PATH...
```

---

## 3. Installing each engine

### 3.1 `powerbi-modeling-mcp` (recommended for MVP)

**What it is:** Microsoft official MCP server for Power BI semantic model
operations (TMDL edits, DAX queries, column/measure CRUD).

**Install:**
```bash
# Requires Node.js 20+. Install via npx (auto-downloads).
npx -y @microsoft/powerbi-modeling-mcp@0.1.9 --version
```

The orchestrator's pinned version is `0.1.9` (see
`src/engines/modeling_mcp.py::DEFAULT_PINNED_VERSION`). To upgrade,
edit the constant and update `docs/MVP-STATUS.md`.

**Verify:**
```bash
npx @microsoft/powerbi-modeling-mcp --version
# Expected: 0.1.9 (or the pinned version)
```

**License:** Microsoft EULA (preview). Commercial use requires EULA
acceptance. The orchestrator does NOT auto-accept EULAs.

### 3.2 `te` (Tabular Editor CLI)

**What it is:** Best Practice Analyzer runner + TMDL editor + VertiPaq
analyzer. Used as modeling fallback and for BPA validation.

**Install:**
- **Windows / macOS:** download from [Tabular Editor releases](https://github.com/TabularEditor/TabularEditor/releases)
- **Linux:** install via `dotnet tool install --global TabularEditor`

**Verify:**
```bash
te2 --version
# Expected: 3.0.0 (or pinned version)
```

**License:** TE3 requires a license for commercial use; TE2 is free.

### 3.3 `dscmd` (DAX Studio CLI)

**What it is:** DAX query trace + server timings + benchmark. Windows
only — Linux/macOS users get a `not found` warning and should skip
DAX-specific tooling.

**Install:** download from [DAX Studio releases](https://daxstudio.org/).

**Verify:**
```bash
dscmd --version
```

### 3.4 `pbip-validator` (validation only)

**What it is:** Microsoft's PBIP pre-flight validator (Python CLI).

**Install** (when published):
```bash
pip install pbip-validator==0.3.2
```

**Verify:**
```bash
pbip-validator --version
```

### 3.5 `superbi-mcp` (Windows-focused, advanced report editing)

**What it is:** Community MCP server with rich report editing capabilities
(`.pbix` direct edits, M transformations, full PBIR + TMDL). License:
**FSL (Functional Source License)** — non-commercial use only.

**Install:**
```bash
# Requires Node.js 20+. Auto-downloads via npx.
npx -y superbi-mcp@1.5.0 --version
```

**Verify:**
```bash
superbi-mcp --version
# Expected: 1.5.0 (or pinned)
```

**License:** FSL — compatible with internal use, NOT for redistribution
or commercial SaaS. See [FUSION LICENSE](https://github.com/...).
Read the FSL terms before using in production.

---

## 4. Configuration via environment variables

Each engine's default timeout can be overridden:

```bash
export PBI_ENGINE_TIMEOUT_TE_S=120          # default 60s, max 300s
export PBI_ENGINE_TIMEOUT_POWERBI_MODELING_MCP_S=60
export PBI_ENGINE_TIMEOUT_DSCMD_S=180
export PBI_ENGINE_TIMEOUT_PBIP_VALIDATOR_S=30
export PBI_ENGINE_TIMEOUT_SUPERBI_S=90
```

The HMAC key for the audit log (auto-generated on first run if unset):

```bash
export PBI_ORCHESTRATOR_AUDIT_SECRET="my-secret-32-bytes-min"
```

Token refresh behavior is handled by `azure-identity` — no config needed.

---

## 5. Verifying the setup

Run the full test suite, which includes engine-detection tests with
mocked `shutil.which`:

```bash
.venv-semana1/bin/python -m pytest tests/ -v
```

Or test just the engine wiring:

```bash
.venv-semana1/bin/python -m pytest tests/unit/test_engines_base.py \
    tests/unit/test_engines_modeling_mcp.py \
    tests/unit/test_engines_report_python.py \
    tests/unit/test_engines_selector.py -v
```

Run an end-to-end smoke test (requires `python_report`, no external deps):

```bash
.venv-semana1/bin/python -m pytest tests/integration/test_safe_rename_e2e.py -v
```

---

## 6. Adding a new engine

To add a new subprocess engine (e.g., `te3-mcp`):

1. **Add a probe** to `src/orchestrator/engine_detector.py`:
   ```python
   EngineProbe(
       engine="te3-mcp",
       binary_names=("te3-mcp",),
       version_args=("--version",),
       npx_package="te3-mcp",
   ),
   ```

2. **Add a timeout** to `src/engines/timeouts.py::DEFAULT_TIMEOUTS`:
   ```python
   "te3-mcp": EngineTimeout(default_s=60, max_s=300),
   ```

3. **Optionally write a dedicated adapter** (`src/engines/te3_mcp.py`)
   if it has unique protocol semantics; otherwise reuse
   `PowerBiModelingMcpEngine` as a template (they're both MCP servers).

4. **Update the selector chain** in `src/engines/selector.py`:
   ```python
   DEFAULT_MODELING_CHAIN = (
       "powerbi-modeling-mcp",
       ("te3-mcp", "te"),  # add your engine in the fallback position
   )
   ```

5. **Wire StepExecutor** in the orchestrator's `step_executor.py` default
   registry (or via plugin registration in `server.py`).

6. **Write tests**: probe detection, adapter dispatch, integration test
   in `tests/integration/` that uses the engine for one operation.

7. **Update `docs/MVP-STATUS.md`** with the new engine and its use case.

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `npx` hangs forever | Node.js version too old | Upgrade to Node 20+ |
| Engine available=True but every op times out | subprocess launches but doesn't respond | Check firewall / sandboxing / antivirus |
| `tablediff` errors in `te` | Wrong TE version | `te2 --version`, check pin in `src/engines/versions.py` |
| `python_report` produces invalid PBIR | Model has edge cases the regex doesn't handle | Add a fixture for your model, file an issue |
| `pbip-validator` exits 2 | Real PBIR corruption (parser error) | Run `pbip-validator pbir ./path` manually for details |
| Connector errors on Linux | Some FABRIC SDK features are Windows-only | Expected; the orchestrator's `engine_warnings` field surfaces this |

---

## 8. Cross-references

- [`specs/05-engines-adapters.md`](../specs/05-engines-adapters.md) — adapter contracts, version pinning, OS×feature matrix
- [`specs/06-engine-error-contracts.md`](../specs/06-engine-error-contracts.md) — error/timeout/exit-code semantics
- [`src/engines/base.py`](../src/powerbi_orchestrator_mcp/engines/base.py) — Protocols + `JsonRpcSubprocessEngine` base
- [`src/engines/selector.py`](../src/powerbi_orchestrator_mcp/engines/selector.py) — `EngineSelector` with graceful degradation
- [`docs/connect-target.md`](./connect-target.md) — entry-point tool (returns `engines_available`)
- [`docs/MVP-STATUS.md`](./MVP-STATUS.md) — current implementation status
