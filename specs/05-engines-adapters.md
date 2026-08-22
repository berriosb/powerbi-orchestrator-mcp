# Spec: Engines Adapters (Capas 1 y 2)

> Cómo `powerbi-orchestrator-mcp` delega a los motores externos especializados.
> No reimplementamos TOM/PBIR — los orquestamos.

**Status:** v0.1 (spec)
**Prioridad:** P0 — bloqueante para todos los tools que tocan modelo/reporte
**Responsable:** codehak
**Depende de:** [`01-orchestrator.md`](./01-orchestrator.md)
**Habilita:** todas las herramientas que escriben modelo o reporte

**Spec relacionado:** [`docs/architecture.md`](../docs/architecture.md) §2.2 + §2.3

---

## Cambios respecto a v0.0

N/A (spec inicial).

---

## 1. Objetivo

Definir la **interfaz común** y los **adapters concretos** para que el
orquestador delegue a engines especializados de forma robusta, tipada y
versionada. El orquestador no sabe nada de los detalles internos de cada
engine — solo conoce la interfaz.

**Métricas de éxito:**
- Switch entre engines (Windows → Linux fallback) sin cambio de código
  del orquestador.
- Adapter tests con mocks para los 4 engines.
- Versiones pinneadas; upgrade intencional via PR.

---

## 2. Interfaz común

```python
# src/engines/base.py
from typing import Protocol

class ModelingEngine(Protocol):
    """Engine para operaciones de modelo semántico (Capa 1)."""

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    async def health_check(self) -> EngineStatus: ...

    async def connect(
        self, target: Target
    ) -> ConnectionHandle: ...

    async def list_tables(self, conn: ConnectionHandle) -> list[Table]: ...
    async def list_measures(self, conn: ConnectionHandle) -> list[Measure]: ...
    async def list_columns(self, conn: ConnectionHandle, table: str) -> list[Column]: ...
    async def list_relationships(self, conn: ConnectionHandle) -> list[Relationship]: ...

    async def update_column(
        self, conn: ConnectionHandle, table: str, column: str, changes: dict
    ) -> OperationResult: ...
    async def create_measure(
        self, conn: ConnectionHandle, table: str, measure: Measure
    ) -> OperationResult: ...
    async def update_measure(
        self, conn: ConnectionHandle, table: str, measure: str, changes: dict
    ) -> OperationResult: ...
    async def delete_measure(
        self, conn: ConnectionHandle, table: str, measure: str
    ) -> OperationResult: ...

    async def execute_dax(
        self, conn: ConnectionHandle, query: str, effective_identity: dict | None = None
    ) -> DaxResult: ...

    async def snapshot(self, conn: ConnectionHandle, label: str) -> SnapshotHandle: ...
    async def restore_snapshot(self, handle: SnapshotHandle) -> None: ...

class ReportEngine(Protocol):
    """Engine para operaciones de reporte (Capa 2)."""

    @property
    def name(self) -> str: ...

    async def health_check(self) -> EngineStatus: ...

    async def connect(self, pbip_path: Path) -> ConnectionHandle: ...

    async def add_page(
        self, conn: ConnectionHandle, page_name: str, layout: PageLayout | None = None
    ) -> OperationResult: ...

    async def add_visual(
        self, conn: ConnectionHandle, page: str, visual_spec: VisualSpec
    ) -> OperationResult: ...

    async def update_visual(
        self, conn: ConnectionHandle, page: str, visual_id: str, changes: dict
    ) -> OperationResult: ...

    async def propagate_rename(
        self, conn: ConnectionHandle, old_path: str, new_path: str, scope: str
    ) -> OperationResult: ...

    async def validate_pbir(self, conn: ConnectionHandle) -> ValidationResult: ...
```

---

## 3. Adapter: `powerbi-modeling-mcp`

**Engine real:** `@microsoft/powerbi-modeling-mcp` (npx subprocess).
**License:** EULA restrictiva (preview). Solo para uso autorizado.
**OS:** Win/Mac/Linux (Node 20+).
**Versión pinneada:** `0.1.x` al momento del MVP.

### 3.1 Implementación

```python
# src/engines/modeling_mcp.py
class PowerBiModelingMcpEngine(ModelingEngine):
    def __init__(self, binary_path: str = "npx", version: str = "0.1.9"):
        self._binary = binary_path
        self._version = version
        self._process: asyncio.subprocess.Process | None = None

    async def health_check(self) -> EngineStatus:
        """Run `npx -y @microsoft/powerbi-modeling-mcp@VERSION --version`"""
        ...

    async def _send_request(self, method: str, params: dict) -> dict:
        """Send JSON-RPC request via stdio to the subprocess."""
        ...
```

