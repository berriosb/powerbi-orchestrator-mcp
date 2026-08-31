# Spec: Tool `refactor_to_calculation_groups` (v2) — OUTLINE

> Refactoriza un grupo de medidas candidatas a un calculation group de
> Tabular Editor con reconciliación de totales. Cubre el workflow 02
> completo.

**Status:** v0.1 (outline — sem 6-8 de implementación)
**Versión:** v2
**Capa:** 1 (Modelo)

---

## Objetivo

Detectar automáticamente un set de medidas que comparten estructura
(típicamente: variantes de una medida base que difieren en time
intelligence o scope — ej. `Total Sales YTD`, `Total Sales QTD`,
`Total Sales MTD`, `Total Sales Prior Year`) y consolidarlas en un
**calculation group** con un item por cada variante. Reconciliar
totales para que los visuales que referencian las medidas originales
sigan mostrando el mismo número, pero ahora consumiendo el calc group.

## Inputs principales

- `target`: PBIP / Fabric workspace / `.pbix` con el modelo.
- `scope`: `auto` (detección) o `explicit` (lista de medidas candidatas).
- `min_candidates`: mínimo de medidas para considerar el refactor (default 3).
- `reconciliation_mode`: `strict` (totales exactos) vs `tolerance` (±0.01% default).
- `dry_run`: bool, default true (calcular diff antes de aplicar).
- `preserve_originals`: bool, default false (si true, deja las medidas viejas como aliases).

## Outputs principales

- `groups_created`: lista de calc groups creados con sus items.
- `measures_remapped`: mapping `{old_measure_name → calc_group_item_ref}`.
- `reconciliation_report`: por cada visual/reporte, delta de totales pre vs post.
- `rollback_handle`: snapshot pre-refactor.
- `risk_score`: 0.0-1.0.

## Dependencias

- [`../01-orchestrator.md`](../01-orchestrator.md) — `plan_change` + `apply_plan` con rollback.
- [`../02-cloud-fabric.md`](../02-cloud-fabric.md) — refresh post-refactor (target Fabric).
- [`../03-validation.md`](../03-validation.md) — `run_dax_regression` baseline + tolerance check.
- [`../05-engines-adapters.md`](../05-engines-adapters.md) — `te` o `powerbi-modeling-mcp` para crear el calc group + remapear referencias.
- [`../workflows/02-refactor-to-calc-groups.md`](../workflows/02-refactor-to-calc-groups.md) — workflow canónico.

## Acceptance criteria

- [ ] Auto-detecta al menos 3 patrones conocidos: time intelligence variants, scope variants, format variants.
- [ ] Reconciliation strict: 0 deltas en totales para todos los visuales que referencian medidas afectadas.
- [ ] Reconciliation tolerance: 100% de los visuales con `|delta| < 0.01%`.
- [ ] Rollback atómico: restore byte-idéntico del PBIP pre-refactor.
- [ ] Audit log con HMAC chain entry + plan_id + execution_id.
- [ ] Workflow 02 ejecuta end-to-end con el fixture PBIP.

## Riesgos / open questions

- **Naming del calc group:** heurística actual `TimeIntelligence_<TableName>` puede no encajar con convenciones de la org. ¿Opciones custom?
- **Items con dependencies entre sí** (ej. `%MoM` que depende de `%PriorMonth`): orden de creación en el calc group debe respetar DAG.
- **Reconciliación en visuales con filtros cruzados**: ¿el visual mantiene el mismo número si se cambió el filter context?
- **Soporte para calc groups condicionales** (`SELECTEDMEASURE`): fuera de MVP.
- **Migración de medidas con `isHidden=true`**: actualmente se asume que están en uso.

## Fuera de alcance (v2)

- ❌ Crear calc groups desde cero sin medidas candidatas.
- ❌ Migrar measures con parameter tables (variants dinámicas).
- ❌ Detección automática de `Calculation Group` pre-existente con items duplicados.
- ❌ Calc groups anidados (calc group que referencia otro calc group).

---

## Spec completo

El detalle de implementación (Pydantic models, algoritmo de detección,
heurísticas de reconciliación, casos edge) se desarrolla en su semana
correspondiente (Semana 6-8 según `SPEC.md` §7 v2).
