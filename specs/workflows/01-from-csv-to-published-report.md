# Workflow: De CSV a Reporte Publicado con RLS

> Flujo end-to-end #1. Un solo prompt del agente → modelo estrella → DAX básico
> → RLS → reporte ejecutivo mobile-friendly → publicado en Fabric con refresh.

**Status:** v0.2 (spec — corregido tras audit 2026-08-21)
**Prioridad:** P0 — showcase del MVP **completo** (algunas subpartes dependen de tools v2)
**Responsable:** codehak
**Depende de:**
- Tools MVP v1: `connect_target`, `plan_change`, `apply_plan`, `add_measure_with_validation`, `create_report_from_dataset`, `edit_report_visual`, `safe_rename`, `audit_model_and_report`, `apply_theme_and_accessibility_rules`, `pre_deploy_check`, `deploy_to_workspace`, `run_refresh`, `generate_data_dictionary`
- Tools v2 (requeridos para showcase completo): `create_semantic_model_from_schema`, `setup_rls_and_roles`, `design_report_page_from_requirements` — ver §9 "Alcance MVP alcanzable" abajo
- [`../01-orchestrator.md`](../01-orchestrator.md) — orquestación
- [`../02-cloud-fabric.md`](../02-cloud-fabric.md) — cloud
- [`../03-validation.md`](../03-validation.md) — validación
- [`../04-viz-ux.md`](../04-viz-ux.md) — viz/UX
- [`../05-engines-adapters.md`](../05-engines-adapters.md) — engines
- [`../tools/safe-rename.md`](../tools/safe-rename.md), [`audit-model-and-report.md`](../tools/audit-model-and-report.md), [`deploy-to-workspace.md`](../tools/deploy-to-workspace.md)

---

## 1. Objetivo

Demostrar que un solo prompt del usuario puede llevar desde un CSV local
hasta un reporte publicado en Fabric con RLS, validado y con auditoría.

**Caso de uso real:**

> Tengo `ventas_2025.csv` con columnas `fecha, region, producto, unidades,
> precio_unitario`. Quiero:
>
> 1. Modelo estrella en Fabric (DimDate, DimProduct, DimRegion, FactSales).
> 2. Medidas DAX: Total Sales (SUM), YTD Sales, MoM% (mes a mes),
>    Top 10 Products, Sales by Region.
> 3. RLS por región: 3 roles (Region-West, Region-East, Region-Central)
>    con gerentes específicos.
> 4. Reporte ejecutivo mobile-friendly con 4 visuales: KPI YTD,
>    MoM% trend, Top 10 products, Sales by Region.
> 5. Deploy a workspace 'Analytics-Dev' con refresh diario 6AM.
> 6. Que pase quality gate antes de publicar.

**Resultado esperado:**

- PBIP local listo para abrir en Desktop.
- Workspace Fabric con dataset + report + refresh schedule.
- RLS matrix con pass/fail por rol.
- Audit score ≥80.
- Data dictionary Markdown.

---

## 2. Tools del MCP utilizadas

| # | Tool | Capa | Propósito |
|---|------|------|-----------|
| 1 | `connect_target` | 6 | Abrir contexto PBIP local + detectar workspace Fabric. |
| 2 | `plan_change` × 5 | 6 | Planificar cada fase. |
| 3 | `apply_plan` × 5 | 6 | Ejecutar cada fase con rollback. |
| 4 | `create_semantic_model_from_schema` | 1 | Generar PBIP desde spec (MVP: scaffold manual + modeling engine). |
| 5 | `add_measure_with_validation` × 5 | 1 | Añadir medidas con lint DAX + runtime check. |
| 6 | `setup_rls_and_roles` | 1+3 | Crear roles + asignar members + RLS matrix test. |
| 7 | `design_report_page_from_requirements` | 2+5 | (v2 — en MVP se hace manual con `create_report_from_dataset` + `edit_report_visual`). |
| 8 | `create_report_from_dataset` | 2 | Scaffold PBIR. |
| 9 | `apply_theme_and_accessibility_rules` | 4+5 | WCAG + theme. |
| 10 | `audit_model_and_report` | 4+5 | Audit completo. |
| 11 | `pre_deploy_check` | 4 | Gate antes de deploy. |
| 12 | `deploy_to_workspace` | 3 | Publicar. |
| 13 | `run_refresh` | 3 | Validar refresh. |
| 14 | `generate_data_dictionary` | 1 | Documentación. |

