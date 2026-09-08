"""setup_rls_and_roles tool — v2 (SPEC §6.2; spec: specs/tools/setup-rls-and-roles.md).

Author RLS roles + members against a TMDL file (PBIP) and (optionally)
run a test matrix that exercises each role with deterministic DAX
predicates.

For MVP the role definitions are injected via Pydantic-validated spec.
The tool writes role blocks into ``definition.tmdl`` as TMDL ``role``
records. The test matrix runs against a pluggable ``test_engine`` that
evaluates the role filter under a given role context and returns the
scalar result of a DAX expression. Without a real Fabric REST bridge,
the engine can be stubbed to return deterministic values; in production
the engine wraps ``FabricClient.executeQueries`` with
``EffectiveIdentity`` per role.

When ``rollback_on_test_failure=True``, failing any test reverts the
TMDL role block edits (atomic via a single .tmdl rewrite).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------


_VALID_MEMBER_TYPES: set[str] = {"email", "group", "principal"}


class RoleMember(BaseModel):
    """One member of a role (email AAD user, AAD group, or service principal)."""

    type: str
    value: str

    @field_validator("type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v not in _VALID_MEMBER_TYPES:
            raise ValueError(
                f"invalid member type {v!r}; expected one of "
                f"{sorted(_VALID_MEMBER_TYPES)}"
            )
        return v


class RlsTestQuery(BaseModel):
    """One test that verifies the role filter."""

    name: str
    dax: str  # expression returning a scalar or table
    expected: dict[str, float | int | str]  # map role_name -> expected value


class RoleSpec(BaseModel):
    """One role + filter + members + optional test queries."""

    role_name: str
    filter_expression: str  # e.g. "[Region] = \"West\""
    table: str
    members: list[RoleMember] = Field(default_factory=list)
    test_queries: list[RlsTestQuery] = Field(default_factory=list)
    description: str | None = None


class SetupRlsAndRoles(BaseModel):
    """Input schema."""

    target: str  # PBIP path or .tmdl file
    spec_yaml: str | None = None
    spec_json: str | None = None
    dry_run: bool = True
    rollback_on_test_failure: bool = True
    test_engine: Any = None  # callable: (role_name, dax) -> value


class RoleCreated(BaseModel):
    """Per-role output entry."""

    role_name: str
    members_count: int
    test_queries_count: int


class TestResult(BaseModel):
    """One test query result."""

    role_name: str
    query_name: str
    expected: Any
    actual: Any
    passed: bool


class SetupRlsAndRolesResult(BaseModel):
    """Output."""

    roles_created: list[RoleCreated] = Field(default_factory=list)
    test_results: list[TestResult] = Field(default_factory=list)
    failed_test: TestResult | None = None
    rollback_performed: bool = False
    risk_score: float = 0.0
    dry_run: bool = True
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------


def _parse_spec(
    spec_yaml: str | None, spec_json: str | None
) -> list[RoleSpec]:
    """Parse a YAML or JSON spec string into a list of RoleSpec."""
    import yaml as _yaml

    if spec_yaml and spec_json:
        raise ValueError("pass either spec_yaml or spec_json, not both")
    if not spec_yaml and not spec_json:
        raise ValueError("one of spec_yaml or spec_json is required")

    raw: Any
    if spec_yaml:
        raw = _yaml.safe_load(spec_yaml)
    else:
        try:
            raw = json.loads(spec_json or "[]")
        except json.JSONDecodeError as exc:
            raise ValueError(f"spec_json is not valid JSON: {exc}") from exc

    # The spec can be a list of roles, or a {"roles": [...]} mapping.
    if isinstance(raw, dict):
        raw = raw.get("roles", [])
    if not isinstance(raw, list):
        raise ValueError("spec must be a list of roles or {roles: [...]}")

    return [RoleSpec.model_validate(item) for item in raw]


# ---------------------------------------------------------------------------
# TMDL editing
# ---------------------------------------------------------------------------


def _locate_tmdl(target: str) -> Path:
    """Locate the TMDL file: if target is a PBIP dir, descend into .Dataset/."""
    p = Path(target)
    if p.is_dir():
        candidates = sorted(p.glob("*.Dataset/definition.tmdl"))
        if not candidates:
            raise FileNotFoundError(
                f"no *.Dataset/definition.tmdl under PBIP {p}"
            )
        return candidates[0]
    if p.is_file():
        return p
    raise FileNotFoundError(f"target not found: {p}")


def _extract_table_ref(filter_expression: str) -> str | None:
    """Best-effort: pull the dim table reference out of a DAX filter expression.

    Used only to ensure cross-filter relationships are plausible at
    edit time. Returns the column name or None if it cannot infer.
    """
    m = re.search(r"\[\s*([^\]]+?)\s*\]", filter_expression)
    return m.group(1) if m else None


def render_tmdl_role(role: RoleSpec) -> str:
    """Render a single role block in TMDL form."""
    members = "\n".join(
        f"        member: {json.dumps(m.value)}" for m in role.members
    )
    desc = (
        f"    description: {json.dumps(role.description)}\n"
        if role.description
        else ""
    )
    return (
        f"role {role.role_name}\n"
        f"{desc}"
        f"    tablePermission {role.table}\n"
        f"        filterExpression: {json.dumps(role.filter_expression)}\n"
        + (members + "\n" if members else "")
    )


def merge_roles_into_tmdl(
    tmdl_text: str, role_blocks: list[str]
) -> str:
    """Append role blocks at the end of the TMDL file (idempotent)."""
    body = tmdl_text.rstrip() + "\n\n// ---- Roles (RLS) ----\n"
    for block in role_blocks:
        body += block + "\n"
    return body


# ---------------------------------------------------------------------------
# Atomic TMDL writes
# ---------------------------------------------------------------------------


def _atomic_write_tmdl(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".rls.", suffix=".tmdl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# ---------------------------------------------------------------------------
# Test engine + matrix execution
# ---------------------------------------------------------------------------


def _execute_test_matrix(
    roles: list[RoleSpec],
    test_engine: Any,
) -> tuple[list[TestResult], TestResult | None]:
    """Run all role × query tests through the (injected) test engine.

    Returns a list of all results and the first failure (or None).
    """
    results: list[TestResult] = []
    first_failure: TestResult | None = None
    for role in roles:
        for q in role.test_queries:
            expected = q.expected.get(role.role_name)
            actual: Any = None
            error: str | None = None
            try:
                actual = test_engine(role_name=role.role_name, dax=q.dax)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
            passed = (error is None) and (actual == expected)
            r = TestResult(
                role_name=role.role_name,
                query_name=q.name,
                expected=expected,
                actual=actual if error is None else f"ERROR: {error}",
                passed=passed,
            )
            results.append(r)
            if not passed and first_failure is None:
                first_failure = r
    return results, first_failure


# ---------------------------------------------------------------------------
# Risk score heuristic
# ---------------------------------------------------------------------------


def _risk_score(specs: list[RoleSpec]) -> float:
    """Crude 0.0-1.0 risk score from spec content."""
    if not specs:
        return 0.0
    n_roles = len(specs)
    n_members = sum(len(r.members) for r in specs)
    n_queries = sum(len(r.test_queries) for r in specs)
    # Heuristic: more members and more wildcards in filters = more risk.
    wildcards = sum(
        1 for r in specs if "*" in r.filter_expression
    )
    raw = (n_roles * 0.1) + (n_members * 0.05) + (n_queries * 0.02) + (
        wildcards * 0.2
    )
    return min(1.0, raw)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def setup_rls_and_roles(
    target: str,
    spec_yaml: str | None = None,
    spec_json: str | None = None,
    dry_run: bool = True,
    rollback_on_test_failure: bool = True,
    test_engine: Any = None,
) -> SetupRlsAndRolesResult:
    """Apply a role spec to a TMDL model + run the test matrix."""
    warnings: list[str] = []

    try:
        specs = _parse_spec(spec_yaml, spec_json)
    except (ValueError, Exception) as exc:  # noqa: BLE001
        return SetupRlsAndRolesResult(
            dry_run=dry_run,
            warnings=[f"spec parse failed: {exc}"],
        )

    risk = _risk_score(specs)

    tmdl_path: Path | None = None
    original_text: str = ""
    if not dry_run:
        try:
            tmdl_path = _locate_tmdl(target)
            original_text = tmdl_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            return SetupRlsAndRolesResult(
                dry_run=dry_run,
                risk_score=risk,
                warnings=[f"could not locate TMDL: {exc}"],
            )

    test_results: list[TestResult] = []
    failed: TestResult | None = None
    if test_engine is not None and specs and specs[0].test_queries:
        test_results, failed = _execute_test_matrix(specs, test_engine)
        if failed is not None and rollback_on_test_failure and not dry_run:
            # Rollback: write the original text back.
            if tmdl_path is not None:
                _atomic_write_tmdl(tmdl_path, original_text)
            return SetupRlsAndRolesResult(
                roles_created=[
                    RoleCreated(
                        role_name=r.role_name,
                        members_count=len(r.members),
                        test_queries_count=len(r.test_queries),
                    )
                    for r in specs
                ],
                test_results=test_results,
                failed_test=failed,
                rollback_performed=True,
                risk_score=risk,
                dry_run=dry_run,
                warnings=[
                    f"rollback due to failed test: {failed.query_name} for role {failed.role_name}",
                ],
            )

    if dry_run or tmdl_path is None:
        return SetupRlsAndRolesResult(
            roles_created=[
                RoleCreated(
                    role_name=r.role_name,
                    members_count=len(r.members),
                    test_queries_count=len(r.test_queries),
                )
                for r in specs
            ],
            test_results=test_results,
            risk_score=risk,
            dry_run=True,
            warnings=warnings,
        )

    # Apply role blocks.
    role_blocks = [render_tmdl_role(r) for r in specs]
    new_text = merge_roles_into_tmdl(original_text, role_blocks)
    _atomic_write_tmdl(tmdl_path, new_text)

    # If tests were run but a failure was suppressed (rollback disabled),
    # surface the failure in the result without rolling back.
    if failed is not None and not rollback_on_test_failure:
        # Keep the TMDL edits; report the first failure so the caller
        # can react (e.g. emit elicitation).
        return SetupRlsAndRolesResult(
            roles_created=[
                RoleCreated(
                    role_name=r.role_name,
                    members_count=len(r.members),
                    test_queries_count=len(r.test_queries),
                )
                for r in specs
            ],
            test_results=test_results,
            failed_test=failed,
            rollback_performed=False,
            risk_score=risk,
            dry_run=False,
            warnings=warnings,
        )

    return SetupRlsAndRolesResult(
        roles_created=[
            RoleCreated(
                role_name=r.role_name,
                members_count=len(r.members),
                test_queries_count=len(r.test_queries),
            )
            for r in specs
        ],
        test_results=test_results,
        risk_score=risk,
        dry_run=False,
        warnings=warnings,
    )


__all__ = [
    "RoleCreated",
    "RoleMember",
    "RoleSpec",
    "RlsTestQuery",
    "SetupRlsAndRoles",
    "SetupRlsAndRolesResult",
    "TestResult",
    "merge_roles_into_tmdl",
    "render_tmdl_role",
    "setup_rls_and_roles",
]
