# Spec: Tool `audit_model_and_report`

> Auditoría integral en una sola llamada. Combina BPA + naming + DAX lint +
> star-schema + accessibility WCAG + performance budget.

**Status:** v0.1 (spec)
**Prioridad:** P0 — usado en `pre_deploy_check`, `safe_rename`, workflows
**Responsable:** codehak
**Depende de:**
- [`../03-validation.md`](../03-validation.md) — BPA + lint + WCAG + diff
- [`../04-viz-ux.md`](../04-viz-ux.md) — performance budget
- [`../05-engines-adapters.md`](../05-engines-adapters.md) — `te` para BPA

---

## Cambios respecto a v0.0

N/A (spec inicial).

---

## 1. Objetivo

Ejecutar un set configurable de checks sobre un modelo/reporte y devolver
**un score unificado** + **lista priorizada de findings** con
`fix_suggestion` accionable y flag `auto_fixable`.

**Métricas de éxito:**
- p95 <30s para modelo mediano (50 tablas, 200 medidas, 30 visuales).
- Score determinístico: mismo target → mismo score.
- 0 falsos positivos en `secrets_check` (test con fixtures).
- Findings con `fix_suggestion` en ≥80% de los casos.

---

## 2. Checks disponibles (MVP)

| Check | Engine | Descripción | MVP |
|-------|--------|-------------|-----|
| `bpa` | te CLI | Tabular Editor Best Practice Analyzer con ruleset default. | ✅ |
| `naming` | propio | Detecta inconsistencias en naming (snake_case vs CamelCase vs Title Case). | ✅ |
| `star_schema` | propio | Clasifica tablas (fact/dim/date/bridge/disconnected) y detecta snowflake chains, bidirectional filters, M:M problemáticos. | ✅ |
| `unused_objects` | propio | Encuentra columnas y measures no usados en ninguna measure ni visual. | ✅ |
| `orphan_keys` | propio | Detecta fact keys sin matching dimension row (causa del hidden blank row). | ✅ |
| `dax_lint` | propio | Anti-patterns DAX (FILTER-not-ISFILTER, nested CALCULATE, etc.). | ✅ |
| `accessibility` | propio (C4 WCAG) | WCAG AA sobre PBIR (alt text, tab order, contrast, color-only encoding). | ✅ |
| `storytelling` | propio (C5) | Heurístico: jerarquía, densidad, narrativa. | ✅ |
| `performance_budget` | propio (C5) | Estimación de coste visual + medida. | ✅ |
| `trace_fe_se` | dscmd | Trace FE/SE real con server timings. Solo Windows. | ⚠️ best-effort |
| `secrets_check` | propio | Detecta tokens, connection strings, passwords, API keys en TMDL/M/PBIR. | ✅ |

---

## 3. Inputs y outputs

### Input schema

```yaml
tool_name: audit_model_and_report
input_schema:
  type: object
  required: [target]
  properties:
    target:
      oneOf:
        - type: object
          properties:
            type: {const: pbip_path}
            ref: {type: string}
        - type: object
          properties:
            type: {const: pbix_file}
            ref: {type: string}
            password: {type: string, nullable: true}
        - type: object
          properties:
            type: {const: workspace}
            workspace_id: {type: string}
            dataset_id: {type: string, nullable: true}
            report_id: {type: string, nullable: true}
    checks:
      type: array
      items:
        type: string
        enum: [bpa, naming, star_schema, unused_objects, orphan_keys, dax_lint, accessibility, storytelling, performance_budget, trace_fe_se, secrets_check]
      default: [bpa, naming, dax_lint, accessibility, secrets_check]
    wcag_level:
      type: string
      enum: [A, AA, AAA]
      default: AA
    bpa_ruleset:
      type: string
      enum: [default, performance, governance, custom]
      default: default
    custom_bpa_ruleset_path:
      type: string
      nullable: true
    auto_fix:
      type: boolean
      default: false
      description: "Aplica fixes automáticos a findings auto_fixable (con elicitation previa)"
    output_format:
      type: string
      enum: [json, markdown, html]
      default: json
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
          fix_suggestion: {type: string, nullable: true}
          auto_fixable: {type: boolean}
          rule_id: {type: string, nullable: true}
    summary_by_check:
      type: object
      additionalProperties:
        type: object
        properties:
          score: {type: number}
          finding_count: {type: integer}
          auto_fixable_count: {type: integer}
    artifacts:
      type: array
      items:
        type: object
        properties:
          type: {type: string, enum: [markdown, html, json]}
          path: {type: string}
    warnings: {type: array, items: {type: string}}
    duration_ms: {type: integer}
    exit_code: {type: integer, description: "0=pass, 1=blocking findings, 2=execution error"}
```

