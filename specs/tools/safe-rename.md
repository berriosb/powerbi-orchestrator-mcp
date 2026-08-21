# Spec: Tool `safe_rename` ⭐

> Tool estrella del MVP. Renombra un objeto propagando a modelo + DAX + M
> + report bindings en una sola transacción lógica con rollback atómico.

**Status:** v0.1 (spec)
**Prioridad:** P0 — feature diferenciador #1
**Responsable:** codehak
**Depende de:**
- [`../01-orchestrator.md`](../01-orchestrator.md) — planner + rollback engine
- [`../02-cloud-fabric.md`](../02-cloud-fabric.md) — refresh post-rename (si target es Fabric)
- [`../03-validation.md`](../03-validation.md) — DAX linter + BPA + WCAG inline
- [`../04-viz-ux.md`](../04-viz-ux.md) — WCAG alt text propagation
- [`../05-engines-adapters.md`](../05-engines-adapters.md) — ModelingEngine + ReportEngine

**Spec relacionado:** [`../01-orchestrator.md`](../01-orchestrator.md) §3.2 (plan_change template)

---

## Cambios respecto a v0.0

N/A (spec inicial).

---

## 1. Objetivo

Permitir que un agente renombre un objeto (tabla, columna, medida, jerarquía)
de Power BI **sin romper** los visuales del reporte, las expresiones DAX que
lo referencian, ni las queries M de Power Query — y con **rollback atómico**
si algo falla.

**Métricas de éxito:**
- Latencia p95 <10s para modelo de 100 medidas, 50 visuales.
- 0 visuales rotos post-rename (verificable con `pbip-validator`).
- 0 DAX measures inválidas post-rename.
- Rollback exitoso en 100% de fallos de step intermedio.

---

## 2. Caso de uso

**Input del agente (NL):**
> Renombra `Customer[ID]` a `Customer[CustomerKey]` en el modelo
> `./out/sales.pbip` y propaga a todos los visuales.

**Internal flow (lo que hace el orquestador):**

```
1. plan_change(template="safe_rename", args={...})
   ├─ analyze_impact():
   │  ├─ modeling engine: listar todas las measures/columns que referencian Customer[ID]
   │  ├─ report engine: parsear PBIR, buscar Column references a "Customer.ID"
   │  ├─ M engine: parsear .m files, buscar referencias literales
   │  └─ estimated_changes: {12 measures, 4 visuals, 2 queries M}
   └─ calcular risk_score: 0.4 (rename bajo impacto)

2. elicitation: "¿Aplicar? affected=18 objects"
   └─ user: "sí"

3. apply_plan(plan_id, dry_run=false)
   ├─ snapshot: git tag + tar backup
   ├─ step 1: modeling.update_column(Customer, ID, CustomerKey)
   │  └─ validator: pbip_validate_model → ✅
   ├─ step 2: report.propagate_rename(Customer[ID] → Customer[CustomerKey])
   │  ├─ encuentra 4 visuales con Column references
   │  ├─ actualiza queryRef, Column references, Expression references
   │  └─ validator: pbir_validate → ✅
   ├─ step 3: m.propagate_rename_in_m_files(...)
   │  ├─ 2 archivos .m afectados
   │  └─ validator: m lint → ✅
   ├─ step 4: dax.recompile_all_measures()
   │  └─ ejecuta cada measure con EVALUATE → confirma que no rompe
   └─ step 5: audit_minimal(BPA + accessibility)
       └─ score delta: -2 (acceptable)

4. result: success, changed_files=[...], rollback_handle=snap_2026-08-21
```

---

## 3. Inputs y outputs

### Input schema

```yaml
tool_name: safe_rename
input_schema:
  type: object
  required: [old_path, new_path, target]
  properties:
    target:
      oneOf:
        - type: object
          properties:
            type: {const: pbip_folder}
            ref: {type: string}
        - type: object
          properties:
            type: {const: fabric_workspace}
            workspace_id: {type: string}
            dataset_id: {type: string}
            report_id: {type: string, nullable: true}
        - type: object
          properties:
            type: {const: pbix_file}
            ref: {type: string}
        - type: object
          properties:
            type: {const: pbi_desktop}
            ref: {type: string}
    old_path:
      type: string
      description: "Full path: 'Table[Column]' o 'Table[Measure]' o 'Table'"
      examples: ["Customer[ID]", "Sales[TotalAmount]", "Date"]
    new_path:
      type: string
      examples: ["Customer[CustomerKey]", "Sales[NetAmount]", "CalendarDate"]
    scope:
      type: string
      enum: [model_only, model_and_report, all_including_m]
      default: model_and_report
    dry_run:
      type: boolean
      default: false
    validation_profile:
      type: string
      enum: [minimal, standard, strict]
      default: standard
      description: "minimal=lint DAX only, standard=+BPA+pbir_validate, strict=+regression+accessibility"
    confirm_impact_threshold:
      type: integer
      default: 20
      description: "Elicitar si impact_score > N"
```

