# Spec: Tool `deploy_to_workspace`

> Publica un PBIP local a un workspace de Fabric. Configura refresh, gateway,
> sensitivity labels opcionales. Incluye pre-deploy gate.

**Status:** v0.1 (spec)
**Prioridad:** P0 — cierra el loop end-to-end (desarrollo → producción)
**Responsable:** codehak
**Depende de:**
- [`../02-cloud-fabric.md`](../02-cloud-fabric.md) — REST Fabric + auth
- [`../03-validation.md`](../03-validation.md) — pre-deploy gate
- [`../05-engines-adapters.md`](../05-engines-adapters.md) — modeling engine (para TMDL)

---

## Cambios respecto a v0.0

N/A (spec inicial).

---

## 1. Objetivo

Tomar un PBIP local (o `.pbix`) y publicarlo a un workspace de Fabric /
Power BI Service con todas las configuraciones necesarias: refresh schedule,
gateway binding, sensitivity labels. Pre-deploy gate evita deploys a
producción con issues conocidos.

**Métricas de éxito:**
- Deploy end-to-end p95 <60s (PBIP mediano, sin refresh).
- Elicitation obligatoria antes de tocar workspaces marcados `production`.
- 0 secrets leaked en outputs / logs.
- Pre-deploy gate configurable por perfil (strict / standard / relaxed).

---

## 2. Inputs y outputs

### Input schema

```yaml
tool_name: deploy_to_workspace
input_schema:
  type: object
  required: [pbip_path, workspace_id]
  properties:
    pbip_path: {type: string}
    workspace_id: {type: string, description: "UUID del workspace target"}
    options:
      type: object
      properties:
        create_dataset:
          type: boolean
          default: true
        create_report:
          type: boolean
          default: true
        overwrite_existing:
          type: boolean
          default: false
          description: "Si existe dataset/report con mismo nombre, sobrescribir"
        configure_refresh:
          type: object
          nullable: true
          properties:
            schedule:
              type: string
              enum: [daily, hourly, weekly, monthly, manual]
              default: daily
            time: {type: string, description: "HH:MM en timezone del workspace"}
            days: {type: array, items: {type: string}, description: "Para weekly: ['Monday', ...]"}
        bind_gateway_id:
          type: string
          nullable: true
        sensitivity_label_id:
          type: string
          nullable: true
          description: "GUID del label en Microsoft Purview"
        pre_deploy_gate:
          type: object
          properties:
            enabled: {type: boolean, default: true}
            profile: {type: string, enum: [strict, standard, relaxed], default: standard}
        wait_for_refresh:
          type: boolean
          default: false
          description: "Si true, ejecuta refresh post-deploy y espera"
    dry_run:
      type: boolean
      default: false
output_schema:
  type: object
  properties:
    deploy_id: {type: string}
    dataset_id: {type: string, nullable: true}
    report_id: {type: string, nullable: true}
    refresh_schedule_id: {type: string, nullable: true}
    pre_deploy_result:
      type: object
      properties:
        passed: {type: boolean}
        score: {type: number}
        blocking_findings: {type: array}
    warnings: {type: array, items: {type: string}}
    errors: {type: array, items: {type: string}}
    artifacts_changed: {type: array, items: {type: string}}
    duration_ms: {type: integer}
```

---

## 3. Pasos internos (Plan)

### Step 1: pre_deploy_gate

- **Engine:** validation
- **Action:** pre_deploy_check
- **Args:** `{target: pbip_path, profile}`
- **Si falla:** abortar antes de cualquier deploy.
- **Rollback step:** N/A (read-only).

### Step 2: elicitation (si workspace es production)

- Verificar `~/.powerbi-orchestrator-mcp/workspaces.yaml` para tag `production`.
- Si tagged: elicitar con details ("deploy a workspace 'Analytics-Prod' está marcado como producción. ¿Continuar?").
- Si untagged o no en config: elicitar de todas formas (primera vez).

### Step 3: ensure workspace + capacity

- Verificar workspace existe; tiene capacity Premium/Fabric asignada (necesario
  para refresh enhanced y deployment pipelines).
- Si falta capacity: warning + degradar a refresh básico.

### Step 4: create dataset (model)

- **Engine:** cloud (REST Fabric)
- **Action:** create_item
- **Args:**
  - `type: "SemanticModel"`
  - `displayName: {from PBIP}`
  - `definition: {TMDL serialized}`

### Step 5: create report (si `create_report=true`)

