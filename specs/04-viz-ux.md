# Spec: Visualización y UX (Capa 5)

> Capa 5 — diferenciador. Visual registry, suggester de visuales, layout
> optimizer, theme generator, storytelling scorer, performance budget.

**Status:** v0.1 (spec)
**Prioridad:** P0 — gap real, nadie lo cubre
**Responsable:** codehak
**Depende de:** [`03-validation.md`](./03-validation.md) (WCAG rules compartidos)
**Habilita:** [`tools/apply-theme-and-accessibility-rules.md`](./tools/apply-theme-and-accessibility-rules.md), `optimize_report_performance`, `audit_report_ux_and_storytelling`, v2: `design_report_page_from_requirements`, `select_visuals_for_kpis`

**Spec relacionado:** [`docs/architecture.md`](../docs/architecture.md) §2.6

---

## Cambios respecto a v0.0

N/A (spec inicial).

---

## 1. Objetivo

Proveer razonamiento de **diseño** sobre visualización y UX, no solo de
**validez**. Sugerir visuales según el shape de los datos + audiencia,
auditar accesibilidad WCAG, generar layouts mobile-friendly, y dar
herramientas que el LLM use para tomar decisiones informadas en lugar de
"inventar" un reporte.

**Métricas de éxito:**
- `apply_theme_and_accessibility_rules` resuelve ≥80% de findings WCAG
  básicos sin intervención humana.
- `optimize_report_performance` detecta el 90% de visuales problemáticos
  en spot-check manual.
- Sugerencias de visuales siguen guidelines de SQLBI + PBI documentation.

---

## 2. Componentes

### 2.1 `visual_registry.py` — Catálogo de visuales

```python
# src/viz/visual_registry.py
class VisualRegistry:
    VISUALS: ClassVar[dict[str, VisualSpec]] = {...}  # 52+ visuales nativos

class VisualSpec(BaseModel):
    type_id: str  # ej: "barChart", "lineChart", "card"
    display_name: str
    category: Literal[
        "comparison", "trend", "composition", "distribution",
        "kpi_single_value", "tabular", "map", "custom",
    ]
    required_roles: list[RoleSpec]
    optional_roles: list[RoleSpec]
    supported_properties: dict[str, PropertySpec]
    formatting_options: dict[str, FormattingSpec]
    pbir_schema_version: str  # ej: "2.9.0"
    color_safe_default: bool  # True si default colors son colorblind-safe
    mobile_friendly: bool
    accessibility_notes: list[str]

class RoleSpec(BaseModel):
    name: str  # ej: "Category", "Values", "Legend", "Tooltips"
    data_types: list[Literal["string", "number", "date", "boolean"]]
    cardinality: Literal["single", "many"]
    aggregation: Literal["none", "sum", "avg", "count", "min", "max"] | None

class PropertySpec(BaseModel):
    path: str  # ej: "categoryAxis.fontSize"
    type: Literal["string", "number", "color", "boolean", "enum"]
    enum_values: list[str] | None
    min: float | None
    max: float | None
    description: str
```

**Visuales nativos cubiertos MVP:**

| Categoría | Visuales |
|-----------|----------|
| Comparison | barChart, clusteredBarChart, clusteredColumnChart, lineChart, ribbonChart |
| Trend | lineChart, areaChart, stepLineChart, scatterChart (con trend) |
| Composition | donutChart, pieChart, treemap, waterfallChart, stackedBarChart, stackedColumnChart, funnelChart |
| Distribution | scatterChart, histogram, boxPlot, q_and_a |
| KPI | card, multiRowCard, kpi (status), gauge |
| Tabular | tableEx, matrix, pivotTable |
| Map | map, filledMap, arcGISMap, shapeMap |
| Decomposition | decompositionTree, keyInfluencers |
| Advanced | rScript, pythonVisual (warning: requieren config) |

**Visuales custom certified** (subset MVP): charticulator, valq, inforiver,
akvelon, dataviz.

### 2.2 `suggester.py` — Selector de visuales

