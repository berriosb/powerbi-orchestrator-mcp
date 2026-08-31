# Spec: Tool `promote_in_pipeline` (v2) — OUTLINE

> Promueve contenido a través de un deployment pipeline de Power BI
> (dev → test → prod) ejecutando quality gates configurables en cada
> stage. Cierra el último gap del workflow "del edit a producción".

**Status:** v0.1 (outline — sem 6-8 de implementación)
**Versión:** v2
**Capa:** 3 (Cloud)

---

## Objetivo

Automatizar la promoción de items (datasets, reports, dataflows) entre
los stages de un deployment pipeline de Power BI Fabric. En cada
transición (dev→test, test→prod) ejecutar un **quality gate**
configurable (pre-deploy check + opcional `audit_model_and_report` +
opcional `run_dax_regression`) y solo promover si pasa. Si falla,
rollback al stage anterior (cuando sea posible via take_over + restore)
o pausar para acción humana.

## Inputs principales

- `pipeline_id`: ID del deployment pipeline.
- `source_stage`: `dev | test | prod` (origen).
- `target_stage`: `dev | test | prod` (destino).
- `items`: lista de item IDs a promover (default: todos los del stage).
- `quality_gates`: lista de checks a ejecutar antes de promover. Cada check:
  - `type`: `pre_deploy_check` | `audit_model_and_report` | `run_dax_regression` | custom.
  - `profile`: nombre del profile del check.
  - `blocking`: bool (si falla, abortar promoción).
- `notify_on_failure`: bool, default true (elicitar al usuario).
- `dry_run`: bool, default false.

## Outputs principales

- `promoted_items`: lista de item IDs promovidos con su nuevo stage.
- `gates_executed`: resultado de cada check.
- `failed_gate`: el primer check que falló (si aplica).
- `rollback_performed`: bool.
- `promotion_id`: ID de la operación de Fabric (para tracking).

## Dependencias

- [`../01-orchestrator.md`](../01-orchestrator.md) — `apply_plan` con rollback.
- [`../02-cloud-fabric.md`](../02-cloud-fabric.md) — endpoints de Deployment Pipelines
  (`createPipeline`, `assignWorkspace`, `deployPipeline`, `getDeployOperation`).
- [`../03-validation.md`](../03-validation.md) — `pre_deploy_check`, `audit_model_and_report`, `run_dax_regression`.
- [`../tools/deploy-to-workspace.md`](../deploy-to-workspace.md) — usa el mismo flujo de deploy base.

## Acceptance criteria

- [ ] Promoción dev→test funciona con un pipeline de 3 stages.
- [ ] Quality gate `pre_deploy_check` con profile `prod-ready` bloquea promoción si falla.
- [ ] Quality gate `audit_model_and_report` con score <80 aborta promoción.
- [ ] Quality gate `run_dax_regression` con delta >0.1% aborta promoción.
- [ ] Rollback automático al stage anterior si la promoción a prod falla a mitad (cuando Fabric lo soporta).
- [ ] Audit log incluye `pipeline_id`, `source_stage`, `target_stage`, `promotion_id` de Fabric.
- [ ] Elicitation obligatoria antes de promover a `prod` (incluso con `--allow-prod`).

## Riesgos / open questions

- **Atomicidad de Fabric**: `deployPipeline` es una operación async de Fabric; no siempre se puede revertir atómicamente. Documentar qué stages son reversibles.
- **Dependencias entre items**: si un dataset depende de un dataflow que NO se promueve, ¿qué pasa? Default: detectar y bloquear con elicitation.
- **Quality gates custom de la org**: ¿plugin system ya mencionado en SPEC §7 v3, o antes? Propuesta: en v2, gates custom son JSON configs (Python scripts via subprocess). Plugin system formal en v3.
- **Concurrency**: ¿qué pasa si dos devs promueven a la vez al mismo stage? Default: lock a nivel de pipeline via Fabric REST (`getDeployOperation` muestra `InProgress`).
- **Notificaciones**: integración con Teams/email fuera de MVP.

## Fuera de alcance (v2)

- ❌ Crear el pipeline (solo se asume pre-existente;用户提供 pipeline_id).
- ❌ Asignar workspaces a stages (asumimos pre-asignados).
- ❌ Quality gates que dependen de otros items fuera del pipeline.
- ❌ Aprobaciones multi-usuario (requerido para prod en muchas orgs).
- ❌ Histórico de promociones con diff visual.

---

## Spec completo

Detalle de implementación (Pydantic models, polling async de
`getDeployOperation`, integración con cada tipo de quality gate) se
desarrolla en Semana 6-8.
