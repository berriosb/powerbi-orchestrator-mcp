# Spec: Validación y Testing (Capa 4)

> Capa 4 — quality gates. BPA, DAX linter, regression runner, accesibilidad
> WCAG, model diff, pre-deploy gate.

**Status:** v0.1 (spec)
**Prioridad:** P0 — sin validación no hay deploy seguro
**Responsable:** codehak
**Depende de:** [`01-orchestrator.md`](./01-orchestrator.md) (para ejecutarse via tools)
**Habilita:** todos los tools que tocan modelo o reporte
**Spec relacionado:** [`docs/architecture.md`](../docs/architecture.md) §2.5

---

## Cambios respecto a v0.0

N/A (spec inicial).

---

## 1. Objetivo

Proveer quality gates ejecutables desde CI o desde un agente, con resultados
estructurados y accionables (auto-fixable vs requiere acción humana).

**Métricas de éxito:**
- `audit_model_and_report` p95 <30s para modelo mediano (50 tablas, 200 medidas).
- `pre_deploy_check` retorna PASS/FAIL con umbrales claros.
- `run_dax_regression` detecta drift >0.1% con 0 falsos positivos.
- 0 secrets committed en CI (verificable con `pre_deploy_check`).

---

## 2. Componentes

### 2.1 `bpa_runner.py` — Best Practice Analyzer

Wrapper sobre `te bpa` (Tabular Editor CLI) + ruleset custom JSON.

```python
# src/validation/bpa_runner.py
class BpaRunner:
    def __init__(self, ruleset_path: Path | None = None):
        self._ruleset = ruleset_path or Path("default_rules.json")

    async def run(
        self,
        target: Target,  # PBIP o model.json o .pbix
        ruleset_name: str = "default",
    ) -> BpaResult: ...

class BpaResult(BaseModel):
    score: float  # 0-100
    findings: list[BpaFinding]
    ruleset_used: str

class BpaFinding(BaseModel):
    rule_id: str
    rule_name: str
    severity: Literal["error", "warning", "info"]
    object_name: str  # ej: "Sales[TotalAmount]"
    object_type: str  # "measure" | "column" | "table" | "relationship"
    message: str
    auto_fixable: bool
    fix_suggestion: str | None
```

**Rulesets:**

- `default`: 89 reglas de Tabular Editor 3 (oficial).
- `performance`: subset enfocado en performance (sin naming/formatting).
- `governance`: subset para auditoría de org (PII, descriptions, OLS).
- Custom JSON: ver `specs/03-validation.md` §6.

### 2.2 `dax_linter.py` — Linter DAX propio

```python
# src/validation/dax_linter.py
class DaxLinter:
    ANTI_PATTERNS = [
        # (regex/AST pattern, severity, message, rewrite_suggestion_template)
        (
            r"\bFILTER\s*\(\s*'[^\']+'\s*,\s*[^I]",  # FILTER without ISFILTER
            "warning",
            "FILTER sobre tabla completa dentro de CALCULATE; usar KEEPFILTERS o predicados directos",
            "Considere: CALCULATE([Measure], KEEPFILTERS(Table[Column] = ...))",
        ),
        # ... 20+ reglas más
    ]

    def lint(self, expression: str) -> list[DaxLintFinding]: ...
    def lint_batch(self, measures: dict[str, str]) -> dict[str, list[DaxLintFinding]]: ...
```

**Anti-patterns cubiertos (MVP):**

| Pattern | Severity | Rewrite |
|---------|----------|---------|
| `FILTER(Table, ...)` sin `ISFILTER` en `CALCULATE` | warning | KEEPFILTERS o predicado directo |
| Nested `CALCULATE` (3+ niveles) | warning | Extraer a variable con `VAR` |
| `/` en medida | warning | `DIVIDE(numerator, denominator)` |
| `IFERROR(measure, 0)` blanket | info | Ser específico: `IFERROR(measure, BLANK())` |
| `EARLIER()` | info | Usar variables (`VAR row = ...`) |
| `SUMMARIZE` para agregar | warning | `SUMMARIZECOLUMNS` |
| `+ 0` para blanks | info | Usar `+ 0` solo si intencional; preferir `COALESCE` |
| Función desconocida (no en whitelist DAX 2026) | error | "Possible hallucinated function name" |
| `BLANK()` inconsistente | info | Estandarizar |
| `CALCULATE` sin filter argument | warning | Intencional o falta filter |

**Whitelist de funciones DAX** (basado en docs.microsoft.com DAX reference +
SQLBI articles): 250+ funciones. Se valida contra regex `\b[A-Z][a-zA-Z]+\b`.

### 2.3 `dax_regression.py` — Regression runner

