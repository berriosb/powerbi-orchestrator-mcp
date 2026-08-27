# Feature Specs — powerbi-orchestrator-mcp

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
- [x] `tools/generate-data-dictionary.md` — Data dictionary Markdown/HTML con diagrama Mermaid + coverage score (creado 2026-08-26).

### Tools MVP v1 documentadas en specs por capa (no en `specs/tools/`)

Estas tools están completamente especificadas dentro del spec de la capa
correspondiente, en su sección dedicada. No se duplican como archivos
separados en `specs/tools/` para evitar drift entre copias:

- [x] `connect_target` → [`01-orchestrator.md` §3.1](./01-orchestrator.md)
- [x] `plan_change` → [`01-orchestrator.md` §3.2](./01-orchestrator.md)
- [x] `apply_plan` → [`01-orchestrator.md` §3.3](./01-orchestrator.md)
- [x] `run_refresh` → [`02-cloud-fabric.md` §3](./02-cloud-fabric.md)
- [x] `run_dax_regression` → [`03-validation.md` §5](./03-validation.md) (input/output schema YAML completo).
- [x] `diff_models` → [`03-validation.md` §2.4](./03-validation.md) (Pydantic models + reglas de breaking).
- [x] `pre_deploy_check` → [`03-validation.md` §4](./03-validation.md) (input/output schema + profiles).
- [x] `apply_theme_and_accessibility_rules` → [`04-viz-ux.md` §3](./04-viz-ux.md) (theme.json + WCAG rules).

**Decisión arquitectónica (post-audit 2026-08-26):** un tool MVP v1 puede
tener su spec dedicado en `specs/tools/` (si su lógica es ortogonal a una
capa única) o vivir dentro del spec de la capa que lo implementa (si es
parte del dominio de esa capa). Esto evita proliferación de archivos y
duplicación de schemas.

## Specs por workflow

- [x] `workflows/01-from-csv-to-published-report.md` — De cero a reporte publicado con RLS en un prompt.
- [x] `workflows/02-refactor-to-calc-groups.md` — Refactor medidas → calc group con reconciliación de totales. **No MVP** (depende de `refactor_to_calculation_groups` v2).

## Specs pendientes (post-MVP)

### v1.1 — Semana 5

- [ ] `tools/add-measure-with-validation.md` → v1.1 (spec dedicado a crear)
- [ ] `tools/create-report-from-dataset.md` → v1.1 (spec dedicado a crear)
- [ ] `tools/edit-report-visual.md` → v1.1 (spec dedicado a crear)

### v2 — Semanas 6-8

- [ ] `tools/refactor-to-calculation-groups.md` (con reconciliation total)
- [ ] `tools/promote-in-pipeline.md` (dev→test→prod gates)
- [ ] `tools/design-report-page-from-requirements.md` (viz/UX completa)
- [ ] `tools/select-visuals-for-kpis.md`
- [ ] `tools/audit-report-ux-and-storytelling.md`
- [ ] `tools/setup-rls-and-roles.md`
- [ ] `tools/create-semantic-model-from-schema.md`
- [ ] `tools/screenshot-report-pages.md`

### v3 — Semanas 9-12

- [ ] `tools/sync-git-to-workspace.md`

## Cambios v0.2 (audit 2026-08-26)

- ✅ Conteos corregidos: 26 tools catálogo (era 28), 12 MVP (sin cambios), 14 no-MVP (10 v2 + 4 v3).
- ✅ `tools/generate-data-dictionary.md` creado (1 página con schema completo).
- ✅ Workflow 01 §9: tabla de 3 columnas (MVP / MVP+v1.1 / v2) para distinguir alcances.
- ✅ Links rotos a specs inexistentes eliminados (`audit-model-and-report.md`, `04-viz-ux.md`).
- ✅ MVP-STATUS.md actualizado con issues abiertos, decisión de plan renegociado (5 semanas), y tabla de ubicación real de specs.
- ✅ Decisión arquitectónica adoptada: specs pueden vivir en `specs/tools/` (dedicado) o en spec por capa (inline).
- ✅ Plan renegociado a 5 semanas: Semana 5 v1.1 con `add_measure_with_validation`, `create_report_from_dataset`, `edit_report_visual`.

## Cambios v0.1 (sync 2026-08-21)

- ✅ Creada la estructura modular de specs (5 capas + 3 tools + 2 workflows = 10 specs).
- ✅ Decisión arquitectónica: **delegar** capas 1-2 a engines existentes, **implementar propio** capas 3-6.
- ✅ MVP ambicioso: 12 tools, 4 semanas de un dev senior.
- ✅ Stack: Python 3.11 + `mcp[cli]` + `azure-identity` + `httpx`.
