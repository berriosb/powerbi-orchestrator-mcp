# Example 03 — Audit-then-fix loop

> The iterative improvement workflow: audit a PBIP, get findings,
> fix one issue, audit again, repeat. Useful for taking a legacy
> report from "score 40" to "score 90".

## What this demonstrates

- `audit_model_and_report` — composite audit (BPA + DAX lint + WCAG + naming).
- `add_measure_with_validation` — gated write that fails on lint.
- `apply_theme_and_accessibility_rules` — WCAG improvement in one call.
- `generate_data_dictionary` — documentation sync after fixes.

## Prerequisites

A PBIP folder. We'll use `tests/fixtures/sample.pbip`.

## Walkthrough

### Step 1 — Baseline audit

```
audit_model_and_report(
    pbip_path="/path/to/legacy-report.pbip",
    bpa_ruleset="default",
    dax_measures_json="{}",
    bpa=true,
    dax_lint=true,
    accessibility=true,
    naming=true
)
```

You'll get a composite score + per-check findings.

### Step 2 — Apply theme + accessibility rules (one-shot WCAG improvement)

```
apply_theme_and_accessibility_rules(
    pbip_path="/path/to/legacy-report.pbip",
    palette="okabe_ito",
    auto_backfill_alt_text=true,
    alt_text_template="{visual_type} visualizing {first_measure}"
)
```

This:
- Switches the theme to Okabe-Ito (colorblind-safe 8-color palette).
- Backfills missing alt text on every visual.
- Re-runs WCAG audit; you'll see the score go up.

### Step 3 — Fix DAX anti-patterns

For each DAX linter finding, write the corrected measure:

```
add_measure_with_validation(
    target="/path/to/legacy-report.pbip",
    measure_name="Total Sales Fixed",
    table="FactSales",
    expression="SUM(FactSales[TotalAmount])",
    format_string="$#,##0",
    description="Total revenue across all sales transactions",
    fail_on_severity="warning",
    dry_run=false
)
```

The lint gate blocks the write if the expression triggers any rule at
severity ≥ `fail_on_severity`. Try a bad expression first to see:

```
add_measure_with_validation(
    target="/path/to/legacy-report.pbip",
    measure_name="Bad Measure",
    table="FactSales",
    expression="SUM(FactSales[TotalAmount]) / SUM(FactSales[Units])",
    fail_on_severity="warning"
)
```

This should fail with `BP_DIVIDE_VS_SLASH` because `/` should be `DIVIDE`.

### Step 4 — Add documentation

```
generate_data_dictionary(
    pbip_path="/path/to/legacy-report.pbip",
    output_path="/tmp/dictionary.md"
)
```

Generates Markdown with a Mermaid ER diagram + coverage score
(percentage of columns with descriptions).

### Step 5 — Re-audit to confirm

```
audit_model_and_report(
    pbip_path="/path/to/legacy-report.pbip"
)
```

The composite score should now be higher than baseline. Compare with
the result from step 1.

## Loop pattern

The audit-then-fix loop is repeatable:

```
1. audit_model_and_report(pbip)               # baseline
2. apply_theme_and_accessibility_rules(...) # WCAG
3. for each DAX finding:
     add_measure_with_validation(...)       # gated
4. generate_data_dictionary(...)             # docs
5. audit_model_and_report(pbip)               # confirm
6. (optional) git commit -m "audit: score 40 → 78"
```

## What can go wrong

- **`add_measure_with_validation` blocks on lint** — read the
  `lint_findings`; the `suggestion` field is the fix.
- **Theme change breaks visuals** — apply_theme applies Okabe-Ito;
  if your report depended on the legacy colors, the swap will be
  visible. Use a custom palette if needed.
- **Data dictionary coverage <50%** — go back and add descriptions to
  the missing columns (the output lists them).

## Files

```
examples/03-audit-then-fix/
├── README.md
└── expected-output.md
```