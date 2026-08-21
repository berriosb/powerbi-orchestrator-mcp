# Workflow: Refactor Medidas → Calc Groups con Reconciliación

> Flujo end-to-end #2. Refactoriza N medidas candidatas a un Calculation Group,
> valida que los totales no cambien respecto a las medidas originales,
> regenera documentación y ajusta visuales afectados.

**Status:** v0.1 (spec)
**Prioridad:** P1 — feature v2 (`refactor_to_calculation_groups` completo)
**Responsable:** codehak
**Depende de:**
- [`../01-orchestrator.md`](../01-orchestrator.md) — orquestación
- [`../03-validation.md`](../03-validation.md) — DAX linter + regression
- [`../tools/safe-rename.md`](../tools/safe-rename.md) — para renombrar medidas borradas
- [`../tools/audit-model-and-report.md`](../tools/audit-model-and-report.md)

---

## 1. Objetivo

Migrar un patrón de medidas (típicamente time-intelligence: YTD, QTD, MTD,
SPLY, YTDLY, etc.) a un Calculation Group, validando que **cada celda** de
cada visualización devuelve el mismo valor antes y después.

**Caso de uso real:**

> Tengo 8 medidas sobre `Total Sales`:
> - `Sales YTD`, `Sales QTD`, `Sales MTD`
> - `Sales SPLY` (same period last year)
> - `Sales YTD LY`, `Sales QTD LY`, `Sales MTD LY`
> - `Sales YoY %`
>
> Refactórizalas en un Calculation Group llamado `Time Intelligence` con
> items: YTD, QTD, MTD, SPLY, YTD LY, QTD LY, MTD LY, YoY %.
> Valida que cada valor en cada combinación de dimensión no cambie.
> Regenera la data dictionary y ajusta los visuales que las referencian.

---

## 2. Tools del MCP utilizadas

| # | Tool | Propósito |
|---|------|-----------|
| 1 | `connect_target` | Abrir PBIP. |
| 2 | `plan_change` | Planificar refactor. |
| 3 | `apply_plan` | Ejecutar con reconciliación + rollback. |
| 4 | `add_measure_with_validation` | Crear el calc group placeholder (intermediate step). |
| 5 | `safe_rename` | Renombrar/borrar medidas originales; actualizar visuales. |
| 6 | `run_dax_regression` | Reconciliación total (queries paralelas). |
| 7 | `audit_model_and_report` | Audit post-refactor. |
| 8 | `generate_data_dictionary` | Doc regenerada. |

---

## 3. Pasos internos

### Fase 1 · Análisis de impacto

```
[Agente]
"Refactórizame esas 8 medidas en un calc group 'Time Intelligence'."

[Capa 6]
plan_change(template="refactor_to_calculation_groups",
            args={pattern:"time_intelligence",
                  candidate_measures: [8 nombres],
                  target_table: "Sales",
                  reconciliation_tolerance: "pct:0.001"})

plan_change → impact_analysis:
  ├─ modeling.list_measures(filter_by_name=candidates) → 8 medidas confirmadas
  ├─ modeling.list_visuals_using(measure_names) → 23 visuales afectados
  ├─ modeling.list_dependent_measures(measure_names) → 2 medidas (que usan estas)
  ├─ risk_score: 0.65 (alto, >0.5 → elicitar)
  └─ elicitation: "Refactor afectar\u00e1 8 measures + 23 visuals + 2 dependent measures.
                    Reconciliación con tolerancia 0.1%. ¿Continuar?"
    └─ user: "sí"
```

### Fase 2 · Crear Calculation Group

```
apply_plan (paso 1: crear calc group)
  ├─ step 1: create_table(Sales, ... existing table) [verify exists]
  ├─ step 2: create_calculation_group(Time Intelligence, table=Sales)
  ├─ step 3: add_calculation_items([
  │    {name: "YTD",     expression: "TOTALYTD([Total Sales], 'Date'[Date])"},
  │    {name: "QTD",     expression: "TOTALQTD([Total Sales], 'Date'[Date])"},
  │    {name: "MTD",     expression: "TOTALMTD([Total Sales], 'Date'[Date])"},
  │    {name: "SPLY",    expression: "CALCULATE([Total Sales], SAMEPERIODLASTYEAR('Date'[Date]))"},
  │    {name: "YTD LY",  expression: "..."},
  │    {name: "QTD LY",  expression: "..."},
  │    {name: "MTD LY",  expression: "..."},
  │    {name: "YoY %",   expression: "DIVIDE([Total Sales] - [SPLY], [SPLY])"}
  │  ])
  ├─ validator: BPA + dax_lint → 2 warnings (nested CALCULATE) → info-level, OK
  └─ snapshot: pre-refactor
```

### Fase 3 · Reconciliación TOTAL (corazón del workflow)