- **Engine:** cloud (REST Fabric)
- **Action:** create_item
- **Args:**
  - `type: "Report"`
  - `displayName: {from PBIP}`
  - `definition: {PBIR serialized}`
  - `modelId: {dataset_id del step 4}`

### Step 6: bind datasources + gateway

- Si `bind_gateway_id`: PATCH `/datasets/{id}/datasources/{id}` con
  `gatewayId`.
- Si datasources locales (no DirectQuery): skip.

### Step 7: configure refresh schedule

- Si `configure_refresh`: PATCH `/datasets/{id}/refreshSchedule`.
- **Validación:** timezone del workspace debe estar resuelto.

### Step 8: set sensitivity label (si provisto)

- POST `/admin/items/labels/bulkSet` (requiere admin SPN).
- Si no admin SPN: warning + skip.

### Step 9: trigger refresh (si `wait_for_refresh=true`)

- Engine cloud (`run_refresh`) con `wait=true, timeout_ms=1800000`.

### Step 10: audit log

- Audit log entry con todos los items creados/modificados.

---

## 4. Manejo de errores

### 4.1 Pre-deploy gate falla

- Devolver `result: failed` con `pre_deploy_result.blocking_findings`.
- NO continuar con deploy.
- Elicitar: "¿Aplicar auto-fixes y reintentar?"

### 4.2 Workspace no existe

- Devolver `result: failed` con `errors: [workspace_not_found]`.
- Sugerir crear workspace primero (tool fuera de MVP, vía REST directa).

### 4.3 Capacity no asignada

- Si refresh enhanced requerido: warning + degradar a refresh básico.
- Si requiere Premium (ej: incremental refresh): abortar con remediation.

### 4.4 Dataset con mismo nombre ya existe

- Si `overwrite_existing=true`: take_over + update via TMSL.
- Si false: elicitar ("Ya existe dataset 'X'. ¿Sobrescribir o usar otro nombre?").

### 4.5 Permission insuficiente

- Si SPN sin `Dataset.ReadWrite.All`: elicitar con scopes requeridos.

### 4.6 Gateway binding falla

- Si datasource requiere gateway pero el ID provisto no es válido: warning,
  refresh seguirá fallando. Continuar deploy + warning.

### 4.7 Refresh falla post-deploy

- Si `wait_for_refresh=true`: devolver `result: success` (deploy OK) pero
  con `refresh_status: failed`. Elicitar: "¿Diagnosticar refresh failure?"

---

## 5. Acceptance criteria

- [ ] Deploy end-to-end funciona con fixture PBIP → workspace real.
- [ ] Pre-deploy gate se ejecuta antes de cualquier write a Fabric.
- [ ] Elicitation obligatoria para workspaces tagged `production`.
- [ ] Refresh schedule se configura correctamente.
- [ ] Sensitivity labels se aplican cuando admin SPN disponible.
- [ ] `dry_run=true` valida todo pero NO hace writes.
- [ ] Audit log completo: 1 entry con todos los items affected.
- [ ] Rollback: si refresh falla post-deploy, dejar deploy OK pero flag en output.
- [ ] Latencia p95 <60s para PBIP mediano sin refresh.

## 6. Out of scope (MVP)

- ❌ Crear workspace nuevo (v2).
- ❌ Deployment Pipelines (dev→test→prod gates) (v2).
- ❌ Git Integration post-deploy (v3).
- ❌ Incremental refresh (requiere Premium, v2).
- ❌ Parametrización de datasources (v2).
- ❌ Dataflows / Lakehouses (no son target MVP).

## 7. Riesgos

| Riesgo | Mitigación |
|--------|-----------|
| SPN scopes insuficientes | Documentar scopes mínimos; elicitar con remediation clara. |
| Workspace sin capacity | Warning + degradar (basic refresh en vez de enhanced). |
| Refresh credentials expired | Diagnosticar con refresh_doctor + sugerir fix al usuario. |
| Take over de dataset ajeno | Elicitación fuerte + audit log con warning. |
| Deploy a workspace equivocado | Tag workspaces en config; elicitation si tagged prod; dry-run default. |
| TMSL mal formado | Validar antes de aplicar; fallback a re-publish completo. |

## 8. Specs relacionados

- [`../01-orchestrator.md`](../01-orchestrator.md) — elicitation + audit
- [`../02-cloud-fabric.md`](../02-cloud-fabric.md) — REST Fabric
- [`../03-validation.md`](../03-validation.md) — pre-deploy gate
- [`../05-engines-adapters.md`](../05-engines-adapters.md) — modeling engine
- [`../workflows/01-from-csv-to-published-report.md`](../workflows/01-from-csv-to-published-report.md) — usuario principal