---

## 3. Pasos internos (5 fases)

### Fase 1 · Conectar y entender datos

```
[Agente]
"Para el workflow, primero conectá al PBIP ./out/sales.pbip y
sampleá 100 filas de ./data/ventas_2025.csv para entender el shape."

[Capa 6 · orquestación]
plan_change(intent="connect_target + sample_data")
apply_plan
  ├─ step 1: connect_target(pbip_path=./out/sales.pbip)
  │  └─ engines_available: [powerbi-modeling-mcp 0.1.9 ✅, superbi ❌ no Windows, te ✅]
  └─ step 2: read_csv_sample(./data/ventas_2025.csv, n=100)
     └─ inferred_schema: {fecha:date, region:string, producto:string, unidades:int, precio_unitario:float}

[Output]
{
  session_id: "sess_abc123",
  engines_available: {"powerbi-modeling-mcp": "0.1.9 ✅", "te": "✅"},
  csv_sample_summary: {rows: 100, columns: 5, inferred_types: {...}}
}
```

### Fase 2 · Crear modelo estrella

```
[Agente]
"Creá el modelo estrella: DimDate (auto-generada 2020-2030),
DimProduct, DimRegion, FactSales con relationships Dim* → FactSales.
Configurá la DimDate como tabla de fechas."

[Capa 6]
plan_change(template="create_semantic_model_from_schema", args={...})
apply_plan
  ├─ step 1: load_csv_to_pbip(./data/ventas_2025.csv → FactSales table)
  ├─ step 2: create_table(DimDate, generated 2020-2030, marked_as_date_table=True)
  ├─ step 3: create_table(DimProduct, distinct productos from FactSales)
  ├─ step 4: create_table(DimRegion, distinct regiones from FactSales)
  ├─ step 5: create_relationships(4)
  │  - DimDate[Date] → FactSales[fecha] (single, both)
  │  - DimProduct[Product] → FactSales[producto] (single, both)
  │  - DimRegion[Region] → FactSales[region] (single, both)
  ├─ validator: BPA + pbip_validate → score 85
  └─ snapshot auto
```

### Fase 3 · Crear medidas DAX + RLS

```
[Agente]
"Añadí las medidas DAX con validación, y los 3 roles RLS."

[Capa 6]
plan_change(template="add_measure_with_validation + setup_rls_and_roles")

apply_plan (medidas)
  ├─ step 1: add_measure(Total Sales = SUM(FactSales[precio_unitario] * FactSales[unidades]))
  │  └─ lint: ✅, runtime check: ✅
  ├─ step 2: add_measure(YTD Sales = TOTALYTD([Total Sales], 'DimDate'[Date]))
  │  └─ lint: ✅ (warning: nested CALCULATE implicit via TOTALYTD), runtime: ✅
  ├─ step 3: add_measure(MoM% = DIVIDE([Total Sales] - [Prior Month], [Prior Month]))
  │  └─ requires measure "Prior Month" → crear primero
  ├─ step 4: add_measure(Prior Month = CALCULATE([Total Sales], DATEADD('DimDate'[Date], -1, MONTH)))
  ├─ step 5: add_measure(Top 10 Products by Sales = ...)
  ├─ step 6: add_measure(Sales by Region = ...)
  └─ validator: BPA + dax_lint + regression baseline (auto) → score 88

apply_plan (RLS)
  ├─ step 1: create_security_role(Region-West, filter=[DimRegion[Region]="West")
  ├─ step 2: create_security_role(Region-East, filter=[DimRegion[Region]="East")
  ├─ step 3: create_security_role(Region-Central, filter=[DimRegion[Region]="Central")
  ├─ step 4: assign_role_members(Region-West, ["gerente.west@acme.com"])
  ├─ step 5: assign_role_members(Region-East, ["gerente.east@acme.com"])
  ├─ step 6: assign_role_members(Region-Central, ["gerente.central@acme.com"])
  ├─ step 7: rls_test_matrix(query=[Total Sales] por Region)
  │  - Region-West: {West: 100K, East: 0, Central: 0} → pass
  │  - Region-East: {West: 0, East: 150K, Central: 0} → pass
  │  - Region-Central: {West: 0, East: 0, Central: 120K} → pass
  └─ result: 3/3 passed
```

