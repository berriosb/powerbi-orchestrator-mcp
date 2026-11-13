"""draft_report_summary tool — strict numerical guardrail around LLM narrative.

In MCP architecture the host agent already supplies the LLM. This tool
constructs a bounded prompt from deterministic ``ComputeProcessKpisResult``
figures and deterministically verifies any candidate executive summary text so
every number cited matches the computed metrics (zero hallucinated figures).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.tools.compute_process_kpis import ComputeProcessKpisResult

# Matches numeric tokens like 92.5%, $1,250,000, 1.250,50, 43, -2.5 without eating trailing sentence punctuation.
_NUMBER_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])[-+]?\$?\d(?:[\d.,]*\d)?%?(?![A-Za-z0-9_])")


class DraftReportSummaryInput(BaseModel):
    """Input schema for ``draft_report_summary``."""

    computed_kpis: ComputeProcessKpisResult
    audience: Literal["operations", "contract_admin", "external_client"] = "operations"
    cadence: Literal["daily", "weekly", "monthly"] = "daily"
    candidate_narrative: str | None = None
    language: Literal["es", "en"] = "es"
    allowed_extra_numbers: list[float] = Field(default_factory=list)


class DraftReportSummaryResult(BaseModel):
    """Output schema for ``draft_report_summary``."""

    prompt_bundle: str
    verified_narrative: str | None = None
    numbers_verified: bool
    unverified_numbers: list[str] = Field(default_factory=list)
    missing_required_kpis: list[str] = Field(default_factory=list)


def _normalize_number_token(token: str, *, language: str = "es") -> float | None:
    has_currency = "$" in token
    has_percent = "%" in token
    cleaned = token.strip().lstrip("$").rstrip("%").strip()
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            cleaned = cleaned.replace(",", ".")
        elif all(len(p) == 3 for p in parts[1:]):
            cleaned = "".join(parts)
        else:
            cleaned = cleaned.replace(",", ".")
    elif "." in cleaned:
        parts = cleaned.split(".")
        is_dot_thousands = (
            all(len(p) == 3 and p.isdigit() for p in parts[1:]) and parts[0].lstrip("-+").isdigit()
        )
        if is_dot_thousands and (
            len(parts) > 2
            or (
                len(parts) == 2
                and parts[0].lstrip("-+") != "0"
                and (has_currency or (language == "es" and not has_percent))
            )
        ):
            cleaned = "".join(parts)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _allowed_numeric_pool(
    kpis_obj: ComputeProcessKpisResult,
    extra: list[float],
) -> set[float]:
    allowed: set[float] = {float(kpis_obj.row_count_evaluated)}
    for part in re.findall(r"\d+", kpis_obj.as_of_date):
        allowed.add(float(int(part)))
    for k in kpis_obj.kpis:
        for candidate in (k.value, k.numerator, k.denominator, k.target_threshold):
            if candidate is not None:
                allowed.add(round(float(candidate), 4))
                allowed.add(round(float(candidate), 2))
                allowed.add(round(float(candidate), 0))
    for num in extra:
        allowed.add(round(float(num), 4))
    return allowed


def _is_close_to_any(val: float, pool: set[float], tol: float = 1e-2) -> bool:
    return any(abs(val - allowed) <= tol for allowed in pool)


def _build_prompt(
    kpis_obj: ComputeProcessKpisResult,
    *,
    audience: str,
    cadence: str,
    language: str,
) -> str:
    audience_labels = {
        "operations": "Jefatura de Operaciones / Procesos",
        "contract_admin": "Administrador de Contrato",
        "external_client": "Mandante Externo (solo resumen agregado, sin PII)",
    }
    lines = [
        f"Redacta un resumen ejecutivo ({cadence}) en idioma '{language}' dirigido a: {audience_labels.get(audience, audience)}.",
        f"Fecha de corte: {kpis_obj.as_of_date} | Filas evaluadas: {kpis_obj.row_count_evaluated}.",
        "REGLA ESTRICTA: Usa ÚNICAMENTE las cifras listadas abajo. No inventes ni estimes números:",
    ]
    for k in kpis_obj.kpis:
        thresh = (
            f" (umbral: {k.target_threshold} {k.unit})" if k.target_threshold is not None else ""
        )
        lines.append(f"- {k.name} [{k.kpi_id}]: {k.formatted_value} | estado={k.status}{thresh}")
    return "\n".join(lines)


async def draft_report_summary(
    computed_kpis: ComputeProcessKpisResult | dict[str, Any],
    audience: Literal["operations", "contract_admin", "external_client"] = "operations",
    cadence: Literal["daily", "weekly", "monthly"] = "daily",
    candidate_narrative: str | None = None,
    language: Literal["es", "en"] = "es",
    allowed_extra_numbers: list[float] | None = None,
) -> DraftReportSummaryResult:
    """Build bounded executive summary prompt and verify cited figures in narrative."""
    kpis_obj = (
        computed_kpis
        if isinstance(computed_kpis, ComputeProcessKpisResult)
        else ComputeProcessKpisResult.model_validate(computed_kpis)
    )
    prompt = _build_prompt(
        kpis_obj,
        audience=audience,
        cadence=cadence,
        language=language,
    )

    if candidate_narrative is None or not candidate_narrative.strip():
        return DraftReportSummaryResult(
            prompt_bundle=prompt,
            verified_narrative=None,
            numbers_verified=False,
            missing_required_kpis=[k.kpi_id for k in kpis_obj.kpis if k.value is not None],
        )

    allowed = _allowed_numeric_pool(kpis_obj, allowed_extra_numbers or [])
    tokens = _NUMBER_TOKEN_RE.findall(candidate_narrative)
    unverified: list[str] = []
    cited_values: list[float] = []

    for tok in tokens:
        parsed = _normalize_number_token(tok, language=language)
        if parsed is None:
            continue
        cited_values.append(parsed)
        if not _is_close_to_any(parsed, allowed):
            unverified.append(tok)

    missing_kpis: list[str] = []
    for k in kpis_obj.kpis:
        if k.value is None:
            continue
        if not any(abs(c - float(k.value)) <= 0.05 for c in cited_values):
            missing_kpis.append(k.kpi_id)

    ok = len(unverified) == 0
    return DraftReportSummaryResult(
        prompt_bundle=prompt,
        verified_narrative=candidate_narrative if ok else None,
        numbers_verified=ok,
        unverified_numbers=unverified,
        missing_required_kpis=missing_kpis,
    )


__all__ = [
    "DraftReportSummaryInput",
    "DraftReportSummaryResult",
    "draft_report_summary",
]
