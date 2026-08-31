# Spec: Tool `select_visuals_for_kpis` (v2) — OUTLINE

> Recomienda el visual primario + alternativas para un KPI dado,
> considerando el data shape del modelo, la audiencia y las best-practices
> de PBI/SQLBI.

**Status:** v0.1 (outline — sem 6-8 de implementación)
**Versión:** v2
**Capa:** 5 (Viz/UX)

---

## Objetivo

Dado un KPI (medida o measure a crear) + el contexto del modelo +
audiencia, recomendar:
1. **Visual primario** (la mejor opción).
2. **2-3 alternativas** ranked con justificación.
3. **Anti-recomendaciones explícitas** ("no uses pie chart aquí porque
   tienes >7 categorías").

Diferencia con `design_report_page_from_requirements`: este tool es
**atómico y enfocado** (un KPI = una recomendación); el otro diseña la
página completa.

## Inputs principales

- `target`: PBIP/workspace con el modelo.
- `kpi`: descriptor de la medida o measure a crear. Estructura:
  - `measure_name`: opcional (si ya existe en el modelo).
  - `dax_expression`: opcional (si se está creando).
  - `semantic_type`: `single_value | comparison | trend | composition | distribution | correlation`.
  - `cardinality`: estimado del resultado (single, low <10, medium <100, high >100).
- `audience`: `executive | analyst | operational`.
- `context`: opcional — páginas existentes, theme actual, etc.
- `exclude_visuals`: lista de tipos a excluir (ej. la org prohíbe pie charts).

## Outputs principales

- `primary`: `VisualSpec` con `type`, `justification`, `expected_config`.
- `alternatives`: lista de 2-3 `VisualSpec` ranked.
- `anti_recommendations`: lista con `type` + razón.
- `reasoning`: explicación textual de la decisión.
- `examples`: 1-2 referencias de SQLBI / Microsoft docs que soportan la recomendación.

## Dependencias

- [`../01-orchestrator.md`](../01-orchestrator.md) — para ejecutarse dentro de un plan si el agente lo desea.
- [`../04-viz-ux.md`](../04-viz-ux.md) — `VisualRegistry` + `VisualSuggester` ya especificados.
- [`../05-engines-adapters.md`](../05-engines-adapters.md) — `powerbi-modeling-mcp` para introspeccionar el modelo (cardinalidades reales, relationships).

## Acceptance criteria

- [ ] Para KPI con `semantic_type=single_value` + audience=executive → recomienda `card` o `kpi` (no `pieChart`).
- [ ] Para KPI con cardinalidad >100 → NO recomienda `pieChart` ni `donutChart`.
- [ ] Para trend sobre 24+ meses → recomienda `lineChart` sobre `barChart`.
- [ ] Justificaciones referencian el data shape real (cardinalidad medida, no estimada).
- [ ] Anti-recomendaciones son accionables: incluyen qué hacer en su lugar.
- [ ] Output es estable: mismo input → mismo output (sin LLM randomness).

## Riesgos / open questions

- **Best-practices source of truth**: las reglas se hardcodean en `VisualSuggester` o se cargan de un YAML externo actualizable? Decisión propuesta: YAML externo versionado, permite a la org customizar sin fork.
- **Custom visuals certified**: ¿se incluyen en el ranking? Propuesta: como alternativa explícita, no en el top-3 default.
- **LLM-as-judge opcional**: ¿un paso de "explain like I'm 5" sobre la recomendación usando LLM local? Propuesta v3.
- **Performance**: introspeccionar el modelo puede ser lento (>10s para modelos grandes). Sampling opcional.
- **Idioma de las justificaciones**: español / inglés / seguir locale del report.

## Fuera de alcance (v2)

- ❌ Ranking con LLM (la decisión es determinística).
- ❌ Soporte para visual hierarchies complejas (ej. small multiples).
- ❌ Drill-through automático entre visuales.
- ❌ Recomendación de slicers (eso es parte de `design_report_page_from_requirements`).

---

## Spec completo

Detalle (árbol de decisión del suggester, YAML de best-practices,
heurísticas de anti-recommendation) en Semana 6-8.