### Fase 4 · Crear reporte + viz/UX

```
[Agente]
"Creá el reporte ejecutivo mobile con los 4 visuales y aplicá theme + WCAG."

[Capa 6]
plan_change(template="create_report_from_dataset + apply_theme_and_accessibility_rules")

apply_plan (reporte)
  ├─ step 1: create_page(Overview)
  ├─ step 2: add_visual(KPI Card, fields=[YTD Sales], format="$#,##0")
  ├─ step 3: add_visual(Line Chart, fields=[DimDate[Month], MoM%])
  ├─ step 4: add_visual(Bar Chart horizontal, fields=[Top 10 Products by Sales])
  ├─ step 5: add_visual(Bar Chart, fields=[DimRegion[Region], Total Sales])
  ├─ step 6: configure_mobile_layout(stack vertical, gap=12px)
  └─ validator: pbir_validate → ✅

apply_plan (theme + WCAG)
  ├─ step 1: generate_theme(palette="colorblind_safe_okabe_ito", wcag_level=AA)
  ├─ step 2: apply_theme(report.json + visual themes)
  ├─ step 3: add_alt_text_to_visuals(pattern="{visualType} de {measure}")
  ├─ step 4: configure_tab_order(logical: KPI → Trend → Top → Region)
  ├─ step 5: mark_decorative_as_hidden(tabOrder=-1)
  └─ validator: wcag_audit → score 95
```

### Fase 5 · Audit + Deploy + Refresh

```
[Agente]
"Audit final + pre-deploy gate + deploy + refresh."

[Capa 6]
plan_change(template="audit + pre_deploy + deploy + refresh")

apply_plan
  ├─ step 1: audit_model_and_report(target=pbip, checks=all MVP)
  │  - overall_score: 87
  │  - findings: 3 warnings (all auto-fixable)
  ├─ step 2: elicitation "¿Aplicar 3 auto-fixes?"
  │  - user: "sí"
  ├─ step 3: apply_auto_fixes
  │  - fix 1: BPA naming convention (DimDate → Date dimension naming)
  │  - fix 2: alt text default en 2 visuales
  │  - fix 3: tab order sugerido
  ├─ step 4: audit again → overall_score: 92 ✅
  ├─ step 5: pre_deploy_check(profile=standard) → passed
  ├─ step 6: elicitation "Deploy a workspace 'Analytics-Dev' (no production tag). ¿Continuar?"
  │  - user: "sí"
  ├─ step 7: deploy_to_workspace(pbip, workspace_id, create_dataset+report, refresh daily 6AM)
  │  - dataset_id: ds_xxx
  │  - report_id: rpt_xxx
  │  - refresh_schedule_id: sched_xxx
  ├─ step 8: run_refresh(wait=true, timeout=30min) → status: Completed
  ├─ step 9: generate_data_dictionary(format=markdown)
  │  - path: ./out/sales_model_dict.md
  │  - coverage_score: 0.85 (description faltante en 3 columns → elicitar)
  ├─ step 10: audit_log entry (HMAC chain)
  └─ rollback_handle: snap_2026-08-21_full
```

### Resultado final

```yaml
result: success
overall_score: 92
deploy:
  workspace: Analytics-Dev
  dataset_id: ds_xxx
  report_id: rpt_xxx
  refresh_schedule: daily 6AM UTC-4
  refresh_status: Completed
rls:
  matrix: 3/3 passed
artifacts:
  - ./out/sales.pbip/ (PBIP completo)
  - ./out/sales_model_dict.md (data dictionary)
duration_ms: ~250000  # ~4 min
```

---

## 4. Puntos de validación / rollback

| Step | Validation | Rollback |
|------|-----------|----------|
| Conectar | engines health check | N/A (read-only) |
| Crear modelo | BPA + pbip-validator | snapshot pre-step |
| Crear medidas | DAX linter + runtime check | snapshot pre-step |
| Crear RLS | RLS matrix test | snapshot pre-step |
| Crear reporte | pbir_validate | snapshot pre-step |
| Theme + WCAG | WCAG audit | snapshot pre-step |
| Audit | overall score | N/A (read-only) |
| Auto-fix | re-audit | rollback to pre-fix snapshot |
| Pre-deploy | pre_deploy_check | N/A (read-only) |
| Deploy | REST API success | take_over revert (limitado) |
| Refresh | wait_until_completed | cancel_refresh |

