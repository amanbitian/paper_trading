"""
models/ — Data science, ML, and AI model logic.

This directory handles all LLM inference, model evaluation, and
data science utilities. Business logic stays in app/services/.

Submodules:
- ollama_client       : Low-level Ollama HTTP client
- signal_synthesizer  : Algo findings → LLM consensus
- backtest_interpreter: Metrics → plain-English explanation
- trade_advisor       : Pre-trade reasoning check
- nl_screener         : Natural language → screener filters
- portfolio_analyst   : Portfolio health narrative
- journal_analyzer    : Trade journal pattern detection
- risk_explainer      : Contextual risk metric explanations
"""

from models.ollama_client import OllamaClient, OllamaJSONError, OllamaSettings, OllamaUnavailableError

__all__ = [
    "OllamaClient",
    "OllamaJSONError",
    "OllamaSettings",
    "OllamaUnavailableError",
]
