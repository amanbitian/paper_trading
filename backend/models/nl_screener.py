from __future__ import annotations

from models.ollama_client import OllamaClient, OllamaJSONError

SYSTEM_PROMPT = """
You convert natural language Indian stock screening questions into JSON filter parameters.
Only use fields listed in the user message. Respond with valid JSON only.
"""


async def parse_nl_query(
    client: OllamaClient,
    *,
    query: str,
    model: str | None = None,
) -> dict:
    prompt = f"""
User query: {query}

Map to filters for NSE/BSE stock performance screener.
Available fields (use null if unspecified):
  sector (string), exchange ("NSE"|"BSE"),
  min_change_1m_pct, max_change_1m_pct,
  min_change_3m_pct, max_change_3m_pct,
  min_change_6m_pct, max_change_6m_pct,
  min_change_1y_pct, max_change_1y_pct,
  sort_by: one of change_1m_pct | change_1y_pct | latest_volume | latest_price,
  sort_desc: boolean

Examples:
- "IT stocks down this year but up last month" -> sector Information Technology, max_change_1y_pct negative, min_change_1m_pct positive
- "Banking stocks with high volume" -> sector Financial Services or Banks, sort_by latest_volume, sort_desc true
- "Stocks up more than 20% in 3 months" -> min_change_3m_pct 20

Return JSON:
{{
  "filters": {{ ... }},
  "explanation": "Searching for ...",
  "confidence": "HIGH|MEDIUM|LOW"
}}
"""
    raw = await client.chat(prompt, system=SYSTEM_PROMPT, model=model, expect_json=True)
    try:
        data = client.parse_json_response(raw)
    except OllamaJSONError:
        data = {
            "filters": {},
            "explanation": f"Could not parse query: {query}",
            "confidence": "LOW",
        }
    data.setdefault("filters", {})
    data.setdefault("explanation", f"Showing results for: {query}")
    data.setdefault("confidence", "LOW")
    return data