### Output schema

```yaml
output_schema:
  type: object
  properties:
    result:
      type: string
      enum: [success, rolled_back, partial, failed]
    changes:
      type: array
      items:
        type: object
        properties:
          file: {type: string}
          change_type:
            type: string
            enum: [renamed, ref_updated, alt_text_updated]
          before: {type: string}
          after: {type: string}
    validation:
      type: object
      properties:
        dax_lint:
          type: object
          properties:
            passed: {type: integer}
            warnings: {type: integer}
            errors: {type: integer}
        bpa_delta: {type: number, description: "Cambio en BPA score"}
        pbir_ok: {type: boolean}
        wcag_preserved: {type: boolean, description: "Alt text no se rompió"}
        accessibility_preserved: {type: boolean}
        m_lint:
          type: object
          properties:
            passed: {type: integer}
            warnings: {type: integer}
    rollback_handle:
      type: string
      description: "Para revertir manual post-success"
    impact_summary:
      type: object
      properties:
        measures_affected: {type: integer}
        visuals_affected: {type: integer}
        m_queries_affected: {type: integer}
        total_files_changed: {type: integer}
        duration_ms: {type: integer}
    warnings: {type: array, items: {type: string}}
    errors: {type: array, items: {type: string}}
```

---

## 4. Pasos internos (Plan)

### Step 1: snapshot

- **Engine:** validation
- **Action:** create_snapshot
- **Args:** `{target, label: "pre-rename-{timestamp}"}`
- **Rollback step:** restore_snapshot
- **Outputs:** `snapshot_handle`

### Step 2: model rename (ModelingEngine.update_*)

- **Engine:** modeling (via [`../05-engines-adapters.md`](../05-engines-adapters.md))
- **Action:** depends on object type:
  - `Table[Column]` → `column.update`
  - `Table[Measure]` → `measure.update` (incluyendo expresión DAX)
  - `Table` → `table.rename`
  - `Hierarchy[Level]` → `user_hierarchy.update`
- **Rollback step:** revert rename
- **Validators:** `pbip_validate_model` (básica)

### Step 3: report bindings propagation (ReportEngine.propagate_rename)

- **Engine:** report (via [`../05-engines-adapters.md`](../05-engines-adapters.md))
- **Action:** propagate_rename
- **Args:** `{old_path, new_path, scope: "report_bindings"}`
- **Implementation:**
  - Parsea cada `visual.json` del PBIR.
  - Busca Column / Measure / Hierarchy references con regex preciso.
  - Actualiza:
    - `queryRef` (ej: `Aggregate('Customer'[ID])` → `Aggregate('Customer'[CustomerKey])`)
    - `Column references` en `visualContainerObjects`
    - `Sort` specifications
    - `Filters` que referencian el campo
    - `Alt text` que menciona el campo (preservar accesibilidad)
- **Skip:** visuals que NO referencian el campo (no tocar).
- **Rollback step:** propagate_rename inverso
- **Validators:** `pbir_validate` (via `pbip-validator` CLI)

### Step 4: M queries propagation (M engine)

- **Engine:** modeling (operación sobre archivos TMDL/M)
- **Action:** update_m_references
- **Args:** `{old_path, new_path, target_files: "all"}`
- **Skip:** cuando `scope != all_including_m`.
- **Rollback step:** revert.
- **Validators:** `m_lint` (regex-based, MVP).

### Step 5: DAX recompilation

- **Engine:** modeling (execute_dax)
- **Action:** recompile_referenced_measures
- **Args:** `{measures: [lista de medidas afectadas]}`
- **Implementation:** para cada measure afectada, ejecuta
  `EVALUATE ROW("x", [Measure])` con `EffectiveIdentity=None` para confirmar
  que compila y devuelve 1 fila (no error).