### 3.2 Mapeo método MCP → Model Operation

| ModelingEngine method | MCP tool |
|-----------------------|----------|
| `list_tables` | `database_operations` → list tables |
| `update_column` | `column_operations` → update |
| `create_measure` | `measure_operations` → create |
| `update_measure` | `measure_operations` → update |
| `delete_measure` | `measure_operations` → delete |
| `execute_dax` | `dax_query_operations` → run |
| `snapshot` | `database_operations` → export TMDL to temp dir |
| `restore_snapshot` | `database_operations` → import TMDL |

### 3.3 Connection types

- **PBI Desktop:** `Connect to '[File Name]' in Power BI Desktop` (prompt del agente).
- **Fabric workspace:** `Connect to semantic model '[Name]' in Fabric Workspace '[Name]'` (requiere auth).
- **PBIP:** `Open semantic model from PBIP folder '[Path]'`.

---

## 4. Adapter: `te` (Tabular Editor CLI)

**Engine real:** binario self-contained de Tabular Editor 3.
**License:** TE3 (license key del usuario).
**OS:** Win/Mac/Linux.
**Version pinneada:** última estable.

### 4.1 Comandos usados

```bash
# BPA
te model.pbim --BPA --BpaRules rules.json --BpaOutputFormat JSON

# Diff
te before.bim after.bim --diff

# Test (DAX regression)
te model.bim --Test test.json --TestResultFormat JSON

# Snapshot to TMDL
te model.bim --SerializeTMDL --OutputFolder ./snapshot/

# Deploy
te model.bim -D "Provider=PowerBI;..." -O -C
```

### 4.2 Implementación

```python
# src/engines/te_cli.py
class TabularEditorCliEngine(ModelingEngine):
    def __init__(self, binary_path: str = "te"):
        self._binary = binary_path

    async def run_bpa(
        self,
        model_path: Path,
        ruleset_path: Path | None,
    ) -> BpaResult:
        cmd = [self._binary, str(model_path), "--BPA"]
        if ruleset_path:
            cmd += ["--BpaRules", str(ruleset_path)]
        cmd += ["--BpaOutputFormat", "JSON"]
        result = await self._run(cmd, timeout=60)
        return self._parse_bpa_output(result.stdout)
```

---

## 5. Adapter: `superbi-mcp` (opcional, Windows)

**Engine real:** `powerbi-pbix-mcp` de cyphonica (npm package).
**License:** FSL-1.1-ALv2 (compatible con uso interno, no comercial hosting).
**OS:** **Windows only** (Analysis Services + Desktop Bridge dependencies).
**Versión pinneada:** última estable.

### 5.1 Capabilities

- 490 tools (TOM + PBIR + Power Query M + RLS execution + DAX lint).
- Direct `.pbix` editing sin Desktop abierto.
- Desktop Bridge: hot-reload + screenshots.
- Propagating renames (model + report + M).

### 5.2 Implementación

```python
# src/engines/superbi.py
class SuperBiMcpEngine(ModelingEngine, ReportEngine):
    """Engine combinado: modela + reporta via Super BI MCP."""

    def __init__(self, version: str = "latest"):
        self._version = version

    async def health_check(self) -> EngineStatus:
        """Solo disponible en Windows; en otros OS reporta unavailable."""
        if sys.platform != "win32":
            return EngineStatus(available=False, reason="Windows only")
        ...
```

### 5.3 Fallback chain

```
[Super BI available] → usar SuperBiMcpEngine
[Super BI unavailable + Desktop] → usar PowerBiModelingMcpEngine + Desktop Bridge
[Linux + PBIP] → usar PowerBiModelingMcpEngine + skill-for-fabric
[Linux + .pbix] → solo lectura (no edición dinámica)
```

---

## 6. Adapter: `dscmd` (DAX Studio CLI, Windows only)

**Engine real:** `dscmd.exe` (DAX Studio 3.1+).
**OS:** Windows only.
**Use:** trace FE/SE real, server timings, cache hits.

### 6.1 Comandos usados

```bash
# Server timings trace
dscmd.exe -s server-name -d db-name -f trace.json -q "EVALUATE ..."

# Query plan
dscmd.exe -d db-name -q "EVALUATE ..." --QueryPlan
```

