# Fixture PBIP — `tests/fixtures/sample.pbip/`

> Especificación canónica del PBIP de prueba usado por todos los tests
> e2e del servidor. **Cobertura amplia** (~15 medidas, ~30 columnas,
> 4 visuales) para que cubra los 10 anti-patterns del DAX linter, los
> casos de WCAG, y los rename patterns sin necesidad de mocks
> adicionales.

**Status:** v0.1 (spec — fixture aún no generado; vive en spec hasta
Semana 2 cuando se materializa)

**Owner:** codehak
**Versión del fixture:** `0.1.0`
**Última actualización:** 2026-08-26

---

## 1. Por qué un spec del fixture

El fixture es **load-bearing**: lo usan tests de Semana 2 (`safe_rename`),
Semana 3 (`audit_model_and_report`, `deploy_to_workspace`), Semana 4
(`run_dax_regression`, `diff_models`, `apply_theme_and_accessibility_rules`,
`generate_data_dictionary`), y los workflows 01 + 02. Si el fixture se
diseña ad-hoc por cada test, diverge y los tests se rompen entre sí.

**Decisión:** un único fixture versionado, generado por un script
determinístico (no a mano en Power BI Desktop), con `fixtures_version`
explícito en metadata y tests que dependen de IDs específicos los
fijan en su setup.

---

## 2. Estructura de carpetas

```
tests/fixtures/sample.pbip/
├── sample.pbip                          # metadata file (Power BI Project)
├── sample.Dataset/
│   ├── definition.pbism                # semantic model definition
│   ├── model.tmdl                       # legacy TMDL fallback
│   └── diagramLayout.json               # visual layout in Desktop
└── sample.Report/
    ├── definition.pbir                  # report definition
    ├── report.json                      # legacy report
    └── pages/
        ├── Overview/page.json           # página principal
        └── Detail/page.json             # página secundaria (opcional, MVP: solo Overview)
```

**Generación:** vía script `tests/fixtures/generate.py` (idempotente;
mismo seed → mismo output). El script crea un PBIP mínimo válido que
`pbip-validator model` y `pbip-validator pbir` aceptan.

**Seed:** `42` (default; configurable via `--seed` para variantes de
tests que quieran cambiar cardinalidades).

---

## 3. Modelo estrella (4 tablas)

### 3.1 `DimDate`

Tabla de fechas marcada como "date table" (`isDateTable=true`).

| Columna | Tipo | Notas |
|---------|------|-------|
| `Date` | `dateTime` | PK, marcada como `dataCategory=PaddedDateTableDates` |
| `Year` | `int64` | Calculada: `YEAR([Date])` |
| `Quarter` | `string` | Calculada: `"Q" & QUARTER([Date])` |
| `Month` | `int64` | Calculada: `MONTH([Date])` |
| `MonthName` | `string` | Calculada: `FORMAT([Date], "MMM")` |
| `Day` | `int64` | Calculada: `DAY([Date])` |
| `IsWeekend` | `boolean` | Calculada: `WEEKDAY([Date], 2) > 5` |

**Rows:** 4018 filas (2020-01-01 a 2030-12-31).

**Description:** "Date dimension spanning 11 years for time intelligence."

### 3.2 `DimProduct`

| Columna | Tipo | Notas |
|---------|------|-------|
| `ProductKey` | `int64` | PK, autoincremental |
| `ProductName` | `string` | |
| `Category` | `string` | "Beverages", "Bakery", "Condiments", ... (5 categorías fijas) |
| `Subcategory` | `string` | |
| `Color` | `string` | Color del packaging |
| `UnitPrice` | `decimal` | Precio base |

**Rows:** 10 productos fijos (seeded). Cardinalidad baja intencional para
tests rápidos.

**Description:** "Product dimension with 10 SKUs across 5 categories."

### 3.3 `DimRegion`

| Columna | Tipo | Notas |
|---------|------|-------|
| `RegionKey` | `int64` | PK |
| `Region` | `string` | "West", "East", "Central" (3 fijas para RLS test) |
| `Country` | `string` | "USA" |
| `ManagerEmail` | `string` | Marcada con PII flag (description con "PII: email del gerente regional") |

