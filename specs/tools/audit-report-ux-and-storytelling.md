# Spec: Tool `audit_report_ux_and_storytelling` (v2) — OUTLINE

> Auditoría cualitativa de un reporte: jerarquía visual, densidad,
> narrativa, mobile-readiness, cohesión con la audiencia objetivo. Es el
> complemento heurístico de `audit_model_and_report` (que cubre BPA/WCAG/lint).

**Status:** v0.1 (outline — sem 6-8 de implementación)
**Versión:** v2
**Capa:** 5 (Viz/UX)

---

## Objetivo

Detectar problemas de UX/storytelling que las auditorías técnicas
(BPA, WCAG, lint DAX) no capturan:
- **Jerarquía visual:** ¿el KPI principal está arriba-izquierda como esperan los ejecutivos?
- **Densidad:** ¿hay visuales con demasiadas categorías hacinadas?
- **Narrativa:** ¿la página cuenta una historia de arriba a abajo, o es una colección inconexa de charts?
- **Mobile-readiness:** ¿qué pasa si abro la página en un celular?
- **Cohesión con audiencia:** ¿el estilo (colores, formalidad, formato de números) matchea la audiencia?

El output es un **score cualitativo 0-100** con findings accionables.

## Inputs principales

- `target`: PBIP/workspace con el reporte.
- `page_name`: opcional (default: auditar todas las páginas).
- `audience_assumed`: opcional (si no, inferir desde títulos de páginas).
- `strictness`: `lenient | standard | strict`. Default `standard`.

## Outputs principales

- `overall_score`: 0-100.
- `category_scores`: dict con `{hierarchy, density, narrative, mobile, cohesion}` cada uno 0-100.
- `findings`: lista de `{category, severity, location, message, suggestion, auto_fixable}`.
- `narrative_score`: 0-100 (sub-score del StorytellingScorer).
- `comparison_to_peers`: opcional (si hay baseline de reportes de la org).

## Dependencias

- [`../01-orchestrator.md`](../01-orchestrator.md) — para ejecutarse en un plan.
- [`../03-validation.md`](../03-validation.md) — comparte WCAG auditor con Capa 4.
- [`../04-viz-ux.md`](../04-viz-ux.md) — `StorytellingScorer` (ya especificado) + `LayoutOptimizer`.
- [`../tools/audit-model-and-report.md`](../audit-model-and-report.md) — input complementario (algunos checks de WCAG ya están en `audit_model_and_report`; este tool los complementa con heurísticas cualitativas).

## Acceptance criteria

- [ ] Detecta "KPI principal no está en top-left" en página executive.
- [ ] Detecta "más de 4 visuales en mobile layout vertical sin gap suficiente".
- [ ] Detecta "pie chart con >7 categorías".
- [ ] Detecta "narrativa rota": orden de visuales no sigue la jerarquía lógica inferida (ej. trend antes del KPI que lo resume).
- [ ] Score es reproducible: mismo report → mismo score.
- [ ] Findings auto-fixable tienen `auto_fixable=true` + `suggestion` accionable.

## Riesgos / open questions

- **Storytelling score heurístico**: en v2 es heurístico (basado en patrones de layout + audit de jerarquía). El real con análisis de varianza sobre los datos renderizados queda para v3.
- **Audience inference**: si el usuario no la especifica, ¿cómo la inferimos? Propuesta: regex sobre títulos de página (ej. "Executive Summary" → executive) + fallback `analyst`. Documentar casos ambiguos.
- **Baseline de la org**: para empresas con muchos reportes, un score relativo (percentil) es más útil que absoluto. Propuesta v3: opcional org-wide baselines.
- **Cross-page narrative**: el spec se enfoca en single-page; cross-page (drill-through flow) es más complejo. Out of scope v2.
- **Custom org-specific heuristics**: ¿plugin system v3 o YAML v2? Propuesta YAML en v2.

## Fuera de alcance (v2)

- ❌ Story telling score con análisis real sobre datos (v3).
- ❌ Cross-page narrative (drill-through flows).
- ❌ A/B testing recommendations (qué layout convierte mejor).
- ❌ Generación de mockups / redesigns automáticos (eso es `design_report_page_from_requirements`).

---

## Spec completo

Detalle (algoritmo del StorytellingScorer heurístico, YAML de
audience heuristics, catálogo de anti-patterns UX) en Semana 6-8.