```python
# src/viz/suggester.py
class VisualSuggester:
    def suggest(
        self,
        kpis: list[KpiIntent],
        data_shape: DataShape,
        audience: Audience,
    ) -> list[VisualRecommendation]: ...

class KpiIntent(BaseModel):
    name: str
    semantic_type: Literal[
        "comparison",      # comparar categorías
        "trend",           # evolución temporal
        "composition",     # parte-de-todo
        "distribution",    # dispersión estadística
        "kpi_single_value",
        "ranking",         # top-N
        "correlation",     # 2 variables numéricas
    ]
    target_field: str  # nombre del measure o columna

class DataShape(BaseModel):
    cardinality: int  # cardinalidad estimada del campo category
    hierarchy_depth: int  # 0 = flat
    time_granularity: Literal["none", "year", "quarter", "month", "day", "hour", "minute"]
    has_target: bool  # para comparison (vs target)

class Audience(Enum):
    EXECUTIVE = "executive"
    ANALYTICAL = "analytical"
    OPERATIONAL = "operational"
    MOBILE_FIRST = "mobile_first"

class VisualRecommendation(BaseModel):
    primary_visual: VisualSpec
    alternatives: list[VisualSpec]
    rationale: str  # ej: "Bar chart for category comparison with <20 categories"
    data_requirements: dict[str, Any]
    layout_suggestion: LayoutHint
```

**Heurísticas de selección (basadas en SQLBI + PBI docs):**

| Semantic type + Audience | Primary visual | Reasoning |
|---------------------------|----------------|-----------|
| kpi_single_value + executive | card (large) | Big number, max 3 per row |
| trend + any | lineChart (continuo) o barChart (discrete) | Time on X |
| comparison + executive | clusteredBarChart (horizontal) | Category labels legibles |
| comparison + analytical | clusteredColumnChart | Drill-down friendly |
| composition + executive | donutChart (≤5 cats) | Familiar pattern |
| composition + analytical | treemap (≥10 cats) o stackedBarChart | Hierarchical |
| distribution + any | boxPlot (stats) o scatterChart (correlation) | Show quartiles |
| ranking + any | barChart sorted desc | Top-N pattern |
| correlation + any | scatterChart con trend line | Reveals pattern |
| mobile_first + kpi_single_value | card (responsive) | Stacks vertically |

### 2.3 `layout.py` — Layout optimizer

```python
# src/viz/layout.py
class LayoutOptimizer:
    CANVAS_WIDTHS = {
        "desktop_16_9": 1280,
        "desktop_4_3": 1024,
        "tablet": 768,
        "mobile_portrait": 375,
    }

    def propose_layout(
        self,
        visuals: list[VisualIntent],
        audience: Audience,
        canvas: str = "desktop_16_9",
    ) -> PageLayout: ...

class PageLayout(BaseModel):
    grid: list[LayoutSlot]
    canvas_dimensions: dict[str, int]  # w, h
    mobile_layout: list[LayoutSlot] | None

class LayoutSlot(BaseModel):
    visual_id: str
    x: int
    y: int
    w: int
    h: int
    z_index: int
    alignment: Literal["left", "center", "right"]
    gap_to_neighbors: int  # px
```

**Reglas de layout:**

- **F-pattern / Z-pattern**: lectura natural top-down, left-right.
- **Jerarquía visual**: KPI más importante arriba-izquierda (mayor tamaño).
- **Densidad**: ≤8 visuales desktop, ≤4 mobile.
- **Alineación**: edges alineados entre visuales vecinos.
- **Gaps**: mínimo 8px, múltiplo de 4.
- **Mobile fallback**: reordenar a stack vertical; visuales complejos
  (matrix, scatter) → vista simplificada o detalle page.

### 2.4 `theme.py` — Theme generator

```python
# src/viz/theme.py
class ThemeGenerator:
    PALETTES = {
        "colorblind_safe_okabe_ito": [...],
        "colorblind_safe_ibm": [...],
        "colorblind_safe_viridis": [...],
        "default_powerbi": [...],
    }

    def generate(
        self,
        brand_colors: dict[str, str] | None,
        palette_name: str = "colorblind_safe_okabe_ito",
        wcag_level: Literal["AA", "AAA"] = "AA",
    ) -> ThemeJson: ...

class ThemeJson(BaseModel):
    name: str
    dataColors: list[str]
    background: str
    foreground: str
    tableAccent: str
    good: str
    neutral: str
    bad: str
    maximum: str
    minimum: str
    textClasses: dict[str, TextClass]
    visualStyles: dict[str, dict]
```

