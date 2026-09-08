"""High-level tools (Capa 6).

Each tool composes one or more lower-layer modules (cloud/, validation/,
engines/, viz/) into a single MCP-callable function. Tools receive
Pydantic input models, do the work, and return Pydantic output models.

Sprint 7: 8 MVP tools (SPEC §6.1).
Sprint 8: 3 v1.1 tools (SPEC §6.3).
Sprint 9: 3 v2 tools (SPEC §6.2: refactor_to_calculation_groups,
        select_visuals_for_kpis, design_report_page_from_requirements).
Sprint 10: 3 v2 tools (SPEC §6.2: optimize_report_performance,
        audit_report_ux_and_storytelling, screenshot_report_pages).
Sprint 11: 3 v2 tools (SPEC §6.2: create_semantic_model_from_schema,
        setup_rls_and_roles, promote_in_pipeline).
Sprint 12: 3 v3 tools (SPEC §6.2: commit_workspace_to_git,
        sync_git_to_workspace, set_sensitivity_labels).
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
from powerbi_orchestrator_mcp.tools.audit_report_ux_and_storytelling import (
    AuditReportUxAndStorytelling,
    AuditReportUxAndStorytellingResult,
    UxFinding,
    audit_report_ux_and_storytelling,
)
from powerbi_orchestrator_mcp.tools.commit_workspace_to_git import (
    CommittedItem,
    CommitWorkspaceToGit,
    CommitWorkspaceToGitResult,
    commit_workspace_to_git,
)
from powerbi_orchestrator_mcp.tools.create_report_from_dataset import (
    CreateReportFromDataset,
    create_report_from_dataset,
)
from powerbi_orchestrator_mcp.tools.create_semantic_model_from_schema import (
    ColumnSpec,
    CreateSemanticModelFromSchema,
    CreateSemanticModelResult,
    HierarchySpec,
    MeasureSpec,
    ModelSpec,
    RelationshipCreated,
    RelationshipSpec,
    TableCreated,
    TableSpec,
    create_semantic_model_from_schema,
    render_tmdl,
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
from powerbi_orchestrator_mcp.tools.optimize_report_performance import (
    OptimizeReportPerformance,
    OptimizeReportPerformanceResult,
    PerformanceHotspot,
    optimize_report_performance,
)
from powerbi_orchestrator_mcp.tools.pre_deploy_check import (
    PreDeployCheck,
    pre_deploy_check,
)
from powerbi_orchestrator_mcp.tools.promote_in_pipeline import (
    GateExecuted,
    PromotedItem,
    PromoteInPipeline,
    PromoteInPipelineResult,
    QualityGate,
    promote_in_pipeline,
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
from powerbi_orchestrator_mcp.tools.screenshot_report_pages import (
    PageScreenshot,
    ScreenshotComparisonFinding,
    ScreenshotReportPages,
    ScreenshotReportPagesResult,
    screenshot_report_pages,
)
from powerbi_orchestrator_mcp.tools.select_visuals_for_kpis import (
    SelectVisualsForKpis,
    SelectVisualsResult,
    VisualRecommendation,
    select_visuals_for_kpis,
)
from powerbi_orchestrator_mcp.tools.set_sensitivity_labels import (
    LabeledItem,
    LabelTarget,
    SetSensitivityLabels,
    SetSensitivityLabelsResult,
    set_sensitivity_labels,
)
from powerbi_orchestrator_mcp.tools.setup_rls_and_roles import (
    RlsTestQuery,
    RoleCreated,
    RoleMember,
    RoleSpec,
    SetupRlsAndRoles,
    SetupRlsAndRolesResult,
    TestResult,
    setup_rls_and_roles,
)
from powerbi_orchestrator_mcp.tools.sync_git_to_workspace import (
    DeployedItem,
    SyncGitToWorkspace,
    SyncGitToWorkspaceResult,
    sync_git_to_workspace,
)

__all__ = [
    "AccessibilityResult",
    "AddMeasureWithValidation",
    "CommitWorkspaceToGit",
    "CommitWorkspaceToGitResult",
    "CommittedItem",
    "DeployedItem",
    "ApplyThemeAndAccessibilityRules",
    "AuditCheck",
    "AuditModelAndReport",
    "AuditReportUxAndStorytelling",
    "AuditReportUxAndStorytellingResult",
    "AuditResult",
    "CalcGroupPlan",
    "CreateReportFromDataset",
    "CreateSemanticModelFromSchema",
    "CreateSemanticModelResult",
    "DataDictionaryResult",
    "DeployResult",
    "DeployToWorkspace",
    "DesignReportPageFromRequirements",
    "DesignReportPageResult",
    "DesignedVisual",
    "DiffModels",
    "EditReportVisual",
    "GenerateDataDictionary",
    "GateExecuted",
    "HierarchySpec",
    "LabelTarget",
    "LabeledItem",
    "MeasureSpec",
    "ModelSpec",
    "OKABE_ITO_PALETTE",
    "OptimizeReportPerformance",
    "OptimizeReportPerformanceResult",
    "PageScreenshot",
    "PerformanceHotspot",
    "PreDeployCheck",
    "PromoteInPipeline",
    "PromoteInPipelineResult",
    "PromotedItem",
    "QualityGate",
    "RefactorResult",
    "RefactorToCalculationGroups",
    "RelationshipCreated",
    "RelationshipSpec",
    "RoleCreated",
    "RoleMember",
    "RoleSpec",
    "RunDaxRegression",
    "RunRefresh",
    "RlsTestQuery",
    "SetupRlsAndRoles",
    "SetupRlsAndRolesResult",
    "SetSensitivityLabels",
    "SetSensitivityLabelsResult",
    "SyncGitToWorkspace",
    "SyncGitToWorkspaceResult",
    "TestResult",
    "ScreenshotComparisonFinding",
    "ScreenshotReportPages",
    "ScreenshotReportPagesResult",
    "SelectVisualsForKpis",
    "SelectVisualsResult",
    "TableCreated",
    "TableSpec",
    "UxFinding",
    "VisualRecommendation",
    "ColumnSpec",
    "render_tmdl",
    "add_measure_with_validation",
    "apply_theme_and_accessibility_rules",
    "audit_model_and_report",
    "audit_report_ux_and_storytelling",
    "create_report_from_dataset",
    "create_semantic_model_from_schema",
    "deploy_to_workspace",
    "design_report_page_from_requirements",
    "diff_models",
    "edit_report_visual",
    "generate_data_dictionary",
    "optimize_report_performance",
    "pre_deploy_check",
    "promote_in_pipeline",
    "refactor_to_calculation_groups",
    "run_dax_regression",
    "run_refresh",
    "screenshot_report_pages",
    "select_visuals_for_kpis",
    "commit_workspace_to_git",
    "set_sensitivity_labels",
    "setup_rls_and_roles",
    "sync_git_to_workspace",
]
