from __future__ import annotations

from models.ollama_client import DISCLAIMER, OllamaClient, OllamaJSONError

SYSTEM_PROMPT = """
You explain portfolio risk metrics contextually for Indian retail investors (NSE/BSE).
Avoid generic textbook definitions. Do not provide financial advice, direct buy/sell/hold
recommendations, or future price predictions. Respond with valid JSON only.
"""


async def explain_risk_metrics(
    client: OllamaClient,
    *,
    symbol_or_portfolio: str,
    beta: float | None,
    var_1d_inr: float | None,
    hhi_concentration: float | None,
    max_drawdown_pct: float | None,
    portfolio_value: float,
    model: str | None = None,
) -> dict:
    prompt = f"""
Label: {symbol_or_portfolio}
Portfolio value: INR {portfolio_value:,.0f}
Beta: {beta}
1-day VaR (INR, 95%): {var_1d_inr}
HHI concentration: {hhi_concentration}
Max drawdown %: {max_drawdown_pct}

Context:
- Beta > 1.3 aggressive for Indian retail; < 0.7 defensive
- HHI > 2500 dangerous concentration (US DOJ reference)
- VaR 95% 1-day: explain as "19 out of 20 trading days..."
- Compare max drawdown to NIFTY 50 worst (~60% in 2008 crisis) when relevant

Return JSON:
{{
  "beta_explanation": "...",
  "var_explanation": "...",
  "concentration_explanation": "...",
  "drawdown_explanation": "...",
  "overall_risk_level": "LOW|MODERATE|HIGH|VERY HIGH",
  "risk_summary": "one sentence",
  "disclaimer": "{DISCLAIMER}"
}}
"""
    raw = await client.chat(prompt, system=SYSTEM_PROMPT, model=model, expect_json=True)
    try:
        data = client.parse_json_response(raw)
    except OllamaJSONError:
        data = {
            "beta_explanation": "",
            "var_explanation": "",
            "concentration_explanation": "",
            "drawdown_explanation": raw[:400],
            "overall_risk_level": "MODERATE",
            "risk_summary": "",
            "disclaimer": DISCLAIMER,
        }
    data.setdefault("disclaimer", DISCLAIMER)
    return data