---

## 4. Cálculo del overall score

```
overall_score = (
  bpa_score * 0.25 +
  dax_lint_score * 0.20 +
  accessibility_score * 0.20 +
  star_schema_score * 0.15 +
  performance_budget_score * 0.10 +
  naming_score * 0.05 +
  secrets_score * 0.05
)
```

Cada sub-score es 0-100:
- **100** = 0 findings error.
- **Decremento** según findings warnings (-0.5 cada uno, max 20).
- **0** = cualquier secrets_check error.

**Pesos justificados:**
- BPA y DAX lint son los más importantes (correctness).
- Accessibility tiene peso alto (cumplimiento WCAG).
- Performance budget tiene peso medio.
- Naming es nice-to-have.

---

## 5. Findings priorizados

```python
def sort_findings(findings: list[Finding]) -> list[Finding]:
    """Sort by severity DESC, then auto_fixable ASC, then location ASC."""
    severity_order = {"error": 3, "warning": 2, "info": 1}
    return sorted(
        findings,
        key=lambda f: (
            -severity_order[f.severity],
            f.auto_fixable,  # False first (more attention)
            f.location,
        ),
    )
```

---

## 6. Auto-fix (con elicitation)

Si `auto_fix=true` y hay findings `auto_fixable=true`:

1. Elicitar: "¿Aplicar X fixes automáticos? Cambios: [...]"
2. Si sí: aplicar via engines (modeling.update_* para BPA fixes;
   direct PBIR edits para WCAG fixes simples).
3. Re-correr audit para confirmar score subió.
4. Devolver `result.auto_fix_summary`.

**Auto-fixable MVP:**

- BPA: agregar description default, formato string en measures nuevas,
  isHidden en columnas técnicas, etc.
- Naming: aplicar naming convention consistente (con elicitation del patrón).
- Accessibility: agregar alt text default ("{visualType} de {measure}"),
  marcar decorativos como tab order -1.

**NO auto-fixable:**

- Cambios de relationships.
- Cambios de tipos de columna.
- Cambios de filter direction.
- StoryTelling findings (requiere redesign).

---

## 7. Acceptance criteria

- [ ] Los 11 checks corren en paralelo cuando son independientes.
- [ ] Overall score determinístico (test fixture → mismo score).
- [ ] `auto_fix` con elicitation previa + audit post-fix.
- [ ] `secrets_check` test con fixtures que tienen tokens (debe detectarlos).
- [ ] `output_format=markdown|html` produce artefacto en disco.
- [ ] Latencia p95 <30s en modelo de 50 tablas / 200 medidas.
- [ ] Si un check falla por engine unavailable, warning (no abort).

## 8. Out of scope (MVP)

- ❌ `storytelling` con análisis de varianza real sobre datos (v3).
- ❌ `trace_fe_se` con dscmd solo cuando Desktop disponible (best-effort).
- ❌ Custom rulesets BPA via UI (solo JSON file en MVP).
- ❌ Distribución de findings a Slack/Teams.
- ❌ Scheduled audits.

## 9. Riesgos

| Riesgo | Mitigación |
|--------|-----------|
| Check lento degrada UX | Sampling configurable; warning si >60s. |
| Auto-fix rompe algo | Snapshot pre-fix + rollback si audit post-fix score baja. |
| Secrets check tiene falsos positivos | Whitelist de patterns conocidos (ej: `localhost` no es secret). |
| Score inconsistente entre runs | Pinned dependencies + determinismo en checks propios (no random). |
| te CLI no disponible en Linux | Fallback a linter simplificado propio; warning claro. |

## 10. Specs relacionados

- [`../01-orchestrator.md`](../01-orchestrator.md) — orchestration
- [`../03-validation.md`](../03-validation.md) — BPA + DAX lint + WCAG
- [`../04-viz-ux.md`](../04-viz-ux.md) — performance budget + storytelling
- [`../05-engines-adapters.md`](../05-engines-adapters.md) — `te` CLI
- [`audit-report-ux-and-storytelling.md`](./audit-report-ux-and-storytelling.md) — complementario (solo reporte)
- [`pre-deploy-check.md`](./pre-deploy-check.md) — usa audit
