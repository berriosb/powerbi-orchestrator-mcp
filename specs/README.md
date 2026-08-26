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

**Pendiente real:** `generate_data_dictionary` está listada como MVP en
SPEC §6.1 pero **no tiene spec dedicado en ningún archivo** (solo
descripción de una línea en SPEC §4.5). Crear spec dedicado antes de
arrancar Semana 4. Ver `docs/MVP-STATUS.md` §Tools pendientes de spec.

## Specs por workflow

- [x] `workflows/01-from-csv-to-published-report.md` — De cero a reporte publicado con RLS en un prompt.
- [x] `workflows/02-refactor-to-calc-groups.md` — Refactor medidas → calc group con reconciliación de totales. **No MVP** (depende de `refactor_to_calculation_groups` v2).

## Pendientes (no MVP)

- [ ] `tools/refactor-to-calculation-groups.md` (con reconciliation total) → v2
- [ ] `tools/promote-in-pipeline.md` (dev→test→prod gates) → v2
- [ ] `tools/design-report-page-from-requirements.md` (viz/UX completa) → v2
- [ ] `tools/select-visuals-for-kpis.md` → v2
- [ ] `tools/audit-report-ux-and-storytelling.md` → v2
- [ ] `tools/setup-rls-and-roles.md` → v2
- [ ] `tools/create-semantic-model-from-schema.md` → v2
- [ ] `tools/sync-git-to-workspace.md` → v3
- [ ] `tools/screenshot-report-pages.md` → v2

## Cambios v0.1 (sync 2026-08-21)

- ✅ Creada la estructura modular de specs (5 capas + 3 tools + 2 workflows = 10 specs).
- ✅ Decisión arquitectónica: **delegar** capas 1-2 a engines existentes, **implementar propio** capas 3-6.
- ✅ MVP ambicioso: 12 tools, 4 semanas de un dev senior.
- ✅ Stack: Python 3.11 + `mcp[cli]` + `azure-identity` + `httpx`.
