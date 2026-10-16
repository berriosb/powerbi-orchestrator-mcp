"""Guard rails for .github/workflows/pilot-validation.yml.

The job touches a real tenant, so the properties that keep it safe are
asserted here rather than left to a reviewer to notice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "pilot-validation.yml"


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    loaded: Any = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    mapping: dict[Any, Any] = workflow
    triggers = mapping.get("on", mapping.get(True))
    assert isinstance(triggers, dict), "workflow has no trigger mapping"
    return triggers


class TestWorkflowIsSafeByDefault:
    def test_only_manual_trigger(self, workflow: dict) -> None:
        triggers = _triggers(workflow)

        assert list(triggers.keys()) == ["workflow_dispatch"]

    def test_no_automatic_triggers(self) -> None:
        source = _WORKFLOW.read_text(encoding="utf-8")

        for forbidden in ("\n  push:", "\n  pull_request:", "\n  schedule:"):
            assert forbidden not in source

    def test_workflow_dispatch_has_no_dangerous_defaults(self, workflow: dict) -> None:
        inputs = _triggers(workflow)["workflow_dispatch"]["inputs"]

        assert inputs["workspace_id"]["required"] is True
        assert inputs["dataset_id"]["required"] is True
        assert inputs["workspace_id"].get("default") is None
        assert inputs["dataset_id"].get("default") is None

    def test_confirmation_defaults_to_false(self, workflow: dict) -> None:
        inputs = _triggers(workflow)["workflow_dispatch"]["inputs"]
        confirm = inputs["i_understand_this_touches_a_real_tenant"]

        assert confirm["default"] is False
        assert confirm["required"] is True

    def test_permissions_are_read_only(self, workflow: dict) -> None:
        assert workflow["permissions"] == {"contents": "read"}

    def test_uses_a_named_environment(self, workflow: dict) -> None:
        assert workflow["jobs"]["preflight"]["environment"] == "pilot-validation"

    def test_runs_the_readonly_script(self, workflow: dict) -> None:
        steps = workflow["jobs"]["preflight"]["steps"]
        commands = "\n".join(s.get("run", "") for s in steps)

        assert "pilot_check.py" in commands
        assert "--workspace-id" in commands
        assert "--dataset-id" in commands


class TestMissingSecretsSkipCleanly:
    def test_secret_probe_runs_before_the_live_step(self, workflow: dict) -> None:
        steps = workflow["jobs"]["preflight"]["steps"]
        names = [s.get("id") for s in steps]

        probe = names.index("secrets")
        live = next(i for i, s in enumerate(steps) if "pilot_check.py" in s.get("run", ""))

        assert probe < live

    def test_live_step_is_gated_on_the_probe(self, workflow: dict) -> None:
        steps = workflow["jobs"]["preflight"]["steps"]
        live = next(s for s in steps if "pilot_check.py" in s.get("run", ""))

        assert live["if"] == "steps.secrets.outputs.ready == 'true'"

    def test_probe_treats_empty_secrets_as_not_ready(self, workflow: dict) -> None:
        steps = workflow["jobs"]["preflight"]["steps"]
        probe = next(s for s in steps if s.get("id") == "secrets")

        assert "ready=false" in probe["run"]
        assert '[ -z "$TENANT" ]' in probe["run"]
        assert "exit 0" in probe["run"]


class TestNoSecretsInSource:
    def test_every_pilot_secret_reference_goes_through_secrets_context(self) -> None:
        offenders = [
            line.strip()
            for line in _WORKFLOW.read_text(encoding="utf-8").splitlines()
            if any(
                name in line and ":" in line and not line.strip().startswith("#")
                for name in (
                    "PBI_PILOT_TENANT_ID",
                    "PBI_PILOT_CLIENT_ID",
                    "PBI_PILOT_CLIENT_SECRET",
                )
            )
            and "secrets." not in line
        ]

        assert offenders == []

    def test_azure_variables_are_not_hardcoded(self) -> None:
        source = _WORKFLOW.read_text(encoding="utf-8")

        for var in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
            hardcoded = [
                line.strip()
                for line in source.splitlines()
                if f"{var}:" in line and "secrets." not in line
            ]
            assert hardcoded == [], f"{var} must come from secrets, got {hardcoded}"