```
apply_plan (paso 2: reconciliación)

Estrategia:
  Para cada combinación de dimensión relevante:
    - Evaluar measure original con filter dim=X, date=Y
    - Evaluar calc group item equivalente con el mismo filter
    - Comparar valores (tolerance 0.001%)

  ├─ step 1: identificar dimensiones sample (ej: Region, Product, Date.Year, Date.Month)
  │  - cardinality: Region=5, Product=50, Year=3, Month=12 → 5*50*3*12 = 9000 combos
  │  - sample: 1000 combos random (configurable, default 1000)
  │
  ├─ step 2: para cada combo en sample:
  │  ├─ query_original = EVALUATE ROW("x", [Sales YTD]) FILTER('Region'=X, 'Date'[Year]=Y, 'Date'[Month]=M)
  │  ├─ query_refactored = EVALUATE ROW("x", CALCULATE([Total Sales], 'Time Intelligence'[Time Calc]="YTD")) FILTER(...)
  │  ├─ comparar valores
  │  └─ si drift > tolerance → rollback INMEDIATO
  │
  ├─ step 3: ejecutar 1000 queries * 8 medidas = 8000 queries (asyncio.gather, batch 50)
  │  └─ duration: ~120s
  │
  ├─ step 4: aggregate results
  │  - matches: 7992 / 8000
  │  - drifts: 8 (todos <0.001%, aceptable)
  │  - failures: 0
  │
  └─ result: passed (con drift menor a tolerancia)
```

### Fase 4 · Migrar visuales

```
apply_plan (paso 3: propagar a visuales)

Estrategia: para cada visual que usaba [Sales YTD], cambiar a
  CALCULATE([Total Sales], 'Time Intelligence'[Time Calc]="YTD")
  y agregar el calc group column al slicer/eje.

  ├─ step 1: list_visuals_using_measures(['Sales YTD', ...]) → 23 visuales
  │
  ├─ step 2: para cada visual:
  │  ├─ backup visual.json (rollback handle)
  │  ├─ reemplazar Column reference [Sales YTD] por [Total Sales] (calculado)
  │  ├─ agregar 'Time Intelligence'[Time Calc] como filter/eje
  │  ├─ validar PBIR
  │  └─ si falla → restore backup + continuar con siguiente visual
  │
  ├─ step 3: audit post-cambio
  │  - visuales migrados: 23/23
  │  - visuales con error: 0
  │
  └─ result: 23 visuales migrados
```

### Fase 5 · Borrar medidas originales (opcional, recomendado)

```
apply_plan (paso 4: limpieza)

  ├─ elicitation: "¿Borrar las 8 medidas originales? (recomendado para evitar confusión)"
  │  └─ user: "sí"
  │
  ├─ step 1: list_dependent_measures([8 nombres]) → 2 dependent measures
  │  └─ "Sales Net YTD" usa [Sales YTD] → debe actualizarse
  │
  ├─ step 2: safe_rename dependent measures (manual review)
  │  └─ elicitation con lista exacta
  │
  ├─ step 3: delete_measure × 8
  │
  └─ validator: BPA + dax_lint + dax_regression
```

### Fase 6 · Audit + Doc

```
  ├─ audit_model_and_report(target=pbip, checks=[bpa, dax_lint, accessibility, naming])
  │  - score antes: 87
  │  - score después: 91 (+4 por reducción de measure explosion)
  │
  ├─ generate_data_dictionary(format=markdown, include=[measures, relationships, mermaid])
  │  - path: ./out/sales_model_dict.md
  │  - coverage: 0.95 (mejora vs 0.85 original)
  │
  └─ audit_log entry completo
```

---

## 4. Puntos de validación / rollback

| Step | Validation | Rollback |
|------|-----------|----------|
| Análisis | impacto declarado | N/A |
| Crear calc group | BPA + dax_lint | snapshot pre-step |
| Reconciliación | drift < tolerance | rollback completo del refactor |
| Migrar visuales | pbir_validate | backup visual.json por visual |
| Borrar medidas | BPA + dax_lint | recrear medidas desde baseline |
| Audit | overall score | N/A (read-only) |

---

## 5. Acceptance criteria

- [ ] Reconciliación 0 drifts >tolerance para 1000 sample combos.
- [ ] 23 visuales migrados sin error de render.
- [ ] Medidas borradas no dejan referencias huérfanas.
- [ ] BPA score mejora o se mantiene.
- [ ] Audit post-refactor pasa pre-deploy check.
- [ ] Data dictionary regenerada incluye el calc group.
- [ ] Rollback atómico si reconciliación falla.

## 6. Out of scope (MVP)

- ❌ Refactor automático sin elicitation (`auto=true`).
- ❌ Patrones custom más allá de `time_intelligence`, `shares`, `ranks` (v2).
- ❌ Reconciliación con datos reales vs baseline (solo ejecuta queries).
- ❌ Cross-model refactor (mismo calc group en 2 datasets).

## 7. Riesgos

| Riesgo | Mitigación |
|--------|-----------|
| Reconciliación timeout (>10 min) | Sampling configurable; async con progress. |
| Visual con filter incompatible con calc group | Detección previa + elicitación con opciones. |
| Medida borrada con dependencia no detectada | `list_dependent_measures` obligatorio antes de borrar. |
| Drift > tolerance en producción | Elicitar opciones (rollback, ajustar tolerance, fix manual). |
| Calc group mal nombrado entra en conflicto | Validar unicidad + elicitar rename. |

## 8. Specs relacionados

- [`../01-orchestrator.md`](../01-orchestrator.md) — orquestación + rollback
- [`../03-validation.md`](../03-validation.md) — DAX linter + regression
- [`../tools/safe-rename.md`](../tools/safe-rename.md) — usado en migración
- [`../tools/audit-model-and-report.md`](../tools/audit-model-and-report.md) — usado en audit
