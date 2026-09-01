"""Validation layer (Capa 4): quality gates, linting, regression testing.

Implements ``specs/03-validation.md``:
- ``bpa_runner.py`` — Best Practice Analyzer (Tabular Editor wrapper)
- ``dax_linter.py`` — regex-based DAX anti-pattern detector (7 of 10 patterns)
- ``dax_regression.py`` — execute DAX queries vs baseline, diff results
- ``model_diff.py`` — semantic model diff with breaking/non-breaking classification
- ``pre_deploy_gate.py`` — gate profiles (strict / standard / relaxed) + threshold evaluation
- ``accessibility/`` — WCAG auditor for PBIR files (Capa 5 partial — no rendering)

Each module is independent; ``audit_model_and_report`` tool will compose
``bpa_runner`` + ``dax_linter`` + ``wcag_auditor`` + ``pre_deploy_gate``
into a single composite report.
"""

from powerbi_orchestrator_mcp.validation.accessibility.wcag_auditor import (
    WcagAuditor,
    WcagFinding,
    WcagResult,
)
from powerbi_orchestrator_mcp.validation.bpa_runner import (
    DEFAULT_TIMEOUT_S,
    SUPPORTED_RULESETS,
    BpaFinding,
    BpaResult,
    BpaRunner,
)
from powerbi_orchestrator_mcp.validation.dax_linter import (
    DaxLinter,
    DaxLinter_PATTERNS,
    DaxLintFinding,
    DaxPattern,
)
from powerbi_orchestrator_mcp.validation.dax_regression import (
    BaselineFile,
    BaselineQuery,
    DaxRegressionRunner,
    QueryDiff,
    RegressionResult,
)
from powerbi_orchestrator_mcp.validation.model_diff import (
    DiffSeverity,
    ModelDiffer,
    ModelDiffResult,
    ObjectDiff,
)
from powerbi_orchestrator_mcp.validation.pre_deploy_gate import (
    BUILTIN_PROFILES,
    GateProfile,
    GateResult,
    GateThresholds,
    PreDeployGate,
)

__all__ = [
    "BUILTIN_PROFILES",
    "BaselineFile",
    "BaselineQuery",
    "BpaFinding",
    "BpaResult",
    "BpaRunner",
    "DaxLintFinding",
    "DaxLinter",
    "DaxLinter_PATTERNS",
    "DaxPattern",
    "DaxRegressionRunner",
    "DEFAULT_TIMEOUT_S",
    "DiffSeverity",
    "GateProfile",
    "GateResult",
    "GateThresholds",
    "ModelDiffer",
    "ModelDiffResult",
    "ObjectDiff",
    "PreDeployGate",
    "QueryDiff",
    "RegressionResult",
    "SUPPORTED_RULESETS",
    "WcagAuditor",
    "WcagFinding",
    "WcagResult",
]
