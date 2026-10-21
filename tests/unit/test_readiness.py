"""Tests for the production-readiness findings.

These are the questions an operator should be able to answer before the
server sits in front of real data. A finding that is wrong is worse
than no finding: a deployment that describes itself as bounded when it
is not is the exact failure this module exists to prevent.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from powerbi_orchestrator_mcp.orchestrator.readiness import (
    AUDIT_FAIL_CLOSED_ENV,
    AUDIT_SECRET_ENV,
    BPA_RULES_ENV,
    ERROR,
    READ_OPT_OUT_ENV,
    WORKSPACE_ROOT_ENV,
    ReadinessFinding,
    collect_readiness,
    readiness_warnings,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for var in (
        READ_OPT_OUT_ENV,
        WORKSPACE_ROOT_ENV,
        AUDIT_FAIL_CLOSED_ENV,
        AUDIT_SECRET_ENV,
        "PBI_TRANSPORT",
        BPA_RULES_ENV,
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def _by_name() -> dict[str, ReadinessFinding]:
    return {f.name: f for f in collect_readiness()}


class TestTheReadBoundary:
    def test_bounded_by_default(self) -> None:
        finding = _by_name()["read_boundary"]
        assert finding.ok is True

    def test_the_opt_out_is_reported_as_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A deployment with the read boundary off must not describe
        itself as bounded."""
        monkeypatch.setenv(READ_OPT_OUT_ENV, "1")

        finding = _by_name()["read_boundary"]
        assert finding.ok is False
        assert finding.severity == ERROR
        assert READ_OPT_OUT_ENV in str(finding.detail)


class TestTheWorkspaceRoot:
    def test_set_is_reported_as_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(WORKSPACE_ROOT_ENV, "/srv/pbi")

        finding = _by_name()["workspace_root"]
        assert finding.ok is True
        assert "/srv/pbi" in str(finding.detail)

    def test_unset_is_reported(self) -> None:
        finding = _by_name()["workspace_root"]
        assert finding.ok is False
        assert WORKSPACE_ROOT_ENV in str(finding.detail)


class TestTheTransport:
    def test_http_is_reported_as_enforcing_scopes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PBI_TRANSPORT", "http")

        finding = _by_name()["transport"]
        assert finding.ok is True

    def test_stdio_is_reported_as_vacuous(self) -> None:
        finding = _by_name()["transport"]
        assert finding.ok is False
        assert "vacuous" in str(finding.detail)


class TestTheAuditTrail:
    def test_fail_closed_is_reported_as_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUDIT_FAIL_CLOSED_ENV, "1")

        finding = _by_name()["audit_fail_closed"]
        assert finding.ok is True

    def test_fail_open_is_reported(self) -> None:
        finding = _by_name()["audit_fail_closed"]
        assert finding.ok is False
        assert AUDIT_FAIL_CLOSED_ENV in str(finding.detail)

    def test_a_configured_secret_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUDIT_SECRET_ENV, "s3cret")

        finding = _by_name()["audit_secret"]
        assert finding.ok is True

    def test_an_unset_secret_is_reported(self) -> None:
        finding = _by_name()["audit_secret"]
        assert finding.ok is False


class TestTheReport:
    def test_every_finding_is_named(self) -> None:
        names = {f.name for f in collect_readiness()}
        assert {
            "read_boundary",
            "workspace_root",
            "transport",
            "audit_fail_closed",
            "audit_secret",
            "readonly",
            "orchestrator_home",
            "plan_store",
        } <= names

    def test_the_single_node_limit_is_stated(self) -> None:
        finding = _by_name()["plan_store"]
        assert "single node" in str(finding.detail)

    def test_only_the_findings_that_are_not_fine_are_returned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(WORKSPACE_ROOT_ENV, "/srv/pbi")
        monkeypatch.setenv(AUDIT_FAIL_CLOSED_ENV, "1")
        monkeypatch.setenv(AUDIT_SECRET_ENV, "s3cret")
        monkeypatch.setenv("PBI_TRANSPORT", "http")
        monkeypatch.setenv(BPA_RULES_ENV, "/srv/pbi/rules.json")

        warnings = readiness_warnings()
        # The read boundary, the read-only note and the plan store are
        # either fine or informational; the rest are now configured.
        assert {w.name for w in warnings} <= {"readonly"}

    def test_the_bpa_check_is_reported_as_not_running(self) -> None:
        """Without rules the BPA check is refused, not run and passed."""
        finding = _by_name()["bpa_check"]
        assert finding.ok is False
        assert BPA_RULES_ENV in str(finding.detail)

    def test_configured_rules_are_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(BPA_RULES_ENV, "/srv/pbi/rules.json")

        finding = _by_name()["bpa_check"]
        assert finding.ok is True

    def test_every_finding_serialises(self) -> None:
        for finding in collect_readiness():
            payload = finding.as_dict()
            assert set(payload) == {"name", "ok", "detail", "severity"}
            assert payload["detail"]
