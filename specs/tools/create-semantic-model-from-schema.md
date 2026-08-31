# Spec: Tool `create_semantic_model_from_schema` (v2) — OUTLINE

> Genera un modelo semántico TMDL completo desde una spec declarativa
> (YAML/JSON) describiendo tablas, columnas, relationships, medidas y
> jerarquías. Es el input del workflow 01 en su forma showcase (zero
> intervención manual).

**Status:** v0.1 (outline — sem 6-8 de implementación)
**Versión:** v2
**Capa:** 1 (Modelo)

---

## Objetivo

Reemplazar la creación manual del modelo estrella (escribir TMDL a
mano o configurar columnas una por una en Desktop) por una spec
declarativa que el agente o el usuario escribe, y el orquestador
materializa como PBIP listo para abrir en Desktop.

Diferencia con `add_measure_with_validation` (v1.1): ese añade
medidas sueltas a un modelo existente. Este crea el modelo desde cero.

## Inputs principales

- `spec`: YAML/JSON con la estructura del modelo. Ejemplo:
  ```yaml
  name: sales_v1
  description: "Sales star schema"
  tables:
    - name: FactSales
      columns:
        - {name: SaleKey, type: int64, is_key: true}
        - {name: DateKey, type: int64}
        - {name: ProductKey, type: int64}
        - {name: RegionKey, type: int64}
        - {name: Units, type: int64, format_string: "#,##0"}
        - {name: UnitPrice, type: decimal, format_string: "$#,##0.00"}
      measures:
        - {name: "Total Sales", expression: "SUM(FactSales[Units] * FactSales[UnitPrice])"}
        - {name: "YTD Sales", expression: "TOTALYTD([Total Sales], 'DimDate'[Date])"}
    - name: DimDate
      columns: [...]
      is_date_table: true
      date_column: Date
    - name: DimProduct
      columns: [...]
    - name: DimRegion
      columns: [...]
  relationships:
    - {from: "DimDate[Date]", to: "FactSales[DateKey]", cardinality: many_to_one, cross_filter: single, is_active: true}
    - {from: "DimProduct[ProductKey]", to: "FactSales[ProductKey]", cardinality: many_to_one}
    - {from: "DimRegion[RegionKey]", to: "FactSales[RegionKey]", cardinality: many_to_one}
  hierarchies:
    - {table: DimDate, name: "Fiscal", levels: [Year, Quarter, Month, Date]}
  default_format_strings: {...}
  ```
- `output_pbip_path`: dónde escribir el PBIP resultante.
- `data_source`: opcional (CSV path o SQL connection string). Si se
  provee, genera también los pasos de Power Query.
- `dry_run`: bool, default true (devolver spec validada sin escribir).

## Outputs principales

- `pbip_path`: path al PBIP creado.
- `tables_created`: lista con `table_name`, `columns_count`, `measures_count`.
- `relationships_created`: lista.
- `validation`: BPA + pbip-validator result.
- `lint`: DAX linter sobre las measures.
- `rollback_handle`: snapshot del directorio de output (si existía).

## Dependencias

- [`../01-orchestrator.md`](../01-orchestrator.md) — `plan_change` + `apply_plan` con rollback.
- [`../03-validation.md`](../03-validation.md) — BPA + DAX linter inline.
- [`../05-engines-adapters.md`](../05-engines-adapters.md) — modeling engine para crear el TMDL.
- [`../tools/add-measure-with-validation.md`](../add-measure-with-validation.md) — v1.1, para validación inline de cada measure.
- Type system Pydantic estricto para la spec (ver §`Spec schema` abajo).

## Acceptance criteria

- [ ] Spec YAML válido produce PBIP que abre en Desktop sin warnings.
- [ ] `pbip-validator model` retorna exit 0 sobre el PBIP generado.
- [ ] DAX linter detecta anti-patterns en las measures de la spec antes de aplicarlas.
- [ ] Spec inválida (ej. relationship a columna inexistente) elicita con `remediation_hint` claro.
- [ ] Rollback atómico: si falla mid-creación, no quedan archivos parciales.
- [ ] Workflow 01 puede correr de CSV a publicado con UN solo prompt del usuario (showcase completo).

## Riesgos / open questions

- **Type inference de columnas**: si la spec no incluye `type`, ¿intentamos inferirlo del `data_source` (sample del CSV)? Propuesta: en v2, exigir type explícito (mejor DX que magic).
- **Power Query autogenerado**: ¿cuán complejos son los pasos M que generamos desde `data_source`? Default: 1 paso trivial (`Source = Csv.Document(file)`); transformaciones más complejas las hace el usuario después.
- **Naming conventions**: ¿imponemos snake_case / PascalCase / camelCase? Propuesta: flexible con warning si mezcla estilos.
- **Spec versioning**: la spec YAML también debería tener schema_version (igual que plan YAML, ver §5.1 de `01-orchestrator.md`).
- **Multi-modelo desde una spec**: ¿genera uno o varios modelos? Default: uno.
- **Compatibilidad con Tabular Editor**: el TMDL generado debe abrir en TE y roundtrippear sin warnings.

## Fuera de alcance (v2)

- ❌ Inferencia de schema desde CSV sin spec (eso es AI-assisted modeling, v4+).
- ❌ Generación de particiones / incremental refresh.
- ❌ Calculation groups (eso es `refactor_to_calculation_groups`).
- ❌ Roles RLS (eso es `setup_rls_and_roles`).
- ❌ Dataflows / datasets en Fabric (solo modelo local en PBIP).

---

## Spec completo

Detalle (Pydantic models estrictos de la spec, generación del TMDL,
integración con Power Query autogenerado, versionado de la spec) en
Semana 6-8.
