# Spec: Tool `set_sensitivity_labels` (v3) — OUTLINE

> Aplica Microsoft Purview sensitivity labels (governance) en lote a
> uno o varios items de Fabric (datasets, reports, dataflows, paginated
> reports). Cierra el último gap de governance del MVP+ roadmap.

**Status:** v0.1 (outline — Sprint 12 de implementación)
**Versión:** v3
**Capa:** 3 (Cloud)

---

## Objetivo

Tomar un catálogo de items de Fabric y aplicar un sensitivity label
(Microsoft Purview) a cada uno en una sola operación atómica de
administración. Casos de uso:

1. **Onboarding de orgs nuevas:** etiquetar todos los datasets en
   producción con `Confidential - Internal` para evitar fugas.
2. **Compliance refresh:** rotar un label existente (ej. cambiar
   `General` → `Confidential`) cuando se publica nueva guía de
   clasificación.
3. **Auditoría manual:** un agente valida que cada item tenga label y
   aplica el correcto cuando falta.

## Inputs principales

- `tenant_id`: ID del tenant (AAD). Opcional si `admin_token_provider`
  ya está ambient.
- `items`: lista de `{item_id, item_name}` (o solo IDs si se prefiere).
- `label_id`: GUID del sensitivity label (Purview).
- `label_name`: nombre legible (para logs + audit).
- `admin_scopes`: scopes del SPN (default: `*.Admin.*`).
- `admin_token_provider`: callable que retorna un Fabric admin token.
- `dry_run`: bool, default true.

## Outputs principales

- `applied`: lista de `{item_id, status: applied | failed | skipped,
  reason}`.
- `admin_endpoint_calls`: log de los POST a `/admin/items/labels/bulkSet`.
- `audit_log_entries`: ids de filas creadas en el audit log SQLite.

## Dependencias

- [`../01-orchestrator.md`](../01-orchestrator.md) — `plan_change` /
  `apply_plan` opcional.
- [`../02-cloud-fabric.md`](../02-cloud-fabric.md) — endpoint
  `POST /admin/items/labels/bulkSet`, scopes `*.Admin.*`, scope gate
  explícito.
- [`../05-engines-adapters.md`](../05-engines-adapters.md) —
  Azure Identity SDK para los tokens; rate limiter heredado.

## Acceptance criteria

- [ ] Aplica un label a N items via `bulkSet` en una sola llamada REST.
- [ ] Dry-run reporta qué items serían tocados sin invocar la API.
- [ ] Si el SPN no tiene scope de Admin, elicita con remediation_hint.
- [ ] Si un item ya tiene el mismo label, lo reporta como ``skipped``
  con `reason: already_labeled` (sin llamada REST adicional).
- [ ] Audit log incluye `tenant_id`, `item_count`, `label_id`,
  `result` por item.
- [ ] Si la API devuelve 403, elicita con el link a Purview admin
  center para conceder scopes.
- [ ] Redacta PII en el audit log (el label_id es un GUID, no PII;
  item names se redactan si `redact_names=true`).

## Riesgos / open questions

- **Purview tenant mismatch**: el label ID puede ser de un tenant
  distinto al del workspace. Validar.
- **Label no existe**: si el `label_id` no se encuentra, elicitar.
- **Rate limiting**: `bulkSet` tiene quota (10K items por call por
  default). Si excede, particionar.
- **Permisos**: el SPN debe tener `InformationProtectionPolicy.Read.All`
  + ``InformationProtectionPolicy.Apply.All``.
- **Idempotencia**: la operación es idempotente (mismo label =
  no-op), pero el audit log debe registrar el intento.

## Fuera de alcance (v3)

- ❌ Labeling condicional basado en heurísticas (eso es policy v4+).
- ❌ Auditoría inversa (listar todos los labels aplicados) — el tool
  no expone un endpoint de lectura.
- ❌ Labels jerárquicos (sub-labels) — solo top-level.
- ❌ Decisiones de label automáticas por sensibilidad del DAX o de los
  datos.

---

## Spec completo

Detalle (Pydantic models del admin scope gate, integración con
bulkSet, mapping de errores HTTP → elicitation outcomes) en Sprint 12.
