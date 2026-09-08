# Tool: `select_visuals_for_kpis` (v2)

> Recommend the best visual type for a given KPI based on its semantic
> type, data shape, and target audience. Returns a primary
> recommendation + 2-3 alternatives + justification grounded in
> established PBI / SQLBI guidelines.

**Status:** v2 outline → implementation (Sprint 9)
**Layer:** 5 (Viz/UX)

---

## 1. Inputs

| Name | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `kpis` | list[KPI] | ✅ | — | Each: name, semantic_type, fields, data_shape, audience |
| `audience` | string | ❌ | `"executive"` | `executive` / `analyst` / `operational` |
| `palette` | string | ❌ | `"okabe_ito"` | Colorblind-safe palette hint |
| `include_alternatives` | bool | ❌ | `true` | Return 2-3 alternatives or just the primary |
| `max_results` | int | ❌ | `3` | How many alternatives to return (1-5) |

```python
class KPI(BaseModel):
    name: str
    semantic_type: str  # single_value | comparison | trend | composition | distribution | correlation
    fields: list[str]  # column references to be visualized
    data_shape: dict[str, Any] = Field(default_factory=dict)  # e.g. {"cardinality": 10, "time_span": "5y"}
```

---

## 2. Outputs

| Name | Type | Notes |
|------|------|-------|
| `primary` | VisualRecommendation | Type, justification, expected fields, sample SQLBI reference |
| `alternatives` | list[VisualRecommendation] | 2-3 ranked alternatives |
| `rationale` | string | Full explanation (markdown) |
| `warnings` | list[string] | E.g. "cardinality > 10000 → avoid pie/donut" |

```python
class VisualRecommendation(BaseModel):
    type: str  # "card" | "barChart" | "lineChart" | etc.
    justification: str
    expected_fields: list[str]
    sqlbi_reference: str | None
    color_safe: bool
```

---

## 3. Workflow

1. **For each KPI**: compute features (cardinality, time-span, has
   hierarchy, etc.).
2. **Lookup**: query `viz.visual_registry` for candidates matching
   the semantic type + features.
3. **Score** each candidate against (in order):
   - Data shape match (cardinality, time span).
   - Audience preference (executives → simple visuals; analysts →
     detailed).
   - Anti-recommendations (e.g. pie chart with >7 categories).
4. **Rank**: highest score = primary; next N = alternatives.
5. **Annotate** with SQLBI / PBI docs reference.

---

## 4. Acceptance criteria

- [ ] Returns a primary visual for every KPI.
- [ ] Anti-recommendations are surfaced (e.g. pie chart with >7 slices).
- [ ] Colorblind-safe palette referenced in `color_safe=True`.
- [ ] Justification cites established guidelines (SQLBI / PBI docs).

---

## 5. Failure modes

- **Unknown semantic_type:** fall back to `barChart` with a warning.
- **No fields provided:** return error explaining the KPI needs at least
  one column reference.

---

## 6. Cross-references

- [`../../src/viz/visual_registry.py`](../../src/viz/visual_registry.py) — backing data
- [`../04-viz-ux.md`](../04-viz-ux.md) §2 — recommendation algorithm spec