**Rows:** 3 regiones.

**Description:** "Regional dimension for sales territory analysis. ManagerEmail contains PII."

### 3.4 `FactSales`

| Columna | Tipo | Notas |
|---------|------|-------|
| `SaleKey` | `int64` | PK |
| `DateKey` | `int64` | FK → `DimDate[Date]` |
| `ProductKey` | `int64` | FK → `DimProduct[ProductKey]` |
| `RegionKey` | `int64` | FK → `DimRegion[RegionKey]` |
| `Units` | `int64` | Cantidad vendida |
| `UnitPrice` | `decimal` | Precio al momento de venta |
| `Discount` | `decimal` | Porcentaje (0.0 a 0.3) |
| `TotalAmount` | `decimal` | Calculada: `[Units] * [UnitPrice] * (1 - [Discount])` |

**Rows:** 5000 ventas (seeded con distribución realista: 3 regiones × 10
productos × ~166 días × densidad variable).

**Description:** "Transactional sales fact table."

### 3.5 Relationships

| From | To | Cardinality | Cross filter | Active |
|------|----|----|----|----|
| `DimDate[Date]` | `FactSales[DateKey]` | many:1 | single | ✅ |
| `DimProduct[ProductKey]` | `FactSales[ProductKey]` | many:1 | single | ✅ |
| `DimRegion[RegionKey]` | `FactSales[RegionKey]` | many:1 | single | ✅ |

---

## 4. Medidas (~15 — 5 limpias + 10 con anti-patterns)

### 4.1 Limpias (5)

Estas medidas siguen best-practices y NO disparan el DAX linter.

```dax
Total Sales := SUM(FactSales[TotalAmount])

YTD Sales := TOTALYTD([Total Sales], 'DimDate'[Date])

Prior Month Sales := CALCULATE([Total Sales], DATEADD('DimDate'[Date], -1, MONTH))

Sales by Region := CALCULATE([Total Sales], ALLEXCEPT(DimRegion, DimRegion[Region]))

Order Count := COUNTROWS(FactSales)
```

### 4.2 Con anti-patterns (10 — una por cada regla del DAX linter MVP)

Cada regla del DAX linter tiene exactamente una medida en el fixture
que la dispara, con un comment `// BP_xxx` referenciando el rule_id.

```dax
// BP_FILTER_ISFILTER: FILTER sin ISFILTER dentro de CALCULATE
Filtered Sales Bad :=
CALCULATE (
    [Total Sales],
    FILTER ( ALL ( 'DimProduct' ), 'DimProduct'[Category] = "Beverages" )
)

// BP_CALCULATE_NESTED: CALCULATE anidado 3+ niveles
Deep Calculate :=
CALCULATE (
    CALCULATE (
        CALCULATE (
            [Total Sales],
            'DimProduct'[Category] = "Bakery"
        ),
        'DimRegion'[Region] = "West"
    ),
    'DimDate'[Year] = 2025
)

// BP_DIVIDE_VS_SLASH: usa / en vez de DIVIDE
Avg Price Bad :=
SUM ( FactSales[UnitPrice] ) / SUM ( FactSales[Units] )

// BP_IFERROR_MISUSE: IFERROR envolviendo operación que no falla
Safe Division Bad :=
IFERROR ( [Total Sales] / [Order Count], 0 )

// BP_EARLIER_AVOID: uso de EARLIER en lugar de variable
Rank By Region :=
ADDCOLUMNS (
    VALUES ( DimRegion[Region] ),
    "Rank", RANKX (
        ALL ( DimRegion[Region] ),
        [Total Sales],
        ,
        ASC
    )
)
// NOTA: la implementación correcta usaría VAR; este ejemplo es
// específicamente el patrón "antiguo" para que el linter lo marque.

// BP_SUMMARIZE_FOR_AGG: SUMMARIZE usado para agregar
Sales Per Region Bad :=
SUMMARIZE (
    FactSales,
    DimRegion[Region],
    "Total", SUM ( FactSales[TotalAmount] )
)
// NOTA: lo correcto sería SUMMARIZECOLUMNS.

// BP_BLANK_SUPPRESS_PLUS_ZERO: blank suppression con + 0
Non Blank Sales :=
COUNTROWS ( FactSales ) + 0

// BP_HALLUCINATED_FUNC: función inventada que no existe en DAX
Hallucinated Measure :=
FROBNICATE ( FactSales[TotalAmount] )
// NOTA: el fixture incluye esto solo si se testea el validator de
// parsing con runtime check; si no, dejar con un comment "intentional
// parse error for tests" y excluir de default runs.

// BP_HASONEVALUE_ITERATION: HASONEVALUE sin patrón de branch
Single Region Sales :=
IF ( HASONEVALUE ( DimRegion[Region] ), [Total Sales], BLANK () )
// NOTA: este NO es el anti-pattern (es el uso correcto). El anti-pattern
// sería `IF(HASONEVALUE(...), [Total Sales])` sin la rama ELSE.

// BP_UNUSED_MEASURE: medida sin consumidores en ningún visual
Orphan Measure :=
[Total Sales] * 0.5
// Esta medida existe intencionalmente; tests verifican que `unused_measures`
// en audit la detecta.
```