```python
# src/validation/dax_regression.py
class DaxRegressionRunner:
    async def run(
        self,
        target: Target,
        baseline: Baseline,  # path o inline
        queries: list[DaxQuery],
        tolerance: Tolerance = Tolerance.EXACT,
    ) -> RegressionResult: ...

class Baseline(BaseModel):
    version: str
    captured_at: datetime
    queries: list[BaselineQuery]

class BaselineQuery(BaseModel):
    name: str
    dax: str
    expected_rows: list[list[Any]]  # serializable

class Tolerance(Enum):
    EXACT = "exact"
    PCT_0_001 = "pct:0.001"
    PCT_0_01 = "pct:0.01"
```

**Storage de baselines:** SQLite table `dax_regression_baselines` con
versionado + commit message + timestamp. Se commitea como JSON a Git.

### 2.4 `model_diff.py` — Diff legible

```python
# src/validation/model_diff.py
class ModelDiffer:
    async def diff(
        self,
        before: Target,
        after: Target,
        scope: list[str] | None = None,
    ) -> ModelDiff: ...

class ModelDiff(BaseModel):
    markdown: str  # diff legible
    breaking_changes: list[Change]
    added: list[Change]
    removed: list[Change]
    modified: list[Change]

class Change(BaseModel):
    object_type: str
    object_path: str  # "Sales[TotalAmount]"
    change_type: str  # "renamed" | "type_changed" | "expression_changed"
    before: Any
    after: Any
    breaking: bool
    affected_visuals: list[str]  # paths a visual.json
```

**Reglas de breaking:**

- Cambio de tipo de columna (int → string) → breaking.
- Borrado de measure/column → breaking (lista medidas que dependían).
- Cambio de cardinalidad en relación → breaking.
- Cambio de filter direction (single → both) → warning.
- Cambio de expression DAX → no-breaking per se (el regression lo detecta).

### 2.5 `accessibility/` — Auditor WCAG

```python
# src/validation/accessibility/auditor.py
class WcagAuditor:
    WCAG_LEVELS = ["A", "AA", "AAA"]  # default AA

    async def audit_pbir(self, pbip_path: Path) -> WcagReport: ...
    def audit_visual(self, visual_json: dict) -> list[WcagFinding]: ...
```

**Reglas WCAG implementadas (MVP):**

| Rule | WCAG ref | Check |
|------|----------|-------|
| Alt text required | 1.1.1 | Cada visual tiene `altText` no vacío |
| Tab order logical | 2.4.3 | `tabOrder` no tiene gaps, orden lógico top-down |
| Decorative hidden | 2.4.4 | Elementos decorativos tienen `tabOrder: -1` |
| Contrast text ≥4.5:1 | 1.4.3 | Color texto vs fondo declarado |
| Contrast large ≥3:1 | 1.4.3 | Texto ≥18pt o bold ≥14pt |
| Color not sole encoding | 1.4.1 | Si hay color semántico, debe haber pattern/shape companion |
| Focus visible | 2.4.7 | (no verificable en JSON, warning general) |
| Markers in lines | 1.4.1 | Line chart con múltiples series tiene `markers` activados |

### 2.6 `pre_deploy_gate.py` — Gate configurable

```yaml
# profiles/standard.yaml
name: standard
checks:
  bpa:
    max_errors: 0
    max_warnings: 10
  dax_lint:
    max_errors: 0
    max_warnings: 5
  accessibility:
    min_score: 70
    max_errors: 0
  naming:
    max_inconsistencies: 3
  performance_budget:
    max_est_load_ms: 5000
  secrets:
    enabled: true  # fail si hay tokens/keys/connection strings
```

**Exit codes para CI:**

- 0: pass
- 1: fail con findings
- 2: error de ejecución (target inválido, engine no disponible)

---

## 3. Tool MVP: `audit_model_and_report`

```yaml
tool_name: audit_model_and_report
input_schema:
  type: object
  required: [target]
  properties:
    target:
      oneOf:
        - type: object
          properties: {type: {const: "pbip_path"}, ref: {type: string}}
        - type: object
          properties:
            type: {const: "workspace"}
            workspace_id: {type: string}
            dataset_id: {type: string}
            report_id: {type: string}
    checks:
      type: array
      items:
        type: string
        enum: [bpa, naming, star_schema, unused_objects, orphan_keys, dax_lint, accessibility, storytelling, performance_budget]
      default: [bpa, naming, dax_lint, accessibility]
    wcag_level: {type: string, enum: [A, AA, AAA], default: AA}
    auto_fix: {type: boolean, default: false}
output_schema:
  type: object
  properties:
    overall_score: {type: number, minimum: 0, maximum: 100}
    findings:
      type: array
      items:
        type: object
        properties:
          check: {type: string}
          severity: {type: string, enum: [error, warning, info]}
          location: {type: string}
          message: {type: string}
          fix_suggestion: {type: string}
          auto_fixable: {type: boolean}
    summary_by_check:
      type: object
      additionalProperties:
        type: object
        properties:
          score: {type: number}
          finding_count: {type: integer}
    warnings: {type: array, items: {type: string}}
errors:
  - target_not_found
  - check_requires_windows  # ej: dscmd-based trace
  - check_unavailable
```

