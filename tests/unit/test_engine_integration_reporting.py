"""Tests for what the health check says about the engines.

The health check is what an operator reads to decide what to install.
Suggesting an install that enables no feature sends them down the wrong
path, so the check says which engines the orchestrator can actually
dispatch to.
"""

from __future__ import annotations

from powerbi_orchestrator_mcp.orchestrator.engine_detector import (
    INTEGRATED_ENGINES,
    get_known_engines,
)


class TestIntegratedEngines:
    def test_the_engines_the_orchestrator_dispatches_to_are_declared(self) -> None:
        assert "powerbi-modeling-mcp" in INTEGRATED_ENGINES
        assert "te" in INTEGRATED_ENGINES
        assert "superbi-mcp" in INTEGRATED_ENGINES

    def test_the_engines_without_an_adapter_are_not_declared(self) -> None:
        """Nothing dispatches to these, so installing them changes nothing."""
        assert "dscmd" not in INTEGRATED_ENGINES
        assert "pbip-validator" not in INTEGRATED_ENGINES

    def test_every_detected_engine_is_either_integrated_or_known(self) -> None:
        known = set(get_known_engines())
        assert known >= INTEGRATED_ENGINES


class TestHealthReportsIntegration:
    async def test_an_unintegrated_engine_is_reported_as_such(self) -> None:
        from powerbi_orchestrator_mcp.tools.powerbi_health import powerbi_health

        result = await powerbi_health(include_engine_details=True)

        engines = result["engines"]
        for name in ("dscmd", "pbip-validator"):
            assert engines[name]["integrated"] is False
            assert "not integrated" in (engines[name]["note"] or "")

    async def test_the_check_detail_says_it(self) -> None:
        from powerbi_orchestrator_mcp.tools.powerbi_health import powerbi_health

        result = await powerbi_health(include_engine_details=True)

        details = {c["name"]: c.get("detail") for c in result["checks"]}
        assert "not integrated" in (details.get("engine.dscmd") or "")
        assert "not integrated" in (details.get("engine.pbip-validator") or "")

    async def test_an_unintegrated_engine_gets_no_remediation(self) -> None:
        """There is nothing to install for an engine nothing uses."""
        from powerbi_orchestrator_mcp.tools.powerbi_health import powerbi_health

        result = await powerbi_health(include_engine_details=True)

        joined = " ".join(result.get("remediation", []))
        assert "daxstudio.org" not in joined
        assert "pbip-validator" not in joined
