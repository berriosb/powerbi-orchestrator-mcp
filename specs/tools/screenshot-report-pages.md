# Spec: Tool `screenshot_report_pages` (v2/v3) — OUTLINE

> Genera screenshots PNG de las páginas de un reporte para auditoría
> visual, tests de regresión de layout, o anexo a PRs. v2 hace best-effort
> via Desktop Bridge; v3 agrega análisis determinístico con telemetría
> de rendering.

**Status:** v0.1 (outline — sem 6-8 para v2 best-effort, sem 9-12 para v3 determinístico)
**Versión:** v2 (best-effort) + v3 (determinístico)
**Capa:** 2 (Reporte)

> **Nota sobre clasificación (post-audit 2026-08-26):** el spec inicial
> lo marcaba como v3; el fix `3ad85c3` lo reclasificó a v2 para Desktop
> Bridge básico. La versión determinística con análisis de varianza real
> sobre rendering sigue siendo v3. Este outline cubre v2.

---

## Objetivo

Tomar screenshots de las páginas de un reporte `.pbix` o PBIP sin abrir
Power BI Desktop manualmente. Casos de uso:

1. **Auditoría visual:** anexar el PNG al PR que cambia el reporte.
2. **Regression test de layout:** comparar screenshot pre-cambio vs post-cambio, alertar si diff >threshold.
3. **Storytelling audit visual:** los agentes pueden "ver" lo que el
   usuario vería y razonar sobre UX (input para
   `audit_report_ux_and_storytelling`).

## Inputs principales

- `target`: PBIP o `.pbix` con el reporte.
- `pages`: lista de nombres de página a capturar (default: todas).
- `format`: `png | pdf`. Default `png`.
- `resolution`: `desktop | mobile | print`. Default `desktop`.
- `output_dir`: dónde escribir los archivos.
- `wait_ms`: tiempo de espera después de cargar para que el rendering se estabilice (default 2000ms).
- `include_filters_state`: opcional — aplicar filtros antes de capturar.

## Outputs principales

- `screenshots`: lista con `page_name`, `path`, `width_px`, `height_px`, `format`.
- `rendering_warnings`: lista de issues (ej. "page took >5s to render").
- `comparison`: opcional (si se pasa `baseline_dir`) — diff vs baseline por página.

## Dependencias

- [`../01-orchestrator.md`](../01-orchestrator.md) — para ejecutarse en un plan.
- [`../05-engines-adapters.md`](../05-engines-adapters.md) — `superbi-mcp` (Windows only, mejor calidad) o
  Power BI Desktop Bridge (cross-platform, best-effort).
- [`../tools/audit-report-ux-and-storytelling.md`](../audit-report-ux-and-storytelling.md) — v2, input visual.

## Acceptance criteria

- [ ] Captura PNG de cada página de un PBIP local.
- [ ] Resolución desktop y mobile generadas correctamente.
- [ ] Si Desktop no está disponible: degradación a un placeholder + warning (no fail).
- [ ] Diff contra baseline: cuando `|pixel_diff| > 5%` por página, elicita al usuario.
- [ ] Audit log incluye path del screenshot y `rendering_warnings`.

## Riesgos / open questions

- **Windows-only para alta calidad**: `superbi-mcp` da los mejores screenshots pero requiere Windows. En Linux/Mac, fallback a un placeholder generado desde el PBIR JSON (sin rendering real).
- **Visual custom visuals**: algunos custom visuals no renderizan correctamente via Desktop Bridge (depende del visual). Warning explícito por visual que falla.
- **Rendering state vs saved state**: si el reporte tiene bookmarks, slicers, etc., el screenshot puede variar. ¿Capturamos el state default, el saved view, o un state explícito? Default: state saved en el report.
- **Performance**: renderizar 20 páginas puede tardar minutos. Background execution con progress.
- **Información sensible**: el screenshot puede contener datos reales del modelo. ¿Redactar? Propuesta: opt-in via `redact_sensitive=true` (regex sobre números que parecen cifras de ventas).

## Fuera de alcance (v2)

- ❌ Análisis determinístico de varianza sobre rendering (v3).
- ❌ Comparación píxel-exact (es muy frágil por fuentes anti-aliasing).
- ❌ Captura de tooltips / hover state.
- ❌ Generación de GIF / video del flujo de navegación.
- ❌ OCR sobre los screenshots.

## v3 additions (no en este outline)

- Análisis determinístico comparando rendering pre/post con telemetría de VertiPaq.
- Detección automática de "la página cambió visualmente sin razón" via análisis de varianza sobre pixels.
- Integración con `audit_report_ux_and_storytelling` para score visual real (no solo heurístico).

---

## Spec completo

Detalle (Desktop Bridge protocol, superbi-mcp integration,
comparación de imágenes con tolerancia, redacción de PII en screenshots)
en Semana 6-8.
