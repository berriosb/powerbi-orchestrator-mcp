"""Plan models shared between planner and rollback.

Implements the Pydantic models from specs/01-orchestrator.md §2.2 +
§2.5. Kept in a separate module so both planner.py and rollback.py
import them without circular dependencies.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EngineLiteral = Literal["modeling", "report", "cloud", "viz", "validation"]


class EstimatedChanges(BaseModel):
    """Estimated impact of a plan (used by ``connect_target`` output too)."""

    files_affected: int = 0
    measures_affected: int = 0
    visuals_affected: int = 0
    rollback_complexity: Literal["trivial", "moderate", "complex"] = "trivial"


class PlanStep(BaseModel):
    """Single step in a plan."""

    id: str
    engine: EngineLiteral
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    rollback_step: PlanStep | None = None
    validators: list[str] = Field(default_factory=list)


class PlanOptions(BaseModel):
    """Caller-tunable knobs for ``plan_change``."""

    auto_rollback: bool = True
    max_impact_threshold: int = 50
    dry_run_first: bool = True


class Plan(BaseModel):
    """A versionable plan ready for apply_plan."""

    # Allow PlanStep to be referenced in Plan via forward reference.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    yaml: str = ""
    steps: list[PlanStep]
    rollback_steps: list[PlanStep] = Field(default_factory=list)
    risk_score: float = Field(ge=0.0, le=1.0, default=0.0)
    estimated_changes: EstimatedChanges = Field(default_factory=EstimatedChanges)


class PlanTemplate(str):
    """Marker namespace for the supported plan template names."""

    SAFE_RENAME = "safe_rename"
    AUDIT = "audit"
    DEPLOY = "deploy"
    DAX_REGRESSION = "dax_regression"


class PlanValidationError(ValueError):
    """Raised by the planner when a template's args are invalid."""


class UnknownTemplateError(ValueError):
    """Raised when ``plan_change`` is called with an unrecognized template."""


class RiskThresholdExceededError(ValueError):
    """Raised when the plan's risk_score exceeds ``max_impact_threshold``.

    The caller should elicit the user before applying.
    """