**Cobertura total: 10 reglas del linter MVP** = 10 medidas con
anti-patterns.

---

## 5. Roles RLS

| Role | Filter expression | Test members |
|------|-------------------|--------------|
| `Region-West` | `[Region] = "West"` | `gerente.west@acme.test` |
| `Region-East` | `[Region] = "East"` | `gerente.east@acme.test` |
| `Region-Central` | `[Region] = "Central"` | `gerente.central@acme.test` |

**RLS matrix test (3/3 pass):** cada role logueado ve solo su región,
`Total Sales` agregado correctamente.

---

## 6. Reporte (4 visuales en página `Overview`)

| # | Visual | Type | Fields | Posición | Format |
|---|--------|------|--------|----------|--------|
| 1 | KPI Card | `card` | `[YTD Sales]` | top-left (0,0,400,150) | `$#,##0` |
| 2 | Trend Chart | `lineChart` | `DimDate[MonthName]`, `[MoM%]` | top-right (420,0,800,300) | `0.0%` |
| 3 | Top Products | `barChart` (horizontal) | `DimProduct[ProductName]`, `[Total Sales]` | bottom-left (0,320,600,400) | `$#,##0`, sort desc, top 10 |
| 4 | Sales by Region | `clusteredColumnChart` | `DimRegion[Region]`, `[Total Sales]` | bottom-right (620,320,600,400) | `$#,##0` |

### 6.1 Configuración mobile

`report.json` incluye `mobileLayout` con stacking vertical, gap 12px, en
el mismo orden.

### 6.2 WCAG baseline

Para que `apply_theme_and_accessibility_rules` tenga algo que validar:

- 1 visual SIN alt text (`#3 Top Products`) → dispara WCAG warning.
- 1 visual con alt text placeholder (`"<visual_type> de <measure>"`) → válido.
- Tab order: 1 → 2 → 3 → 4 (KPI primero, narrativamente).
- Contraste declarado: 4.5:1 en texto, 3:1 en iconos.

### 6.3 Theme

`theme.json` mínimo con paleta `colorblind_safe_okabe_ito` (8 colores):
black, orange, skyBlue, bluishGreen, yellow, blue, vermillion, reddishPurple.

---

## 7. Errores DAX cubiertos

El fixture incluye 1 medida que rompe en runtime intencionalmente para
que `safe_rename` y el DAX linter puedan testear el flujo "detectar
antes de aplicar":

```dax
Broken Measure :=
CALCULATE ( [Nonexistent Measure], ALL ( FactSales ) )
// [Nonexistent Measure] no existe → runtime error.
```

