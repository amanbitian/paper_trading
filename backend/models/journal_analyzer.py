from __future__ import annotations

from typing import Any

from models.ollama_client import DISCLAIMER, OllamaClient, OllamaJSONError

SYSTEM_PROMPT = """
You analyze paper trading journals for learning patterns in Indian markets.
Be encouraging, name cognitive biases with evidence, never shame the trader.
Do not provide financial advice, direct buy/sell/hold recommendations, or future price predictions.
Respond with valid JSON only.
"""

MIN_TRADES = 10


async def analyze_journal_patterns(
    client: OllamaClient,
    *,
    trades_with_notes: list[dict[str, Any]],
    model: str | None = None,
) -> dict[str, Any]:
    if len(trades_with_notes) < MIN_TRADES:
        return {
            "error": f"Add at least {MIN_TRADES} paper trades with notes to unlock journal analysis.",
            "patterns_found": [],
            "biases_detected": [],
            "strengths": [],
            "improvement_areas": [],
            "summary": "",
            "disclaimer": DISCLAIMER,
        }

    trade_lines = "\n".join(
        f"- {t.get('date')} {t.get('symbol')} {t.get('action')} pnl={t.get('pnl')} notes={t.get('notes', '')[:200]}"
        for t in trades_with_notes[:50]
    )
    prompt = f"""
Trades with notes:
{trade_lines}

Look for: overtrading, emotional language (FOMO, revenge), reasoning vs profitability,
anchoring, loss aversion, recency bias, strategy/execution consistency.

Return JSON:
{{
  "patterns_found": ["..."],
  "biases_detected": ["..."],
  "strengths": ["..."],
  "improvement_areas": ["..."],
  "summary": "3 sentences",
  "disclaimer": "{DISCLAIMER}"
}}
"""
    raw = await client.chat(prompt, system=SYSTEM_PROMPT, model=model, expect_json=True)
    try:
        data = client.parse_json_response(raw)
    except OllamaJSONError:
        data = {
            "patterns_found": [],
            "biases_detected": [],
            "strengths": [],
            "improvement_areas": [raw[:200]],
            "summary": raw[:400],
            "disclaimer": DISCLAIMER,
        }
    data.setdefault("disclaimer", DISCLAIMER)
    return data
