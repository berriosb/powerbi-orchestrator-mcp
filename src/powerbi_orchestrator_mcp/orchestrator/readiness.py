"""Production-readiness checks for a deployment.

The questions here are the ones an operator should be able to answer
*before* the server sits in front of real data: is the boundary where I
think it is, is the audit trail worth anything, and is this deployment
shape one the code actually supports?

They are reported in two places: the startup log, so a misconfiguration
shows up when the process boots rather than when something goes wrong,
and ``powerbi_health``, so a running deployment can be asked.

Nothing here refuses to run. These are findings, not gates — except for
the one that matters most: a deployment that turned the read boundary
off should not be able to describe itself as bounded.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

INFO = "info"
WARNING = "warning"
ERROR = "error"

READ_OPT_OUT_ENV = "PBI_ALLOW_OUTSIDE_WORKSPACE_READ"
AUDIT_FAIL_CLOSED_ENV = "PBI_AUDIT_FAIL_CLOSED"
AUDIT_SECRET_ENV = "PBI_ORCHESTRATOR_AUDIT_SECRET"
WORKSPACE_ROOT_ENV = "PBI_WORKSPACE_ROOT"
BPA_RULES_ENV = "PBI_TE_BPA_RULES"
TRANSPORT_ENV = "PBI_TRANSPORT"


@dataclass(frozen=True)
class ReadinessFinding:
    """One thing about the deployment worth knowing before it matters."""

    name: str
    ok: bool
    detail: str
    severity: str = INFO

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "severity": self.severity,
        }


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes")


def _home_writable() -> tuple[bool, str]:
    """Can the orchestrator home be written to?

    The audit log, the plan store and the snapshots all live there. A
    deployment whose home is read-only or full fails at the worst
    possible moment otherwise: in the middle of a mutation.
    """
    from powerbi_orchestrator_mcp.orchestrator.paths import get_orchestrator_home

    home = get_orchestrator_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=home, prefix=".readiness-", delete=True):
            pass
    except OSError as exc:
        return False, f"the orchestrator home is not writable: {exc} ({home})"
    return True, f"the orchestrator home is writable ({home})"


def collect_readiness() -> list[ReadinessFinding]:
    """Everything worth knowing about this deployment's configuration."""
    from powerbi_orchestrator_mcp.orchestrator.authorization import readonly_mode

    findings: list[ReadinessFinding] = []

    # The read boundary. This is the one that decides whether the
    # deployment can describe itself as bounded.
    if _truthy(os.environ.get(READ_OPT_OUT_ENV)):
        findings.append(
            ReadinessFinding(
                name="read_boundary",
                ok=False,
                severity=ERROR,
                detail=(
                    f"{READ_OPT_OUT_ENV} is set: reads are not bounded to the "
                    "workspace. A read-scoped caller can reach any path the "
                    "process can reach. Unset it unless that is deliberate."
                ),
            )
        )
    else:
        findings.append(
            ReadinessFinding(
                name="read_boundary",
                ok=True,
                detail="reads are bounded to the workspace root.",
            )
        )

    # Where that root is.
    root = os.environ.get(WORKSPACE_ROOT_ENV, "").strip()
    if root:
        findings.append(
            ReadinessFinding(
                name="workspace_root",
                ok=True,
                detail=f"{WORKSPACE_ROOT_ENV} is set to {root}.",
            )
        )
    else:
        findings.append(
            ReadinessFinding(
                name="workspace_root",
                ok=False,
                severity=WARNING,
                detail=(
                    f"{WORKSPACE_ROOT_ENV} is not set: the workspace root falls "
                    "back to the working directory, which is wherever the "
                    "process happened to start. Set it to the directory that "
                    "holds the projects."
                ),
            )
        )

    # The transport decides whether the per-tool scopes mean anything.
    transport = os.environ.get(TRANSPORT_ENV, "").strip().lower() or "stdio"
    if transport == "http":
        findings.append(
            ReadinessFinding(
                name="transport",
                ok=True,
                detail=(
                    "HTTP transport: the per-tool scopes are enforced against the caller's token."
                ),
            )
        )
    else:
        findings.append(
            ReadinessFinding(
                name="transport",
                ok=False,
                severity=WARNING,
                detail=(
                    "stdio transport: no principal is bound, so the per-tool "
                    "scope checks are vacuous — every tool is allowed. The "
                    "process itself is the trust boundary. Use --transport "
                    "http with Entra ID to make the scopes mean something."
                ),
            )
        )

    # The audit trail: is it a precondition or a report?
    if _truthy(os.environ.get(AUDIT_FAIL_CLOSED_ENV)):
        findings.append(
            ReadinessFinding(
                name="audit_fail_closed",
                ok=True,
                detail=(
                    f"{AUDIT_FAIL_CLOSED_ENV} is set: a mutation that cannot be "
                    "recorded does not happen."
                ),
            )
        )
    else:
        findings.append(
            ReadinessFinding(
                name="audit_fail_closed",
                ok=False,
                severity=WARNING,
                detail=(
                    f"{AUDIT_FAIL_CLOSED_ENV} is not set: the audit log is "
                    "best-effort, and a failed write does not stop the "
                    "mutation. Set it to 1 to make the trail a precondition."
                ),
            )
        )

    # The key that makes the chain worth anything.
    if os.environ.get(AUDIT_SECRET_ENV, "").strip():
        findings.append(
            ReadinessFinding(
                name="audit_secret",
                ok=True,
                detail=(
                    f"{AUDIT_SECRET_ENV} is set: the audit chain is protected "
                    "against an attacker who can read the orchestrator home."
                ),
            )
        )
    else:
        findings.append(
            ReadinessFinding(
                name="audit_secret",
                ok=False,
                severity=WARNING,
                detail=(
                    f"{AUDIT_SECRET_ENV} is not set: the chain key was "
                    "generated and stored on this machine, so the trail is "
                    "tamper-evident only against someone who cannot read the "
                    "orchestrator home."
                ),
            )
        )

    # The mode the server was started in.
    findings.append(
        ReadinessFinding(
            name="readonly",
            ok=True,
            detail=(
                "read-only mode: every state-changing tool is refused."
                if readonly_mode()
                else "read-write mode: state-changing tools are allowed."
            ),
        )
    )

    # Can it actually write where it needs to?
    writable, detail = _home_writable()
    findings.append(
        ReadinessFinding(
            name="orchestrator_home",
            ok=writable,
            severity=INFO if writable else ERROR,
            detail=detail,
        )
    )

    # The BPA check: it only runs when there are rules to run.
    if os.environ.get(BPA_RULES_ENV, "").strip():
        findings.append(
            ReadinessFinding(
                name="bpa_check",
                ok=True,
                detail=(
                    f"{BPA_RULES_ENV} is set: the Best Practice Analyzer check "
                    "runs against the configured rules."
                ),
            )
        )
    else:
        findings.append(
            ReadinessFinding(
                name="bpa_check",
                ok=False,
                severity=WARNING,
                detail=(
                    f"{BPA_RULES_ENV} is not set, so the BPA check is refused "
                    "rather than run against no rules. Tabular Editor runs "
                    "the rules of the machine it runs on, and a server has "
                    "none, so a run without rules would report a clean score "
                    "that means nothing. The other audit checks still run."
                ),
            )
        )

    # The shape this deployment can have.
    findings.append(
        ReadinessFinding(
            name="plan_store",
            ok=True,
            detail=(
                "the plan store is a local SQLite file: this deployment is a "
                "single node. Two replicas do not share plans, and a shared "
                "network volume is not a supported substitute."
            ),
        )
    )

    return findings


def readiness_warnings() -> list[ReadinessFinding]:
    """Only the findings that are not fine."""
    return [f for f in collect_readiness() if not f.ok]


__all__ = [
    "AUDIT_FAIL_CLOSED_ENV",
    "BPA_RULES_ENV",
    "AUDIT_SECRET_ENV",
    "ERROR",
    "INFO",
    "READ_OPT_OUT_ENV",
    "ReadinessFinding",
    "WARNING",
    "WORKSPACE_ROOT_ENV",
    "collect_readiness",
    "readiness_warnings",
]