### 6.2 Uso en MVP

Solo invocado cuando `audit_model_and_report` incluye `check="trace_fe_se"`.
Por defecto OFF (es lento y requiere Desktop).

---

## 7. Adapter: `pbip-validator` (Microsoft, cross-platform)

**Engine real:** CLI Python de Microsoft (skill `skills-for-fabric/powerbi-report-authoring`).
**OS:** Cross-platform.
**Use:** validación offline de PBIR después de cada write.

### 7.1 Comandos

```bash
# Validar un PBIP completo
pbip-validator validate ./out/report.pbip/Report/

# Previsualizar
pbip-validator preview-pages ./out/report.pbip/Report/
pbip-validator preview-visuals ./out/report.pbip/Report/
```

### 7.2 Uso

Siempre invocado **después** de cualquier write a PBIR (paso de validación
obligatorio en el rollback engine).

---

## 8. Selección dinámica de engine

```python
# src/engines/selector.py
class EngineSelector:
    def __init__(self):
        self._engines: list[ModelingEngine | ReportEngine] = [
            SuperBiMcpEngine(),
            PowerBiModelingMcpEngine(),
            TabularEditorCliEngine(),
        ]

    async def select_modeling(
        self, target: Target, preferred: str | None = None
    ) -> ModelingEngine:
        """Devuelve el primer engine que soporte el target + OS."""
        for engine in self._engines:
            status = await engine.health_check()
            if not status.available:
                continue
            if not await engine.supports_target(target):
                continue
            if preferred and engine.name != preferred:
                continue
            return engine
        raise NoEngineAvailable(target)

    async def select_report(
        self, target: Target
    ) -> ReportEngine:
        ...
```

**Política:**

1. Si el usuario especificó `preferred`, intentar ese primero.
2. Si no: Super BI → powerbi-modeling-mcp → te CLI.
3. Si ninguno soporta el target: elicitar al usuario qué instalar.

---

## 9. Version pinning

```python
# src/engines/versions.py
PINNED_VERSIONS = {
    "powerbi-modeling-mcp": "0.1.9",  # update via PR con testing
    "superbi-mcp": "1.5.0",
    "te": "3.0.0",
    "dscmd": "3.1.0",
    "pbip-validator": "0.3.2",
}
```

**Update policy:**

- Minor/patch updates: PR con changelog + tests pasando.
- Major updates: ADR + manual testing con fixtures antes de merge.

---

## 10. Tests

- **Unit tests:** cada adapter con mock del subprocess.
- **Contract tests:** verificar que el adapter expone la interfaz completa.
- **Integration tests:** con fixtures reales (PBIP de prueba, `.pbix` si
  hay licencia TE3 disponible).
- **CI matrix:** Linux + Mac + Windows (al menos unit tests en los 3).

---

## 11. Acceptance criteria

- [ ] Los 4 adapters implementan la interfaz común.
- [ ] Selector dinámico elige engine correcto según target + OS.
- [ ] Versiones pinneadas; upgrade intencional via PR.
- [ ] Adapter tests pasan con mocks (sin dependencias externas).
- [ ] Al menos 1 integration test con fixture real por adapter.
- [ ] Documentación: qué adapter usar en cada escenario.

## 12. Out of scope (MVP)

- ❌ Custom rulesets via UI (solo JSON file en MVP).
- ❌ Auto-discovery de engines nuevos.
- ❌ Hot-swap de engines mid-session.

## 13. Riesgos

| Riesgo | Mitigación |
|--------|-----------|
| Subprocess colgado | Timeout agresivo (30s default) + cancel + retry UNA vez. |
| Engine binary corrupto | SHA256 verification al instalar; cache local. |
| Version breaking change | Pin version + adapter test suite + ADR por upgrade. |
| Engine no soporta target | Selector reporta claramente + elicitation al usuario. |
| OS-specific paths | Usar `pathlib.Path`; resolver binarios via `which` con fallback paths. |

## 14. Specs relacionados

- [`01-orchestrator.md`](./01-orchestrator.md) — planner que usa estos adapters
- [`02-cloud-fabric.md`](./02-cloud-fabric.md) — engine cloud (REST, no subprocess)
- [`03-validation.md`](./03-validation.md) — usa `te` para BPA
- [`tools/safe-rename.md`](./tools/safe-rename.md) — usa modeling + report engines
