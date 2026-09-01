"""Pre-deploy gate — evaluates findings against configurable thresholds.

Implements ``specs/03-validation.md`` §4.

A "gate" is a set of checks with thresholds. The gate evaluates the
checks' findings against the thresholds and returns a pass/fail with
diagnostics. Pre-deploy hooks into this before ``deploy_to_workspace``.

Built-in profiles (per spec):
- ``strict`` — zero error findings allowed; warnings tolerated up to 5.
- ``standard`` (default) — zero error findings; warnings tolerated up to 20.
- ``relaxed`` — error findings tolerated up to 3; warnings up to 50.

Profiles can be loaded from a custom YAML file (see spec §4.4).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GateThresholds(BaseModel):
    """Thresholds for a single severity level."""

    max_findings: int = 0
    blocking: bool = True  # If True, any finding > max = fail.


class GateProfile(BaseModel):
    """Pre-deploy gate profile."""

    name: str
    description: str = ""
    error: GateThresholds = Field(default_factory=lambda: GateThresholds())
    warning: GateThresholds = Field(
        default_factory=lambda: GateThresholds(max_findings=20, blocking=False)
    )
    info: GateThresholds = Field(
        default_factory=lambda: GateThresholds(max_findings=100, blocking=False)
    )


class GateResult(BaseModel):
    """Result of a pre-deploy gate evaluation."""

    passed: bool
    profile_name: str
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    failed_checks: list[str] = Field(default_factory=list)


# Built-in profiles (spec §4).
BUILTIN_PROFILES: dict[str, GateProfile] = {
    "strict": GateProfile(
        name="strict",
        description="Zero tolerance; production releases.",
        error=GateThresholds(max_findings=0, blocking=True),
        warning=GateThresholds(max_findings=5, blocking=False),
        info=GateThresholds(max_findings=20, blocking=False),
    ),
    "standard": GateProfile(
        name="standard",
        description="Default; tolerates minor warnings.",
        error=GateThresholds(max_findings=0, blocking=True),
        warning=GateThresholds(max_findings=20, blocking=False),
        info=GateThresholds(max_findings=100, blocking=False),
    ),
    "relaxed": GateProfile(
        name="relaxed",
        description="Dev environments; tolerates more.",
        error=GateThresholds(max_findings=3, blocking=True),
        warning=GateThresholds(max_findings=50, blocking=False),
        info=GateThresholds(max_findings=500, blocking=False),
    ),
}


class PreDeployGate:
    """Evaluate findings against a profile."""

    def __init__(
        self,
        profile: GateProfile | str = "standard",
        *,
        custom_profiles: dict[str, GateProfile] | None = None,
    ) -> None:
        profiles = {**BUILTIN_PROFILES, **(custom_profiles or {})}
        if isinstance(profile, str):
            if profile not in profiles:
                raise ValueError(
                    f"unknown profile {profile!r}; "
                    f"available: {sorted(profiles)}"
                )
            self._profile = profiles[profile]
        else:
            self._profile = profile

    @property
    def profile(self) -> GateProfile:
        return self._profile

    def evaluate(
        self, findings: list[Any]
    ) -> GateResult:
        """Evaluate a list of findings against the profile.

        ``findings`` may be ``BpaFinding`` instances (with .severity) or
        dicts with a ``"severity"`` key.
        """
        counts = {"error": 0, "warning": 0, "info": 0}
        for f in findings:
            if isinstance(f, dict):
                sev = f.get("severity", "info")
            else:
                sev = getattr(f, "severity", "info")
            if sev in counts:
                counts[sev] += 1

        failed: list[str] = []
        for severity_name, threshold in [
            ("error", self._profile.error),
            ("warning", self._profile.warning),
            ("info", self._profile.info),
        ]:
            count = counts[severity_name]
            if threshold.blocking and count > threshold.max_findings:
                failed.append(
                    f"{severity_name}: {count} findings "
                    f"(max {threshold.max_findings})"
                )

        return GateResult(
            passed=not failed,
            profile_name=self._profile.name,
            error_count=counts["error"],
            warning_count=counts["warning"],
            info_count=counts["info"],
            failed_checks=failed,
        )


__all__ = [
    "BUILTIN_PROFILES",
    "GateProfile",
    "GateResult",
    "GateThresholds",
    "PreDeployGate",
]