---

## 4. Tool MVP: `pre_deploy_check`

```yaml
tool_name: pre_deploy_check
input_schema:
  type: object
  required: [target]
  properties:
    target: {...}  # mismo que audit
    profile:
      type: string
      enum: [strict, standard, relaxed]
      default: standard
    custom_thresholds:
      type: object
      additionalProperties: true
output_schema:
  type: object
  properties:
    passed: {type: boolean}
    blocking_findings: {type: array, items: {...}}
    warnings: {type: array, items: {...}}
    score: {type: number}
    exit_code: {type: integer, description: "Para CI: 0=pass, 1=fail"}
```

---

## 5. Tool MVP: `run_dax_regression`

```yaml
tool_name: run_dax_regression
input_schema:
  type: object
  required: [target, baseline, queries]
  properties:
    target: {...}
    baseline:
      oneOf:
        - type: object
          properties: {baseline_id: {type: string}}
        - type: object
          properties: {baseline_path: {type: string}}
        - type: object
          properties: {baseline_inline: {...}}
    queries:
      type: array
      items:
        type: object
        properties: {name: {type: string}, dax: {type: string}}
    tolerance: {type: string, enum: [exact, pct:0.001, pct:0.01], default: pct:0.001}
output_schema:
  type: object
  properties:
    passed: {type: boolean}
    regressions:
      type: array
      items:
        type: object
        properties:
          query_name: {type: string}
          mismatched_rows: {type: integer}
          max_drift_pct: {type: number}
          sample_diff: {type: array}
    duration_ms: {type: integer}
```

---

## 6. Custom BPA rulesets

Formato JSON compatible con Tabular Editor BPA:

```json
{
  "name": "acme-governance",
  "rules": [
    {
      "id": "ACME001",
      "name": "PII column requires sensitivity label",
      "severity": "error",
      "objectType": "Column",
      "expression": "Table[Object].IsHidden == false && List.Contains({\"email\", \"ssn\", \"phone\"}, Table[Object].SourceColumn.ToLower())",
      "message": "PII column must have sensitivity label applied"
    }
  ]
}
```

**Ubicación:** `~/.powerbi-orchestrator/bpa-rulesets/` (custom del usuario) +
`src/validation/bpa_rulesets/` (oficiales bundled).

---

## 7. Acceptance criteria

- [ ] `bpa_runner` ejecuta `te bpa` con 3 rulesets (default, performance, governance).
- [ ] `dax_linter` detecta los 10 anti-patterns listados.
- [ ] `dax_linter` detecta función desconocida vs whitelist de 250+.
- [ ] `dax_regression` ejecuta queries paralelas (asyncio.gather).
- [ ] `dax_regression` baseline persiste en SQLite + se puede commitear a Git.
- [ ] `model_diff` clasifica breaking vs non-breaking correctamente.
- [ ] `wcag_auditor` detecta los 8 rules WCAG listados.
- [ ] `wcag_auditor` calcula contraste real cuando hex colors están declarados.
- [ ] `pre_deploy_check` retorna exit_code correcto para CI.
- [ ] `audit_model_and_report` p95 <30s en modelo de 50 tablas / 200 medidas.
- [ ] Score determinístico: mismo target → mismo score.

## 8. Out of scope (MVP)

- ❌ StoryTelling scoring con análisis de varianza real sobre datos (v3).
- ❌ Auto-fix completo de findings WCAG (solo alt text default en MVP).
- ❌ Reglas BPA custom del usuario via UI (solo JSON file en MVP).
- ❌ Storage de baselines en cloud (solo local SQLite).
- ❌ Distribución de findings a Slack/Teams (post MVP).
- ❌ Visual regression testing con screenshots (post MVP).

## 9. Riesgos

| Riesgo | Mitigación |
|--------|-----------|
| `te bpa` no disponible (Linux sin Desktop) | Fallback a linter propio simplificado; warning en output. |
| Whitelist DAX desactualizada | Update mensual desde docs.microsoft.com + SQLBI; CI test contra lista. |
| Baseline grande (>10MB) ocupa disco | Comprimir con zstd; retentar 30 días. |
| WCAG auditor no detecta todo | Documentar que es heurístico; Desktop reload + screenshot sigue siendo source of truth. |
| Falsos positivos en DAX linter | Configurable: por defecto warnings, opt-in a errors. |

## 10. Specs relacionados

- [`01-orchestrator.md`](./01-orchestrator.md) — tools que ejecutan esto
- [`05-engines-adapters.md`](./05-engines-adapters.md) — `te` adapter
- [`tools/audit-model-and-report.md`](./tools/audit-model-and-report.md) — usuario principal
- [`tools/safe-rename.md`](./tools/safe-rename.md) — usa lint DAX inline
