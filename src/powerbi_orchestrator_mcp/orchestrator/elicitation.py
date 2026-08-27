"""Elicitation wrapper for MCP 2025-06-18."""

from __future__ import annotations

import time
from typing import Any

from mcp.server.fastmcp import Context
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Pydantic models (spec section 2.6)
# ---------------------------------------------------------------------------


class ElicitationChoice(BaseModel):
    """A single choice for elicitation."""

    label: str
    description: str = ""
    value: str | None = None


class ElicitationRequest(BaseModel):
    """Input schema for elicitation (spec section 2.6)."""

    question: str
    choices: list[ElicitationChoice] | None = None
    multi_select: bool = False
    required: bool = True
    context: dict[str, Any] = Field(default_factory=dict)


class ElicitationResponse(BaseModel):
    """Response from an elicitation."""

    accepted: bool
    values: dict[str, Any] = Field(default_factory=dict)


class ElicitationSchema(BaseModel):
    """Dynamic schema used for MCP Context.elicit."""

    response: str = ""


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

_MIN_ELICIT_INTERVAL_S: float = 5.0
_last_elicit_time: float = 0.0


def _check_rate_limit() -> None:
    """Raise if elicitation rate limit is exceeded."""
    global _last_elicit_time  # noqa: PLW0603
    now = time.monotonic()
    if now - _last_elicit_time < _MIN_ELICIT_INTERVAL_S:
        elapsed = now - _last_elicit_time
        remaining = _MIN_ELICIT_INTERVAL_S - elapsed
        raise ElicitationRateLimitError(
            f"Rate limit: wait {remaining:.1f}s before next elicitation"
        )
    _last_elicit_time = now


class ElicitationRateLimitError(Exception):
    """Raised when elicitation rate limit is exceeded."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def elicit(
    ctx: Context[Any, Any, Any],
    request: ElicitationRequest,
) -> ElicitationResponse:
    """Elicit user input via MCP 2025-06-18.

    Args:
        ctx: The MCP Context (from tool function).
        request: The elicitation request with question, choices, etc.

    Returns:
        ElicitationResponse with accepted status and values.
    """
    _check_rate_limit()

    message = request.question
    if request.choices:
        labels = [c.label for c in request.choices]
        message += f"\n\nOptions: {', '.join(labels)}"

    result = await ctx.elicit(message=message, schema=ElicitationSchema)

    action = getattr(result, "action", None)
    if action == "accept":
        data = getattr(result, "data", None)
        if data is not None and isinstance(data, ElicitationSchema):
            return ElicitationResponse(accepted=True, values={"response": data.response})

    return ElicitationResponse(accepted=False, values={})