Tests que dependen de "no fallar el parse del modelo" deben marcar esta
medida con `isHidden=true` o excluirla via query filter. Documentado
en `tests/fixtures/excluded_measures.json`.

---

## 8. Columnas PII

- `DimRegion[ManagerEmail]` flagged como PII (description contiene "PII").
- `FactSales` no tiene columnas PII directas (no hay customer_id por
  simplicidad; podría agregarse si un test lo requiere).

`generate_data_dictionary` usa el PII flag para redactar en el output
MarkDown.

---

## 9. Description coverage

Para que el data dictionary tenga algo que reportar:

- **80% de columnas tienen description** (24 de ~30).
- **20% NO tienen description** (6 de ~30) → data dictionary coverage
  score ≈ 80%, lo que dispara la elicitation opcional "completar
  descriptions".
- **Tablas tienen description:** todas (4/4).

Las 6 columnas sin description están pre-listadas en
`tests/fixtures/missing_descriptions.json` para que los tests assertivos
sepan cuáles son.

---

## 10. Versionado del fixture

```python
# tests/fixtures/__init__.py
FIXTURE_VERSION = "0.1.0"
FIXTURE_PATH = Path(__file__).parent / "sample.pbip"
```

**Reglas:**

1. Cualquier cambio al contenido del fixture bump a `0.1.X` (patch).
2. Cambio estructural (tablas/medidas nuevas) bump a `0.X.0` (minor).
3. Cambio incompatible (semántica de columnas cambiadas) bump a `X.0.0`.
4. Tests que dependen de IDs específicos (`Order Count == 1234`,
   `Total Sales for 2025 == $X`) los hardcodean en su setup Y verifican
   que `FIXTURE_VERSION` coincide con la asumida.
5. Si `FIXTURE_VERSION` cambia → actualizar todos los tests que
   hardcodean valores derivados. El script `tests/fixtures/generate.py`
   es la única fuente de verdad.

---

## 11. Generación del fixture

```bash
python -m tests.fixtures.generate \
    --output tests/fixtures/sample.pbip \
    --seed 42
```

**Idempotencia:** correr 2 veces con mismo seed produce output byte-idéntico
(verificable con `diff -r`).

**Performance:** generación <5s; tamaño total <500KB.

---

## 12. Acceptance criteria

- [ ] Script `tests/fixtures/generate.py` existe y produce el fixture según esta spec.
- [ ] `pbip-validator model sample.pbip` retorna exit 0 (modelo válido).
- [ ] `pbip-validator pbir sample.pbip` retorna exit 0 (reporte válido).
- [ ] `audit_model_and_report(sample.pbip)` retorna:
  - BPA score ≥80.
  - WCAG score ≥70 (con el warning intencional del Top Products visual).
  - Linter detecta las 10 medidas con anti-patterns.
  - Coverage del data dictionary ≈80%.
- [ ] `safe_rename(DimProduct[Category] → DimProduct[ProductCategory])` propaga a ≥5 medidas + 2 visuales.
- [ ] RLS matrix test 3/3 passed con `run_dax_regression`.
- [ ] Rollback de rename restaura estado byte-idéntico al pre-rename (verificable con `diff -r`).
- [ ] Tests de crash recovery (Semana 1 §2.8) usan este fixture como target.

---

## 13. Riesgos

| Riesgo | Mitigación |
|--------|-----------|
| Fixture diverge entre branches | Script determinístico; CI genera el fixture en cada run antes de tests e2e. |
| Fixture demasiado grande → tests lentos | Sampling opcional para tests que no requieren coverage total (default: 100% del fixture). |
| IDs hardcoded en tests se rompen al bumpear | Test setup asserta `FIXTURE_VERSION` antes de correr; si cambia, fail con mensaje claro. |
| Medida con función inventada (`FROBNICATE`) rompe parse | Marcada `isHidden=true` por default; tests que la usen explícitamente la unhidean. |
| Generar el fixture requiere Python libs pesadas (`pbip-validator`) | Script usa solo stdlib + `pydantic` + `pyyaml`; libs pesadas solo en tests de validación post-generación. |
