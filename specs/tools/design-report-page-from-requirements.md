# Spec: Tool `design_report_page_from_requirements` (v2) — OUTLINE

> Tool flagship del workflow 01: diseña una página completa de reporte
> desde un brief en lenguaje natural + la estructura del dataset, eligiendo
> visuales, layout, theme y aplicando WCAG automáticamente.

**Status:** v0.1 (outline — sem 6-8 de implementación)
**Versión:** v2
**Capa:** 2+5 (Reporte + Viz/UX)

---

## Objetivo

Reemplazar las 6+ llamadas manuales que un agente hace hoy para diseñar
una página (seleccionar visuales, calcular posiciones, configurar
formato, agregar alt text, configurar mobile layout) por **una sola
llamada** que toma un brief NL y devuelve un page.json completo + visual
configs + theme + WCAG aplicado. Es el componente que cierra el workflow
01 de CSV → publicado al 100% automatizable (vs ~70% MVP / ~90% v1.1).

## Inputs principales

- `dataset`: target descriptor (PBIP o workspace ID + dataset ID).
- `brief`: NL describiendo la página. Estructura esperada (no estricto):
  ```
  Diseña una página "<title>" para <audience: executive|analyst|operational>.
  KPIs principales: <lista>.
  Comparar: <dimensiones>.
  Filtrar por: <dimensiones>.
  Layout: <mobile-first|desktop-first>.
  Estilo: <formal|informal|brand-color>.
  ```
- `audience`: opcional si viene en brief. Default: inferir.
- `max_visuals`: default 6 (above this, warning + elicitación).
- `theme`: nombre de paleta o `auto` (colorblind-safe default).
- `mobile_first`: bool, default true.
- `dry_run`: bool, default true (devolver page.json propuesto sin escribir).

## Outputs principales

- `page_json`: estructura PBIR completa de la página.
- `visuals`: lista de visuales con sus configs detalladas (type, fields, format, position).
- `theme_applied`: theme.json resultante.
- `wcag_score`: 0-100 con detalle de findings.
- `narrative_score`: 0-100 del StorytellingScorer heurístico.
- `alt_text_generated`: lista de alt text por visual.
- `rollback_handle`: snapshot pre-cambio.
- `risk_score`: 0.0-1.0.

## Dependencias

- [`../01-orchestrator.md`](../01-orchestrator.md) — `plan_change` + `apply_plan`.
- [`../04-viz-ux.md`](../04-viz-ux.md) — todos los módulos de Capa 5:
  `VisualRegistry`, `VisualSuggester`, `LayoutOptimizer`, `ThemeGenerator`,
  `StorytellingScorer`, `PerformanceBudget`.
- [`../03-validation.md`](../03-validation.md) — WCAG auditor + lint.
- [`../tools/create-report-from-dataset.md`](../create-report-from-dataset.md) — scaffolding base.
- [`../tools/apply-theme-and-accessibility-rules.md`](../apply-theme-and-accessibility-rules.md) — theme/WCAG final.

## Acceptance criteria

- [ ] Brief NL mínimo ("KPI page for sales execs, YTD, MoM%, top 10 products") produce page.json válido (pbip-validator pasa).
- [ ] Layout mobile-first genera `mobileLayout` con stacking vertical y gaps correctos.
- [ ] WCAG score ≥85 por default (colorblind-safe palette + alt text auto + tab order lógico).
- [ ] Visual count respeta `max_visuals` (elicita si brief sugiere más).
- [ ] Cada visual tiene alt text generado (no vacío).
- [ ] Performance budget: estimado <5s para render de la página en dataset mediano.
- [ ] Rollback atómico: restore pre-cambio byte-idéntico.

## Riesgos / open questions

- **Parsing NL del brief**: ¿usar LLM local o template matching? Decisión: template matching con fallback a LLM via MCP. En v2: solo template matching para los 5 patrones más comunes (KPI, comparison, trend, distribution, composition).
- **Visuales custom certified**: el registry soporta custom visuals pero el suggester solo recomienda nativos. ¿Custom como alternativa explícita?
- **Internacionalización del alt text**: idioma del alt text sigue `report.json.locale`. ¿Soporte multi-lenguaje explícito? Default: usa el locale del report.
- **StorytellingScorer heurístico** (v2): el score es heurístico; el real con análisis de varianza queda para v3.
- **PerformanceBudget estimado**: solo heurístico, no ejecuta realmente el reporte.

## Fuera de alcance (v2)

- ❌ Multi-página en una sola llamada (una llamada = una página).
- ❌ Cross-page filtering automático (ej. drill-through desde Overview a Detail).
- ❌ Bookmarks / buttons programáticos.
- ❌ Custom visuals no-certified.
- ❌ Generación de medidas DAX nuevas desde el brief (eso es `create_semantic_model_from_schema`).

---

## Spec completo

Detalle (Pydantic models del brief parser, algoritmos de layout, patrones
de templates de página, integración con cada módulo de Capa 5) en
Semana 6-8.