**Validación de contraste** (delegada a C4 WCAG rules):

- Texto normal: ≥4.5:1 (AA) o ≥7:1 (AAA).
- Texto grande (≥18pt): ≥3:1 (AA) o ≥4.5:1 (AAA).
- UI components: ≥3:1 (AA).
- Color semantic (positive/negative): no debe ser solo rojo/verde (colorblind).

### 2.5 `storytelling.py` — Storytelling scorer

```python
# src/viz/storytelling.py
class StorytellingScorer:
    async def score(self, pbip_path: Path) -> StorytellingScore: ...

class StorytellingScore(BaseModel):
    overall: float  # 0-100
    hierarchy: float  # ¿hay jerarquía visual clara?
    density: float  # densidad apropiada
    narrative: float  # ¿cuenta una historia?
    mobile_readiness: float
    findings: list[StorytellingFinding]
```

**Heurísticas MVP (sin análisis de datos real):**

- **Jerarquía**: primer visual del page debe ser el más grande (KPIs arriba).
- **Densidad**: ≤8 visuales desktop = 100; >15 = 0.
- **Narrativa**: page title + section headers presentes.
- **Mobile readiness**: cada visual tiene `tabOrder`, decorativos en -1.
- **Consistencia**: mismas unidades de medida entre visuales similares.

### 2.6 `performance_budget.py` — Estimador

```python
# src/viz/performance_budget.py
class PerformanceBudget:
    async def estimate(self, pbip_path: Path) -> PerformanceReport: ...

class PerformanceReport(BaseModel):
    overall_score: float  # 0-100
    estimated_load_ms: int
    hotspots: list[PerformanceHotspot]
    budget_used_pct: float

class PerformanceHotspot(BaseModel):
    visual_id: str
    est_cost: Literal["low", "medium", "high"]
    reasons: list[str]
    fix_suggestion: str
```

**Heurísticas de coste:**

- Visual usa measure con nested CALCULATE → +medium.
- Visual usa measure con FILTER sobre tabla completa → +high.
- Page tiene >10 visuals con measures complejos → +high general.
- Visual es matrix con >100k celdas potenciales → +high.
- Page tiene filtros a nivel visual redundantes con filtros report → +low.

---

## 3. Tool MVP: `apply_theme_and_accessibility_rules`

```yaml
tool_name: apply_theme_and_accessibility_rules
input_schema:
  type: object
  required: [pbip_path, theme, accessibility_rules]
  properties:
    pbip_path: {type: string}
    theme:
      type: object
      properties:
        primary: {type: string, description: "Hex color"}
        secondary: {type: string}
        accent: {type: string}
        semantic:
          type: object
          properties:
            positive: {type: string}
            negative: {type: string}
            warning: {type: string}
        background: {type: string}
        palette_name:
          type: string
          enum: [colorblind_safe_okabe_ito, colorblind_safe_ibm, colorblind_safe_viridis, default_powerbi]
    accessibility_rules:
      type: object
      properties:
        min_contrast_text: {type: number, default: 4.5}
        min_contrast_large: {type: number, default: 3}
        require_alt_text: {type: boolean, default: true}
        default_alt_text_pattern: {type: string, default: "{visualType} de {measure}"}
        default_decorative_hidden: {type: boolean, default: true}
        suggested_tab_order: {type: boolean, default: true}
    dry_run: {type: boolean, default: false}
output_schema:
  type: object
  properties:
    theme_json_path: {type: string}
    changes:
      type: array
      items:
        type: object
        properties: {file: {type: string}, property: {type: string}, before: {type: string}, after: {type: string}}
    accessibility_validation:
      type: object
      properties:
        violations_fixed: {type: integer}
        remaining_warnings: {type: array, items: {type: string}}
```

---

## 4. Tool MVP: `optimize_report_performance`

```yaml
tool_name: optimize_report_performance
input_schema:
  type: object
  required: [pbip_path]
  properties:
    pbip_path: {type: string}
    target_load_ms: {type: integer, default: 5000}
output_schema:
  type: object
  properties:
    performance_score: {type: number}
    estimated_total_load_ms: {type: integer}
    hotspots:
      type: array
      items:
        type: object
        properties:
          visual_id: {type: string}
          est_cost: {type: string, enum: [low, medium, high]}
          reasons: {type: array, items: {type: string}}
          fix_suggestion: {type: string}
```

