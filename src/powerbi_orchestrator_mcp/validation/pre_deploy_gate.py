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
    evaluated: int = 0
    """Findings the gate actually looked at.

    Zero means the gate had no evidence to decide on, which is reported
    as a failure rather than as a pass.
    """
    unknown_severities: list[str] = Field(default_factory=list)
    """Severity values the profile does not know about.

    They are blocking: a severity the gate cannot interpret is a
    severity it cannot dismiss.
    """


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
                raise ValueError(f"unknown profile {profile!r}; available: {sorted(profiles)}")
            self._profile = profiles[profile]
        else:
            self._profile = profile

    @property
    def profile(self) -> GateProfile:
        return self._profile

    def evaluate(
        self,
        findings: list[Any],
        *,
        blocking_severities: list[str] | None = None,
    ) -> GateResult:
        """Evaluate a list of findings against the profile.

        ``findings`` may be ``BpaFinding`` instances (with .severity) or
        dicts with a ``"severity"`` key.

        The gate fails closed on anything it cannot interpret:

        - A severity it does not know ("critical", "Error", a missing
          severity) blocks, whatever the profile tolerates.
        - No findings at all is not a pass: the gate had no evidence to
          decide on, and reporting "passed" would read as "everything
          was checked and nothing was found".
        """
        counts = {"error": 0, "warning": 0, "info": 0}
        unknown: list[str] = []
        for f in findings:
            raw = f.get("severity") if isinstance(f, dict) else getattr(f, "severity", None)
            sev = str(raw).strip().lower() if raw is not None else ""
            if sev in counts:
                counts[sev] += 1
            else:
                unknown.append(sev or "(missing severity)")

        failed: list[str] = []
        for severity_name, threshold in [
            ("error", self._profile.error),
            ("warning", self._profile.warning),
            ("info", self._profile.info),
        ]:
            count = counts[severity_name]
            is_blocking = (
                (severity_name in blocking_severities)
                if blocking_severities is not None
                else threshold.blocking
            )
            if is_blocking and count > threshold.max_findings:
                failed.append(f"{severity_name}: {count} findings (max {threshold.max_findings})")

        if unknown:
            summary = ", ".join(sorted(set(unknown)))
            failed.append(
                f"unknown severity: {summary} — the gate cannot interpret "
                "these findings, so they block"
            )

        evaluated = len(findings)
        if evaluated == 0:
            failed.append(
                "no findings were evaluated: the gate had no evidence to "
                "decide on, so it did not pass"
            )

        return GateResult(
            passed=not failed,
            profile_name=self._profile.name,
            error_count=counts["error"],
            warning_count=counts["warning"],
            info_count=counts["info"],
            failed_checks=failed,
            evaluated=evaluated,
            unknown_severities=sorted(set(unknown)),
        )


__all__ = [
    "BUILTIN_PROFILES",
    "GateProfile",
    "GateResult",
    "GateThresholds",
    "PreDeployGate",
]
