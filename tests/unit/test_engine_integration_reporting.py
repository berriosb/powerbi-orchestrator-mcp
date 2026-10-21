"""Tests for what the health check says about the engines.

The health check is what an operator reads to decide what to install.
Suggesting an install that enables no feature sends them down the wrong
path, so every engine it reports has to be one a tool can dispatch to.
"""

from __future__ import annotations

from powerbi_orchestrator_mcp.orchestrator.engine_detector import (
    get_known_engines,
)


class TestTheDetectorOnlyListsUsableEngines:
    def test_the_engines_the_orchestrator_dispatches_to_are_declared(self) -> None:
        known = set(get_known_engines())

        assert "powerbi-modeling-mcp" in known
        assert "te" in known
        assert "superbi-mcp" in known

    def test_the_engines_without_an_adapter_are_not_declared(self) -> None:
        """Nothing dispatches to these, so listing them helps nobody.

        `dscmd` used to be listed, and `pbip-validator` with it: the
        health check reported them as available and suggested an
        install that enabled no feature. `pbip-validator` was never
        published on PyPI either.
        """
        known = set(get_known_engines())

        assert "dscmd" not in known
        assert "pbip-validator" not in known


class TestHealthReportsUsableEngines:
    async def test_every_reported_engine_is_one_a_tool_can_use(self) -> None:
        from powerbi_orchestrator_mcp.tools.powerbi_health import powerbi_health

        result = await powerbi_health(include_engine_details=True)

        assert set(result["engines"]) <= set(get_known_engines())

    async def test_an_unavailable_engine_gets_remediation(self) -> None:
        """The remediation has to point at something that helps."""
        from powerbi_orchestrator_mcp.tools.powerbi_health import powerbi_health

        result = await powerbi_health(include_engine_details=True)

        joined = " ".join(result.get("remediation", []))
        assert "daxstudio.org" not in joined
        assert "pbip-validator" not in joined

        for name, engine in result["engines"].items():
            if not engine["available"]:
                assert name in joined or "Install" in joined
