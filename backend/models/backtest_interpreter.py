from __future__ import annotations

from models.ollama_client import DISCLAIMER, OllamaClient, OllamaJSONError

SYSTEM_PROMPT = """
You are an educational backtest analyst for Indian equities (NSE/BSE).
Explain metrics in plain English for retail learners. Do not provide financial advice,
direct buy/sell/hold recommendations, or future price predictions.
Respond with valid JSON only.
"""


async def interpret_backtest(
    client: OllamaClient,
    *,
    strategy_name: str,
    symbol: str,
    sector: str,
    metrics: dict,
    benchmark_return: float,
    oos_sharpe: float | None,
    overfitting_score: float | None,
    model: str | None = None,
) -> dict:
    is_sharpe = metrics.get("sharpe_ratio") or metrics.get("is_sharpe_ratio")
    prompt = f"""
Strategy: {strategy_name}
Symbol: {symbol} (sector: {sector or 'unknown'})
Metrics: sharpe={metrics.get('sharpe_ratio')}, max_drawdown_pct={metrics.get('max_drawdown_pct')},
win_rate={metrics.get('win_rate')}, total_return_pct={metrics.get('total_return_pct')},
num_trades={metrics.get('num_trades', metrics.get('total_trades'))}
Benchmark return (period): {benchmark_return}%
OOS sharpe (walk-forward): {oos_sharpe}
Overfitting score (OOS/IS sharpe): {overfitting_score}
In-sample sharpe: {is_sharpe}

Context for Indian daily equity backtests:
- Sharpe > 1.0 good, > 1.5 strong, < 0.5 weak
- Max drawdown > 30% is high risk for retail
- Win rate alone is misleading without payoff ratio - mention this
- If oos_sharpe < 0.5 * in_sample_sharpe, flag likely overfitting
- If num_trades < 20, flag insufficient sample

Return JSON:
{{
  "verdict": "STRONG|ACCEPTABLE|WEAK|OVERFIT",
  "headline": "one sentence",
  "interpretation": "4-5 sentences",
  "red_flags": ["..."],
  "improvement_tip": "one tip",
  "disclaimer": "{DISCLAIMER}"
}}
"""
    raw = await client.chat(prompt, system=SYSTEM_PROMPT, model=model, expect_json=True)
    try:
        data = client.parse_json_response(raw)
    except OllamaJSONError:
        data = {
            "verdict": "WEAK",
            "headline": "Could not parse interpretation.",
            "interpretation": raw[:600],
            "red_flags": [],
            "improvement_tip": "",
            "disclaimer": DISCLAIMER,
        }
    data.setdefault("disclaimer", DISCLAIMER)
    data.setdefault("red_flags", [])
    return data
