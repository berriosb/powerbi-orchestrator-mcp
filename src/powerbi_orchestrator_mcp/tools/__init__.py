"""High-level tools (Capa 6).

Each tool composes one or more lower-layer modules (cloud/, validation/,
engines/) into a single MCP-callable function. Tools receive Pydantic
input models, do the work, and return Pydantic output models.

Sprint 7 implemented the 8 MVP tools from SPEC §6.1.
Sprint 8 implements the 3 v1.1 tools from SPEC §6.3.
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
from powerbi_orchestrator_mcp.tools.run_dax_regression import (
    RunDaxRegression,
    run_dax_regression,
)
from powerbi_orchestrator_mcp.tools.run_refresh import (
    RunRefresh,
    run_refresh,
)

__all__ = [
    "AccessibilityResult",
    "AddMeasureWithValidation",
    "ApplyThemeAndAccessibilityRules",
    "AuditCheck",
    "AuditModelAndReport",
    "AuditResult",
    "CreateReportFromDataset",
    "DataDictionaryResult",
    "DeployResult",
    "DeployToWorkspace",
    "DiffModels",
    "EditReportVisual",
    "GenerateDataDictionary",
    "OKABE_ITO_PALETTE",
    "PreDeployCheck",
    "RunDaxRegression",
    "RunRefresh",
    "add_measure_with_validation",
    "apply_theme_and_accessibility_rules",
    "audit_model_and_report",
    "create_report_from_dataset",
    "deploy_to_workspace",
    "diff_models",
    "edit_report_visual",
    "generate_data_dictionary",
    "pre_deploy_check",
    "run_dax_regression",
    "run_refresh",
]