---

## 5. Cómo se asegura la calidad de la visualización

- **Audiencia = executive** → layout F-pattern, KPI grande arriba-izquierda.
- **Mobile-first** → stack vertical, ≤4 visuales/página en mobile.
- **Paleta colorblind-safe** (Okabe-Ito) → no rojo/verde solo.
- **Contraste WCAG AA** verificado en theme generator.
- **Tab order lógico** (KPI → Trend → Top → Region).
- **Alt text auto** en cada visual.
- **Performance budget** estimado <5000ms (target).
- **StoryTelling score** ≥70.

---

## 6. Acceptance criteria

**MVP v1 alcanzable (solo con tools MVP):**
- [ ] Workflow end-to-end ejecuta sin intervención adicional más allá de las elicitations listadas.
- [ ] PBIP resultante se abre en Power BI Desktop sin warnings.
- [ ] Dataset publicado se refresca correctamente.
- [ ] Overall audit score ≥80.
- [ ] Data dictionary generado con coverage ≥80%.
- [ ] Rollback atómico funciona si cualquier fase falla.

**Requiere tools v2 (no MVP):**
- [ ] RLS matrix 3/3 passed (depende de `setup_rls_and_roles` v2).
- [ ] Scaffold automático del modelo estrella desde spec (depende de `create_semantic_model_from_schema` v2).
- [ ] Diseño automático de página con 4 visuales desde brief NL (depende de `design_report_page_from_requirements` v2).

Sin los tools v2, el flujo MVP se completa vía **scaffold manual + `add_measure_with_validation` para medidas** + **scaffold manual del reporte con `create_report_from_dataset` + `edit_report_visual`**. La orquestación, validación, deploy, refresh, theme/WCAG y data dictionary sí son 100% MVP.

## 9. Alcance MVP alcanzable vs showcase completo

Esta distinción se documenta porque el workflow 01 mezcla MVP y v2. Para no inducir a error:

**MVP alcanzable sin v2 (~80% del flujo):**

| Fase | MVP alcanzable | Limitación |
|------|---------------|-----------|
| 1. Conectar | ✅ 100% | — |
| 2. Modelo estrella | ⚠️ scaffold manual | Schema debe venir pre-armado por el agente en formato YAML; `add_measure_with_validation` sí funciona, pero el scaffold del esqueleto (tablas, relaciones) es manual. |
| 3. Medidas + RLS | ✅ Medidas 100% | ❌ RLS matrix testeada con `run_dax_regression` + EffectiveIdentity vía REST `Execute Queries` — `setup_rls_and_roles` v2 lo automatiza, MVP requiere setup manual del test. |
| 4. Reporte + viz/UX | ⚠️ scaffold manual | `create_report_from_dataset` crea el PBIR con página vacía; `edit_report_visual` agrega visuales uno a uno. `design_report_page_from_requirements` v2 lo haría desde NL. Theme + WCAG sí son 100% MVP. |
| 5. Audit + Deploy + Refresh | ✅ 100% | — |

**Showcase completo (con v2):** el agente hace todo en un solo prompt, sin intervención manual adicional.

**Recomendación para MVP done (SPEC §6.4):** validar el sub-flujo MVP alcanzable, no el showcase completo. El showcase completo es acceptance criteria de v2.

## 7. Riesgos

| Riesgo | Mitigación |
|--------|-----------|
| CSV malformado | Validación previa con sample; elicitar al usuario si headers faltan. |
| Cardinalidad muy alta (>10K productos) | Warning + sugerencia de jerarquía. |
| Refresh timeout en deploy | Default 30 min; warning si dataset >500MB. |
| RLS rule incorrecta | rls_test_matrix valida con queries de prueba antes de deploy. |
| SPN sin scopes de write | Elicitación con scopes requeridos antes de deploy. |

## 8. Specs relacionados

- [`../01-orchestrator.md`](../01-orchestrator.md) — orquestación
- Todos los tools MVP
