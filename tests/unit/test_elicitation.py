"""Tests for orchestrator.elicitation - MCP 2025-06-18 wrapper."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from powerbi_orchestrator_mcp.orchestrator.elicitation import (
    ElicitationChoice,
    ElicitationRateLimitError,
    ElicitationRequest,
    ElicitationResponse,
    ElicitationSchema,
    _check_rate_limit,
    elicit,
)


class TestModels:
    """Tests for Pydantic models."""

    def test_elicitation_choice(self) -> None:
        choice = ElicitationChoice(label="Yes", description="Confirm")
        assert choice.label == "Yes"
        assert choice.value is None

    def test_elicitation_choice_with_value(self) -> None:
        choice = ElicitationChoice(label="No", value="no")
        assert choice.value == "no"

    def test_elicitation_request_defaults(self) -> None:
        req = ElicitationRequest(question="Proceed?")
        assert req.choices is None
        assert req.multi_select is False
        assert req.required is True
        assert req.context == {}

    def test_elicitation_request_with_choices(self) -> None:
        req = ElicitationRequest(
            question="Choose action",
            choices=[
                ElicitationChoice(label="A"),
                ElicitationChoice(label="B"),
            ],
        )
        assert len(req.choices) == 2

    def test_elicitation_response(self) -> None:
        resp = ElicitationResponse(accepted=True, values={"key": "val"})
        assert resp.accepted is True
        assert resp.values["key"] == "val"

    def test_elicitation_schema(self) -> None:
        schema = ElicitationSchema(response="yes")
        assert schema.response == "yes"


class TestRateLimiter:
    """Tests for elicitation rate limiter."""

    def test_rate_limit_first_call_ok(self) -> None:
        with patch(
            "powerbi_orchestrator_mcp.orchestrator.elicitation._last_elicit_time", 0.0
        ):
            _check_rate_limit()

    def test_rate_limit_raises_on_fast_call(self) -> None:
        with patch(
            "powerbi_orchestrator_mcp.orchestrator.elicitation._last_elicit_time",
            time.monotonic(),
        ), pytest.raises(ElicitationRateLimitError):
            _check_rate_limit()


class TestElicit:
    """Tests for elicit() function."""

    @pytest.mark.asyncio
    async def test_elicit_accepted(self) -> None:
        ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.action = "accept"
        mock_result.data = ElicitationSchema(response="confirmed")
        ctx.elicit = AsyncMock(return_value=mock_result)

        req = ElicitationRequest(question="Proceed?")
        with patch(
            "powerbi_orchestrator_mcp.orchestrator.elicitation._last_elicit_time", 0.0
        ):
            resp = await elicit(ctx, req)

        assert resp.accepted is True
        assert resp.values["response"] == "confirmed"

    @pytest.mark.asyncio
    async def test_elicit_rejected(self) -> None:
        ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.action = "decline"
        mock_result.content = None
        ctx.elicit = AsyncMock(return_value=mock_result)

        req = ElicitationRequest(question="Delete?")
        with patch(
            "powerbi_orchestrator_mcp.orchestrator.elicitation._last_elicit_time", 0.0
        ):
            resp = await elicit(ctx, req)

        assert resp.accepted is False

    @pytest.mark.asyncio
    async def test_elicit_with_choices_in_message(self) -> None:
        ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.action = "accept"
        mock_result.data = ElicitationSchema(response="a")
        ctx.elicit = AsyncMock(return_value=mock_result)

        req = ElicitationRequest(
            question="Pick one",
            choices=[
                ElicitationChoice(label="Alpha"),
                ElicitationChoice(label="Beta"),
            ],
        )
        with patch(
            "powerbi_orchestrator_mcp.orchestrator.elicitation._last_elicit_time", 0.0
        ):
            resp = await elicit(ctx, req)

        call_args = ctx.elicit.call_args
        assert "Alpha" in call_args.kwargs["message"]
        assert "Beta" in call_args.kwargs["message"]
        assert resp.accepted is True

    @pytest.mark.asyncio
    async def test_elicit_rate_limited(self) -> None:
        ctx = MagicMock()
        req = ElicitationRequest(question="Test")

        with patch(
            "powerbi_orchestrator_mcp.orchestrator.elicitation._last_elicit_time",
            time.monotonic(),
        ), pytest.raises(ElicitationRateLimitError):
            await elicit(ctx, req)
