# Feature Specs — powerbi-orchestrator

> Specs modulares por capa y por tool. Cada spec es un entregable implementable.
>
> **Última actualización:** 2026-08-21 (specs v0.1, MVP ambicioso)

> Las casillas de esta lista indican que existe la spec, no que todos sus
> criterios estén verificados. Para estado de implementación consultar
> [`docs/MVP-STATUS.md`](../docs/MVP-STATUS.md).

---

## Cómo leer este repo de specs

- **[`SPEC.md`](../SPEC.md)** — visión + arquitectura 6 capas + stack + MVP.
- **[`docs/architecture.md`](../docs/architecture.md)** — arquitectura detallada con diagramas.
- **Specs por capa (`0X-*.md`)** — cómo se implementa cada capa del servidor.
- **Specs por tool (`tools/*.md`)** — herramientas individuales de alto nivel.
- **Specs por workflow (`workflows/*.md`)** — flujos end-to-end compuestos.
- **[`docs/MVP-STATUS.md`](../docs/MVP-STATUS.md)** — qué está implementado vs qué es spec.
- **[`docs/IMPLEMENTATION-PLAN-v1.0.md`](../docs/IMPLEMENTATION-PLAN-v1.0.md)** — roadmap.

---

## Specs por capa

- [x] `01-orchestrator.md` — Capa 6: planner, connect_target, apply_plan, rollback engine.
- [x] `02-cloud-fabric.md` — Capa 3: REST Fabric, auth, deploy, refresh, RLS, git integration.
- [x] `03-validation.md` — Capa 4: BPA, DAX linter, regression runner, accessibility, pre-deploy gate.
- [x] `04-viz-ux.md` — Capa 5: visual registry, suggester, layout, theme, storytelling.
- [x] `05-engines-adapters.md` — Cómo se delega a `powerbi-modeling-mcp`, `superbi-mcp`, `te`, `dscmd`, `pbip-validator`.

## Specs por tool (MVP ambicioso)

- [x] `tools/safe-rename.md` — Tool estrella: rename cross-engine (model + DAX + M + report bindings) con rollback.
- [x] `tools/audit-model-and-report.md` — Auditoría integral: BPA + WCAG + lint + star-schema + naming.
- [x] `tools/deploy-to-workspace.md` — Publish PBIP a Fabric workspace con refresh + RLS + labels.

## Specs por workflow

- [x] `workflows/01-from-csv-to-published-report.md` — De cero a reporte publicado con RLS en un prompt.
- [x] `workflows/02-refactor-to-calc-groups.md` — Refactor medidas → calc group con reconciliación de totales.

## Pendientes (no MVP)

- [ ] `tools/add-measure-with-validation.md` → MVP v1 (corrección: faltaba del índice)
- [ ] `tools/create-report-from-dataset.md` → MVP v1 (corrección: faltaba del índice)
- [ ] `tools/edit-report-visual.md` → MVP v1 (corrección: faltaba del índice)
- [ ] `tools/diff-models.md` → MVP v1 (corrección: faltaba del índice)
- [ ] `tools/refactor-to-calculation-groups.md` (con reconciliation total) → v2
- [ ] `tools/promote-in-pipeline.md` (dev→test→prod gates) → v2
- [ ] `tools/design-report-page-from-requirements.md` (viz/UX completa) → v2
- [ ] `tools/select-visuals-for-kpis.md` → v2
- [ ] `tools/audit-report-ux-and-storytelling.md` → v2
- [ ] `tools/setup-rls-and-roles.md` → v2
- [ ] `tools/create-semantic-model-from-schema.md` → v2
- [ ] `tools/run-dax-regression.md` → MVP v1 (corrección: estaba mal clasificado como v3; falta spec)
- [ ] `tools/sync-git-to-workspace.md` → v3
- [ ] `tools/screenshot-report-pages.md` → v2 (corrección: era v3, es post-MVP)
- [ ] `tools/apply-theme-and-accessibility-rules.md` → MVP v1 (corrección: estaba mal clasificado como v3)

## Cambios v0.1 (sync 2026-08-21)

- ✅ Creada la estructura modular de specs (5 capas + 3 tools + 2 workflows = 10 specs).
- ✅ Decisión arquitectónica: **delegar** capas 1-2 a engines existentes, **implementar propio** capas 3-6.
- ✅ MVP ambicioso: 12 tools, 4 semanas de un dev senior.
- ✅ Stack: Python 3.11 + `mcp[cli]` + `azure-identity` + `httpx`.
