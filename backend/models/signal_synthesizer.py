from __future__ import annotations

from typing import Any

from models.ollama_client import DISCLAIMER, OllamaClient, OllamaJSONError

SYSTEM_PROMPT = """
You are an educational trading analysis assistant for Indian equity markets (NSE/BSE).
Your job is to help users understand complex algorithmic signals. Do not provide financial
advice, direct buy/sell/hold recommendations, or future price predictions.
Always end responses with the disclaimer field copied from the requested JSON schema.
Be concise, honest, and never hype a stock.
Respond with valid JSON only, no markdown fences.
"""


async def synthesize_signals(
    client: OllamaClient,
    *,
    symbol: str,
    findings: list[dict[str, Any]],
    model: str | None = None,
) -> dict[str, Any]:
    lines = []
    for row in findings:
        lines.append(
            f"- {row.get('algorithm_name', row.get('algo_type', 'algo'))}: "
            f"signal={row.get('action', row.get('signal', 'HOLD'))}, "
            f"confidence={row.get('confidence_score', 0)}%, "
            f"reason={row.get('reason', '')[:120]}"
        )
    findings_text = "\n".join(lines) or "No findings provided."

    prompt = f"""
Stock: {symbol}
Algorithm findings:
{findings_text}

Instructions:
- Weight technical execution algos (VWAP, RSI, MACD, SMA) higher than statistical/ML proxies for daily OHLCV.
- If >= 60% of actionable signals agree on direction, describe the model state as BULLISH or BEARISH accordingly; else MIXED or NEUTRAL.
- Treat consensus as an educational summary of stored indicators, not a trading instruction.
- consensus_strength is 0-100 integer.

Return JSON:
{{
  "headline": "one sentence",
  "summary": "max 3 sentences",
  "consensus": "BULLISH|BEARISH|MIXED|NEUTRAL",
  "consensus_strength": 0,
  "agreement_note": "why algos agree or disagree",
  "key_risk": "one sentence",
  "educational_note": "one learning point",
  "disclaimer": "{DISCLAIMER}"
}}
"""
    raw = await client.chat(prompt, system=SYSTEM_PROMPT, model=model, expect_json=True)
    try:
        data = client.parse_json_response(raw)
    except OllamaJSONError:
        data = {
            "headline": "Unable to parse model response.",
            "summary": raw[:500],
            "consensus": "NEUTRAL",
            "consensus_strength": 0,
            "agreement_note": "",
            "key_risk": "",
            "educational_note": "",
            "disclaimer": DISCLAIMER,
        }
    data.setdefault("disclaimer", DISCLAIMER)
    return data
