# Spec: E2E Testing Strategy

> Estrategia para cerrar el gap entre los smoke tests via JSON-RPC
> (que solo validan el orquestador contra mocks) y la realidad de
> invocar los engines reales (`te`, `dscmd`, `pbip-validator`) sobre
> un PBIP real. Estado actual: pendiente — el README y `docs/MVP-STATUS.md`
> lo declaran explícitamente como "Pendiente: tests E2E con binaries
> reales".

**Status:** v0.1 (spec)
**Prioridad:** P2 — necesario para v1.0 production-ready, no bloqueante
para v1.9.x betas
**Responsable:** Bastian Berrios
**Depende de:** [`06-engine-error-contracts.md`](../06-engine-error-contracts.md),
[`tests/fixtures/README.md`](../../tests/fixtures/README.md)
**Habilita:** confianza real de que el orquestador funciona end-to-end
con engines instalados

---

## Cambios respecto a v0.0

N/A (spec inicial — el gap ya está documentado en
[`docs/MVP-STATUS.md`](../../docs/MVP-STATUS.md) desde v1.8).

---

## 1. Objetivo

Definir un harness de tests E2E que:

1. Levante cada engine real (`te`, `dscmd`, `pbip-validator`) en
   CI, pinned por versión y SHA256.
2. Ejecute un subset de las 27 tools del orquestador contra el
   fixture PBIP real (no mocks).