---

## 5. Tool MVP: `audit_report_ux_and_storytelling`

```yaml
tool_name: audit_report_ux_and_storytelling
input_schema:
  type: object
  required: [pbip_path]
  properties:
    pbip_path: {type: string}
    criteria:
      type: object
      properties:
        check_storytelling: {type: boolean, default: true}
        check_accessibility: {type: boolean, default: true}
        check_density: {type: boolean, default: true}
        check_mobile: {type: boolean, default: true}
        check_consistency: {type: boolean, default: true}
output_schema:
  type: object
  properties:
    ux_score: {type: number}
    storytelling_score: {type: number}
    findings:
      type: array
      items:
        type: object
        properties:
          category: {type: string}
          severity: {type: string, enum: [error, warning, info]}
          location: {type: string}
          message: {type: string}
          suggestion: {type: string}
```

---

## 6. Visual Registry JSON format

```json
{
  "version": "0.1.0",
  "pbir_schema_versions_supported": ["2.7.0", "2.8.0", "2.9.0"],
  "last_updated": "2026-08-21",
  "visuals": {
    "barChart": {
      "display_name": "Bar Chart",
      "category": "comparison",
      "required_roles": [
        {"name": "Category", "data_types": ["string"], "cardinality": "many", "aggregation": "none"},
        {"name": "Y", "data_types": ["number"], "cardinality": "single", "aggregation": "sum"}
      ],
      "optional_roles": [
        {"name": "Legend", "data_types": ["string"], "cardinality": "many"},
        {"name": "Tooltips", "data_types": ["string", "number"]}
      ],
      "supported_properties": {...},
      "color_safe_default": false,
      "mobile_friendly": true,
      "accessibility_notes": [
        "Use distinct colors for legend categories (don't rely on color alone)",
        "Add alt text describing comparison direction"
      ]
    }
  }
}
```

**Mantenimiento:** update mensual desde docs.microsoft.com + community
visual catalog. Versionado semver del JSON.

---

## 7. Acceptance criteria

- [ ] Visual registry cubre 52+ visuales nativos con roles y properties.
- [ ] Suggester devuelve recomendaciones para los 5 semantic types principales.
- [ ] Layout optimizer calcula grids coherentes (alineación, gaps, mobile).
- [ ] Theme generator produce theme.json válido PBIR-compatible.
- [ ] WCAG integration: palette generator valida contraste automáticamente.
- [ ] Storytelling scorer detecta jerarquía y densidad.
- [ ] Performance budget detecta los 5 hotspots comunes.
- [ ] Todos los findings tienen `fix_suggestion` accionable.

## 8. Out of scope (MVP)

- ❌ `design_report_page_from_requirements` completo con NL intent (v2).
- ❌ `select_visuals_for_kpis` avanzado con data shape analysis (v2).
- ❌ Storytelling con análisis de varianza real sobre datos (v3).
- ❌ Auto-fix WCAG completo (solo alt text default + theme en MVP).
- ❌ Marketplace de templates (v3).
- ❌ Screenshots para validación visual (best-effort post MVP).
- ❌ Multi-language UI strings (post MVP).

## 9. Riesgos

| Riesgo | Mitigación |
|--------|-----------|
| Visual registry desactualizado | Update mensual + version pin por `$schema` URL del PBIR. |
| Suggester recomienda visual incorrecto | Documentar que es heurístico; usuario provee feedback via elicitation. |
| WCAG contrast wrong en colores declarados | Usar lib estándar (ej: `wcag-contrast-ratio` Python); tests con pares conocidos. |
| Performance budget muy pesimista | Calibrar con dataset real de benchmarks; opt-in a "aggressive" mode. |
| LLM genera visuals sin sentido | Suggester siempre explica rationale; LLM puede pedir alternativas. |

## 10. Specs relacionados

- [`01-orchestrator.md`](./01-orchestrator.md)
- [`03-validation.md`](./03-validation.md) — WCAG rules compartido
- [`tools/apply-theme-and-accessibility-rules.md`](./tools/apply-theme-and-accessibility-rules.md)
- [`tools/audit-model-and-report.md`](./tools/audit-model-and-report.md)
