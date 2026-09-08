"""High-level tools (Capa 6).

Each tool composes one or more lower-layer modules (cloud/, validation/,
engines/, viz/) into a single MCP-callable function. Tools receive
Pydantic input models, do the work, and return Pydantic output models.

Sprint 7: 8 MVP tools (SPEC §6.1).
Sprint 8: 3 v1.1 tools (SPEC §6.3).
Sprint 9: 3 v2 tools (SPEC §6.2: refactor_to_calculation_groups,
        select_visuals_for_kpis, design_report_page_from_requirements).
"""

from powerbi_orchestrator_mcp.tools.add_measure_with_validation import (
    AddMeasureWithValidation,
    add_measure_with_validation,
)
from powerbi_orchestrator_mcp.tools.apply_theme_and_accessibility_rules import (
    OKABE_ITO_PALETTE,
    AccessibilityResult,
    ApplyThemeAndAccessibilityRules,
    apply_theme_and_accessibility_rules,
)
from powerbi_orchestrator_mcp.tools.audit_model_and_report import (
    AuditCheck,
    AuditModelAndReport,
    AuditResult,
    audit_model_and_report,
)
from powerbi_orchestrator_mcp.tools.create_report_from_dataset import (
    CreateReportFromDataset,
    create_report_from_dataset,
)
from powerbi_orchestrator_mcp.tools.deploy_to_workspace import (
    DeployResult,
    DeployToWorkspace,
    deploy_to_workspace,
)
from powerbi_orchestrator_mcp.tools.design_report_page_from_requirements import (
    DesignedVisual,
    DesignReportPageFromRequirements,
    DesignReportPageResult,
    design_report_page_from_requirements,
)
from powerbi_orchestrator_mcp.tools.diff_models import (
    DiffModels,
    diff_models,
)
from powerbi_orchestrator_mcp.tools.edit_report_visual import (
    EditReportVisual,
    edit_report_visual,
)
from powerbi_orchestrator_mcp.tools.generate_data_dictionary import (
    DataDictionaryResult,
    GenerateDataDictionary,
    generate_data_dictionary,
)
from powerbi_orchestrator_mcp.tools.pre_deploy_check import (
    PreDeployCheck,
    pre_deploy_check,
)
from powerbi_orchestrator_mcp.tools.refactor_to_calculation_groups import (
    CalcGroupPlan,
    RefactorResult,
    RefactorToCalculationGroups,
    refactor_to_calculation_groups,
)
from powerbi_orchestrator_mcp.tools.run_dax_regression import (
    RunDaxRegression,
    run_dax_regression,
)
from powerbi_orchestrator_mcp.tools.run_refresh import (
    RunRefresh,
    run_refresh,
)
from powerbi_orchestrator_mcp.tools.select_visuals_for_kpis import (
    SelectVisualsForKpis,
    SelectVisualsResult,
    VisualRecommendation,
    select_visuals_for_kpis,
)

__all__ = [
    "AccessibilityResult",
    "AddMeasureWithValidation",
    "ApplyThemeAndAccessibilityRules",
    "AuditCheck",
    "AuditModelAndReport",
    "AuditResult",
    "CalcGroupPlan",
    "CreateReportFromDataset",
    "DataDictionaryResult",
    "DeployResult",
    "DeployToWorkspace",
    "DesignReportPageFromRequirements",
    "DesignReportPageResult",
    "DesignedVisual",
    "DiffModels",
    "EditReportVisual",
    "GenerateDataDictionary",
    "OKABE_ITO_PALETTE",
    "PreDeployCheck",
    "RefactorResult",
    "RefactorToCalculationGroups",
    "RunDaxRegression",
    "RunRefresh",
    "SelectVisualsForKpis",
    "SelectVisualsResult",
    "VisualRecommendation",
    "add_measure_with_validation",
    "apply_theme_and_accessibility_rules",
    "audit_model_and_report",
    "create_report_from_dataset",
    "deploy_to_workspace",
    "design_report_page_from_requirements",
    "diff_models",
    "edit_report_visual",
    "generate_data_dictionary",
    "pre_deploy_check",
    "refactor_to_calculation_groups",
    "run_dax_regression",
    "run_refresh",
    "select_visuals_for_kpis",
]
