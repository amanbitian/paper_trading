from __future__ import annotations

from typing import Any

from models.ollama_client import DISCLAIMER, OllamaClient, OllamaJSONError

SYSTEM_PROMPT = """
You are an educational pre-trade reasoning coach for Indian paper trading (NSE/BSE).
Acknowledge good reasoning and flag risks. Do not provide financial advice, direct
buy/sell/hold recommendations, or future price predictions. Be educational only.
Respond with valid JSON only.
"""


async def evaluate_trade_reasoning(
    client: OllamaClient,
    *,
    symbol: str,
    action: str,
    quantity: int,
    price: float,
    user_notes: str,
    algo_signals: list[dict[str, Any]],
    portfolio_concentration_pct: float,
    current_portfolio_value: float,
    atr_stop_price: float | None,
    stop_loss_pct: float | None,
    model: str | None = None,
) -> dict[str, Any]:
    signals_text = "\n".join(
        f"- {s.get('algorithm_name', 'algo')}: {s.get('action', 'HOLD')} ({s.get('confidence_score', 0)}%)"
        for s in algo_signals
    ) or "No algo signals."

    prompt = f"""
Proposed paper trade: {action} {quantity} x {symbol} @ INR {price}
Portfolio value: INR {current_portfolio_value:,.0f}
Position would be {portfolio_concentration_pct:.1f}% of portfolio after trade
ATR stop price: {atr_stop_price}
Stop loss %: {stop_loss_pct}
User reasoning: {user_notes or '(none provided)'}

Algo signals:
{signals_text}

Rules:
- Acknowledge what the user got right
- Flag concentration if > 25%
- Note algo contradictions vs trade direction
- Comment risk/reward vs ATR stop
- Suggest one research or risk-management improvement
- Never approve, reject, or instruct the user to place the trade

Return JSON:
{{
  "reasoning_quality": "STRONG|ADEQUATE|THIN",
  "positive_note": "...",
  "considerations": ["...", "..."],
  "risk_reward_note": "...",
  "educational_note": "...",
  "disclaimer": "{DISCLAIMER}"
}}
"""
    raw = await client.chat(prompt, system=SYSTEM_PROMPT, model=model, expect_json=True)
    try:
        data = client.parse_json_response(raw)
    except OllamaJSONError:
        data = {
            "reasoning_quality": "THIN",
            "positive_note": "",
            "considerations": [raw[:300]],
            "risk_reward_note": "",
            "educational_note": "",
            "disclaimer": DISCLAIMER,
        }
    data.setdefault("disclaimer", DISCLAIMER)
    data.setdefault("considerations", [])
    return data