- **Rollback step:** N/A (read-only).
- **Validators:** si alguna falla → rollback step 2-4.

### Step 6: post-rename audit

- **Engine:** validation
- **Action:** audit_minimal
- **Args:** `{checks: [bpa, dax_lint, accessibility]}` (subset)
- **Validators:** N/A (informational).

---

## 5. Casos edge y errores

### 5.1 `old_path` no existe

- `ModelingError: object_not_found`
- Remediation: listar objetos similares (fuzzy match) → elicitar al usuario.

### 5.2 `new_path` ya existe

- `ModelingError: name_conflict`
- Remediation: elicitar "¿renombrar el existente también?" o usar suffix.

### 5.3 Nombre no válido (caracteres especiales, espacios)

- `ModelingError: invalid_name`
- Remediation: sugerir nombre válido + elicitar.

### 5.4 Cambio rompería >X visuales

- Si `visuals_affected > confirm_impact_threshold`:
  - Elicitar antes de ejecutar.
  - Default threshold: 20.

### 5.5 M query tiene referencia literal no detectada

- Después del rename, `m_lint` detecta que un `.m` file menciona el old_path.
- Log warning con file:línea.
- NO falla el rename (es warning, no error).
- User puede decidir si quiere fix manual.

### 5.6 Visual con alt text que menciona el campo

- NO modificar alt text automáticamente (rompería UX).
- Log warning si alt text podría quedar desactualizado.
- User puede decidir.

### 5.7 Rollback falla

- `RollbackError` con paths exactos.
- NO abortar; devolver `result: "rolled_back_partial"` con detalle.
- Audit log marca el incidente.

### 5.8 Target es Fabric (no local)

- Step 2 se hace via `take_over_dataset` + TMSL script + reload.
- Steps 3-4 se hacen exportando PBIP a local primero, editando, re-publicando.
- Refresh post-rename (Step 7 opcional).
- Advertencia: tiempo total >5 min.

---

## 6. Acceptance criteria

- [ ] `safe_rename` propaga correctamente a modelo, DAX, report bindings y M.
- [ ] `safe_rename` rollback atómico si cualquier step falla.
- [ ] `safe_rename` elicita si `impact_score > confirm_impact_threshold`.
- [ ] `safe_rename` detecta M references literales y log warning.
- [ ] `safe_rename` preserva alt text en visuales (no rompe accesibilidad).
- [ ] `safe_rename` post-rename: BPA score delta <5 puntos.
- [ ] `safe_rename` con `dry_run=true` no toca archivos, solo devuelve diff estimado.
- [ ] Tests unitarios: 1 test por step + 1 test de rollback end-to-end.
- [ ] Tests integration: 1 test con fixture PBIP real.

## 7. Out of scope (MVP)

- � Rename en `.pbix` cerrado sin Desktop abierto (requiere Super BI).
- ❌ Rename cross-model (entre datasets relacionados).
- ❌ Auto-fix de M references detectadas (solo warning en MVP).
- ❌ Batch rename (múltiples objetos en un solo call) → v2.

## 8. Riesgos

| Riesgo | Mitigación |
|--------|-----------|
| Regex reference miss (visual con path no estándar) | Multi-strategy: queryRef, Column reference, Expression, sortBy, filter. Tests con fixtures diversos. |
| Rollback parcial | Snapshot ANTES de todo; rollback step por step; nunca continuar si rollback falla. |
| Performance: >500 visuals a actualizar | Progress reporting + cancel possible. Considerar chunking. |
| Alt text preservation | Audit post-rename valida que alt text no se rompió (no fue editado). |
| M files con sintaxis custom | Linter M regex básico; warning si no parsea. |
| TMSL script mal formado para Fabric target | Validación previa; fallback a deploy completo si TMSL falla. |

## 9. Specs relacionados

- [`../01-orchestrator.md`](../01-orchestrator.md) — planner + rollback engine + elicitation
- [`../03-validation.md`](../03-validation.md) — DAX linter + BPA + WCAG
- [`../05-engines-adapters.md`](../05-engines-adapters.md) — ModelingEngine + ReportEngine
- [`../workflows/02-refactor-to-calc-groups.md`](../workflows/02-refactor-to-calc-groups.md) — usuario de safe_rename
