# Spec: Tool `setup_rls_and_roles` (v2) — OUTLINE

> Automatiza la creación de roles RLS, asignación de miembros, y
> ejecución de la matriz de prueba (RLS matrix). Cierra el gap del
> workflow 01 que en MVP requiere setup manual del test.

**Status:** v0.1 (outline — sem 6-8 de implementación)
**Versión:** v2
**Capa:** 1+3 (Modelo + Cloud)

---

## Objetivo

Tomar una spec de seguridad (qué roles, qué filtros, qué miembros)
y aplicarla atómicamente al modelo + testear que cada rol filtra
correctamente usando `Execute Queries` con `EffectiveIdentity` contra
Fabric REST API.

El MVP actual (`run_dax_regression` con `EffectiveIdentity` manual)
cubre el testeo pero requiere que el agente cree los roles y asigne
miembros a mano. Este tool automatiza los 3 pasos juntos.

## Inputs principales

- `target`: PBIP o Fabric workspace.
- `spec`: lista de roles:
  ```yaml
  - role_name: "Region-West"
    filter_expression: "[Region] = \"West\""
    table: "DimRegion"
    members:
      - type: email
        value: "gerente.west@acme.test"
      - type: group
        value: "acme-west-managers"
    test_queries:
      - name: "Total Sales by Region"
        dax: "EVALUATE ROW(\"Total\", [Total Sales])"
        expected:
          "West": 100000
          "East": 0
          "Central": 0
  ```
- `dry_run`: bool, default true (crear roles pero no deploy).
- `rollback_on_test_failure`: bool, default true (si algún test falla, rollback).

## Outputs principales

- `roles_created`: lista con `role_name`, `role_id`, `members_count`.
- `test_results`: lista con `role_name`, `query_name`, `actual`, `expected`, `passed`.
- `failed_test`: el primer test que falló (si aplica).
- `rollback_handle`: snapshot pre-cambio.
- `risk_score`: 0.0-1.0.

## Dependencias

- [`../01-orchestrator.md`](../01-orchestrator.md) — `plan_change` + `apply_plan` con rollback.
- [`../02-cloud-fabric.md`](../02-cloud-fabric.md) — `executeQueries` con `EffectiveIdentity` para los tests.
- [`../03-validation.md`](../03-validation.md) — `run_dax_regression` puede invocarse para tests más complejos.
- [`../05-engines-adapters.md`](../05-engines-adapters.md) — modeling engine para crear los roles (TMDL edit o TOM).
- [`../tools/run-dax-regression.md`](../run-dax-regression.md) — para queries de test más allá de las simples.

## Acceptance criteria

- [ ] Crea 3 roles RLS desde spec YAML sin pasos manuales.
- [ ] Asigna miembros (email + group) correctamente.
- [ ] Ejecuta test matrix y reporta pass/fail por (role × query).
- [ ] Si algún test falla con `rollback_on_test_failure=true`, rollback completo (elimina roles, deshace asignaciones).
- [ ] Audit log incluye cada test con su expected vs actual.
- [ ] Misma matriz de fixture PBIP retorna 3/3 passed (consistente con `tests/fixtures/README.md` §5).

## Riesgos / open questions

- **Resolución de group members**: en Fabric, asignar un grupo AAD no significa que los miembros hereden acceso al workspace automáticamente. ¿Esto es responsabilidad de este tool o del admin? Default: documentar el prerequisite y warning si el grupo no es resolvable.
- **EffectiveIdentity availability**: `executeQueries` con RLS funciona pero el cache de Fabric puede tener datos stale. ¿Forzar refresh antes de los tests? Propuesta: opt-in via flag.
- **Filter expressions con cross-table filtering**: si un filtro referencia una dimensión que no está relacionada con la fact table, ¿detectamos el error? Propuesta: validar relaciones antes de aplicar.
- **Roles en modelo con `isHidden=true`**: el role sigue activo pero no aparece en Desktop. ¿Reportar?
- **Test queries con muchos valores esperados**: la spec con `expected: {West: 100000, East: 0, ...}` se vuelve verbosa. ¿Helper para generar expected desde un dataset?

## Fuera de alcance (v2)

- ❌ Object-level security (OLS), solo RLS.
- ❌ Roles con dynamic row-level security via `USERPRINCIPALNAME()` lookup table.
- ❌ Roles cross-workspace.
- ❌ Auto-generación de la spec desde análisis de la org (quién debe ver qué).

---

## Spec completo

Detalle (Pydantic models de la spec YAML, integración con el RLS matrix
test, manejo de `USERPRINCIPALNAME()` en filter expressions) en Semana 6-8.