3. Detecte drift entre el contrato del orquestador y el contrato
   real del engine (el problema clásico: "el mock dice que pasó,
   pero el binario real falla").
4. NO bloquee PRs por flaky-ness inherente a E2E — corre en nightly.

**Métricas de éxito:**

- Cada release mayor (vX.0.0) tiene al menos 5 escenarios E2E verdes
  en CI matrix completo (Linux + macOS + Windows).
- Una regresión de contrato en `te` o `dscmd` (ej. cambio en exit
  codes) se detecta en ≤24h (frecuencia nightly).
- Tiempo total del job nightly E2E ≤ 15 min en el runner más lento.

---

## 2. Alcance (qué se testea)

### 2.1 Engines a testear

| Engine | Por qué E2E | Plataforma |
|---|---|---|
| `te` (Tabular Editor CLI) | Modeling fallback, BPA runner, model diff | Linux (dotnet tool), macOS, Windows |
| `dscmd` (DAX Studio CLI) | DAX regression runner, query analysis | Windows-only (binario .NET con Win-specific deps). macOS/Linux = skip + marcar como `xfail-platform` |
| `pbip-validator` | Validación pre-deploy | Linux, macOS, Windows |

`powerbi-modeling-mcp` y `superbi-mcp`: **fuera de scope** de este
spec. Son engines remotos (npm) con licencia restrictiva; su E2E
pertence a repos upstream, no a este orquestador.

### 2.2 Tools a testear (subset inicial)

De las 27 tools, priorizar las que **dependen de un engine real**:

- `add_measure_with_validation` (via `te`)
- `audit_model_and_report` (via `te` BPA + `pbip-validator`)
- `apply_plan` (rollback end-to-end via `te`)
- `diff_models` (via `te` model diff)
- `run_dax_regression` (via `dscmd` trace FE/SE)

Tools que NO necesitan E2E real:

- `connect_target` (solo state, ya cubierto por smoke test)
- `plan_change` (solo state, ya cubierto)
- `powerbi_health` (solo introspection)
- `audit_report_ux_and_storytelling` (heurística pura, no engine)
- Cloud tools (`deploy_to_workspace`, `run_refresh`, etc.) — son REST,
  tienen sus propios integration tests con `httpx` mockeado (ver
  [`tests/integration/`](../../tests/integration/)).

### 2.3 Fixture

Reusar el fixture load-bearing definido en
[`tests/fixtures/README.md`](../../tests/fixtures/README.md): 4 tablas,
~15 medidas, 4 visuales, RLS, baseline WCAG, 1 error DAX intencional.
Es el mismo fixture que usan los unit tests; E2E solo agrega la
capa de invocar el binario real sobre él.

---

## 3. Estrategia de hosting de binarios

### 3.1 Opciones evaluadas

| Opción | Pros | Contras |
|---|---|---|
| A. **GitHub release assets del upstream** | Versionado oficial, sin duplicación | Drift silencioso (el upstream puede borrar el asset). Sin pinning fuerte por SHA256. |
| B. **Vendoring en `tests/bin/`** (binarios commiteados) | Reproducibilidad 100%, sin red en CI | Repo pesa ~50MB. Actualizar versión requiere commit explícito. |
| C. **Mirror propio en S3/GCS** con manifest de hashes | Control total, versionable | Costo de infra, mantenimiento del mirror. |
| D. **Cache de actions (`actions/cache`)** | Reduce tiempo | No resuelve el primer download ni el version pinning. |

### 3.2 Decisión recomendada: opción A + verificación con SHA256

- Descargar del release oficial upstream (GitHub releases del
  proyecto del engine).
- Cachear via `actions/cache` con key =
  `engine-${name}-${version}-${sha256}`.
- En cada corrida, comparar el SHA256 descargado con un valor
  hardcoded en este spec; mismatch → fail loudly.

**Trade-off conocido:** el spec debe actualizarse cada vez que se
quiere bumpear la versión del engine (commit a
`specs/qa/e2e-testing-strategy.md` con nuevo SHA256). Es fricción
intencional: forzar revisión humana de qué versión del engine se está
testeando.

### 3.3 Tabla de versiones pinned (estado inicial)

| Engine | Versión | SHA256 (placeholder) | Plataforma |
|---|---|---|---|
| `te` | 2.20.0 (TBD) | TBD — se setea en el primer commit del workflow | Linux/macOS/Windows |
| `dscmd` | 3.0.0 (TBD) | TBD | Windows only |
| `pbip-validator` | latest en PyPI | via `pip install pbip-validator==<v>` con hash check | Linux/macOS/Windows |

(Los valores TBD se llenan en el primer PR de implementación. Se
recomienda empezar con la versión más reciente estable del upstream
al momento de implementar.)

---

## 4. CI integration

### 4.1 Workflow nuevo: `.github/workflows/e2e-nightly.yml`

```yaml
name: e2e-nightly
on:
  schedule:
    - cron: '0 4 * * *'   # 04:00 UTC diario
  workflow_dispatch:        # trigger manual bajo demanda
  pull_request:
    paths:
      - 'src/powerbi_orchestrator_mcp/engines/**'
      - 'tests/fixtures/**'
      - '.github/workflows/e2e-nightly.yml'
```

**Por qué nightly, no on-PR:**

- E2E real con binarios externos tiene flaky-ness inherente
  (network, GPU scheduling en runners Windows, file locks).
- 15 min × 3 OS = ~45 min de CI compute por PR es excesivo.
- Daily es suficiente: drift de contrato se detecta en ≤24h.
- PR-triggered solo cuando cambian archivos del engine adapter o
  del fixture (cambios en `src/.../engines/**` o
  `tests/fixtures/**`) — cuando hay razón para sospechar regresión.

### 4.2 Matriz

```yaml
strategy:
  fail-fast: false
  matrix:
    os: [ubuntu-latest, macos-latest, windows-latest]
    python-version: ['3.12']
    include:
      - os: ubuntu-latest
        engines: ['te', 'pbip-validator']
      - os: macos-latest
        engines: ['te', 'pbip-validator']
      - os: windows-latest
        engines: ['te', 'dscmd', 'pbip-validator']
    exclude:
      - os: macos-latest
        python-version: '3.11'
```

### 4.3 Steps (resumido)

1. Checkout + setup-python + install deps.
2. Download + cache engines (verificación SHA256).
3. Install `powerbi-orchestrator-mcp` en editable mode.
4. Run `pytest tests/e2e/ -v --tb=short --junitxml=e2e-results.xml`.
5. Upload `e2e-results.xml` como artifact.
6. Si falla: crear issue con template automático via
   `peter-evans/create-issue-from-file`.

---

## 5. Estructura de tests E2E

```
tests/
├── e2e/
│   ├── __init__.py
│   ├── conftest.py              # fixtures: engine paths, fixture PBIP
│   ├── test_te_modeling.py      # add_measure, diff_models via te
│   ├── test_te_bpa.py           # audit_model_and_report via te BPA
│   ├── test_pbip_validator.py   # audit + pre_deploy_check
│   ├── test_dscmd_regression.py # run_dax_regression via dscmd (Windows only)
│   └── test_apply_plan_rollback.py  # end-to-end apply + rollback
├── fixtures/
│   ├── README.md                # spec del fixture load-bearing
│   └── load_bearing_pip/         # el PBIP versionado
└── ...
```

`conftest.py` provee:
- `engine_te_path`: path al binario, skip si no se puede descargar.
- `engine_dscmd_path`: idem, skip con reason si OS != Windows.
- `fixture_pbip_dir`: copia temporal del fixture load-bearing
  (cada test corre sobre una copia fresca para evitar estado
  compartido).

---

## 6. Primer test scenario concreto (referencia)

```python
# tests/e2e/test_te_modeling.py
def test_add_measure_with_te_validates_dax(temp_pbip, engine_te_path):
    """add_measure_with_validation debe invocar te y propagar el error de BPA."""
    from powerbi_orchestrator_mcp.tools.add_measure_with_validation import (
        add_measure_with_validation,
    )

    # Act: agregar una medida con error DAX intencional (CALCULATE sin contexto)
    result = await add_measure_with_validation(
        pbip_dir=temp_pbip,
        table="Sales",
        measure_name="TestMeasure_BadDAX",
        dax_expression="CALCULATE([Sales Amount])",  # sin filter context
    )

    # Assert: te BPA detecta el error, orquestador propaga con código canónico
    assert result.success is False
    assert result.error_code == "engine_validation_error"
    assert "filter context" in result.remediation_hint.lower()
    assert result.engine == "te"
```

El fixture ya tiene 1 error DAX intencional documentado en
`tests/fixtures/README.md` — este test verifica que el orquestador
lo detecta, no que lo inventa.

---

## 7. Acceptance criteria

- [ ] `.github/workflows/e2e-nightly.yml` creado.
- [ ] `tests/e2e/` con al menos 5 escenarios (3 te + 1 dscmd + 1
      pbip-validator) corriendo verde.
- [ ] Tabla §3.3 con SHA256 reales (no TBD).
- [ ] `actions/cache` configurado con key derivada de SHA256.
- [ ] Issue automático creado en repo al fallar nightly.
- [ ] `docs/MVP-STATUS.md` marca "Tests E2E con binaries reales" como
      ✅ al cerrar este spec.
- [ ] Tiempo total nightly ≤ 15 min en runner Ubuntu (target: 10 min).

---

## 8. Out of scope (MVP de E2E)

- ❌ Testear engines remotos (`powerbi-modeling-mcp`, `superbi-mcp`)
  — pertenecen al upstream.
- ❌ E2E contra Fabric / Power BI Service real — eso son integration
  tests que requieren credenciales y viven en otro workflow.
- ❌ Performance benchmarks (queries por segundo, latencia P95). Esto
  sería un harness separado, posiblemente `locust` o `k6`.
- ❌ Property-based testing (Hypothesis) sobre inputs DAX —
  interesante pero otro spec.

---

## 9. Riesgos

| Riesgo | Mitigación |
|---|---|
| Upstream borra el asset del release | Mirror en S3 personal + alerta via RSS. (Cubierto si §3.2 opción C se elige en el futuro). |
| Runner Windows tiene file locks persistentes entre tests | Cada test corre sobre copia fresca del fixture (`tmp_path` fixture de pytest). |
| `te` cambia exit codes → spec [`06-engine-error-contracts.md`](../06-engine-error-contracts.md) queda desactualizado | Nightly falla → alerta → spec actualizado. Tratar como breaking change: bumpear major del orquestador. |
| SHA256 hardcoded queda stale | Bot Dependabot para specs/** (no solo código). O workflow que compara con upstream y abre PR. |
| Nightly consume demasiada quota de GitHub Actions (~45min/día) | Limitar a OS=ubuntu en schedule; macOS/Windows solo on-PR-trigger (cambios en engines/**). |

---

## 10. Specs relacionados

- [`06-engine-error-contracts.md`](../06-engine-error-contracts.md) —
  los E2E tests validan que los adapters respetan este contrato.
- [`tests/fixtures/README.md`](../../tests/fixtures/README.md) — el
  fixture load-bearing que los E2E usan como input.
- [`specs/release/supersede-policy.md`](../release/supersede-policy.md) —
  si E2E detecta un bug post-release, este define el camino de yank.
- [`docs/MVP-STATUS.md` §"Hecho en vX.Y.Z"](../../docs/MVP-STATUS.md) —
  destino del ✅ al cerrar este spec.