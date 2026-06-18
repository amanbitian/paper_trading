from __future__ import annotations

from typing import Any

from models.ollama_client import DISCLAIMER, OllamaClient, OllamaJSONError

SYSTEM_PROMPT = """
You write educational portfolio health narratives for Indian equity paper portfolios.
Compare to NIFTY 50 when relevant. Do not provide financial advice, direct buy/sell/hold
recommendations, or future price predictions. Respond with valid JSON only (~200 words in narrative).
"""


async def generate_portfolio_narrative(
    client: OllamaClient,
    *,
    portfolio_summary: dict[str, Any],
    top_holdings: list[dict[str, Any]],
    risk_metrics: dict[str, Any],
    recent_trades: list[dict[str, Any]],
    benchmark_return_1w: float,
    model: str | None = None,
) -> dict[str, Any]:
    prompt = f"""
Portfolio summary: {portfolio_summary}
Top holdings: {top_holdings[:5]}
Risk metrics: {risk_metrics}
Recent trades (last 5): {recent_trades[:5]}
NIFTY 50 1-week return: {benchmark_return_1w}%

Cover: weekly vs benchmark, top contributor/detractor, concentration if sector>35% or stock>20%,
VaR in plain English with INR if present, and one educational risk-control suggestion.

Return JSON:
{{
  "narrative": "~200 words",
  "health_score": 0-100,
  "health_label": "Healthy|Needs attention|High risk",
  "top_concern": "one sentence",
  "disclaimer": "{DISCLAIMER}"
}}
"""
    raw = await client.chat(prompt, system=SYSTEM_PROMPT, model=model, expect_json=True)
    try:
        data = client.parse_json_response(raw)
    except OllamaJSONError:
        data = {
            "narrative": raw[:800],
            "health_score": 50,
            "health_label": "Needs attention",
            "top_concern": "",
            "disclaimer": DISCLAIMER,
        }
    data.setdefault("disclaimer", DISCLAIMER)
    return data
