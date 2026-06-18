# Knowledge Transfer Documents
### Paper Trading App — Intern Onboarding

> **Note:** The Streamlit frontend has been removed. The current UI is the
> server-rendered FastAPI + Jinja/HTMX app under `backend/app`. Streamlit
> mentions in these KT docs are historical.

Welcome! These 5 KT documents are your onboarding guide. Start with the one matching your role.

---

## Documents

| # | File | Who Should Read It | Topics |
|---|------|--------------------|--------|
| 1 | [KT_01_FRONTEND.md](KT_01_FRONTEND.md) | Frontend interns | Jinja2 templates, HTMX, Streamlit, page flows, auth state |
| 2 | [KT_02_BACKEND.md](KT_02_BACKEND.md) | Backend interns | FastAPI, SQLAlchemy, Pydantic, JWT, routing, ER diagram |
| 3 | [KT_03_DATA_ENGINEERING.md](KT_03_DATA_ENGINEERING.md) | Data engineering interns | ETL pipelines, yfinance, batch ingestion, caching |
| 4 | [KT_04_ALGO_TRADING.md](KT_04_ALGO_TRADING.md) | Algo interns | Strategies, backtesting engine, paper trading, risk mgmt |
| 5 | [KT_05_DATA_SCIENCE_GENAI.md](KT_05_DATA_SCIENCE_GENAI.md) | DS/AI interns | Ollama LLM, sentiment, fundamentals, signal ML feedback |

---

## Quick Start

```bash
# Clone and start
git clone <repo>
cd paper_trading_app
python run.py start

# App URLs
http://localhost:8000/web/explore   ← Main web UI
http://localhost:8000/docs          ← API docs (Swagger)
http://localhost:8501               ← Streamlit UI (if enabled)
```

## Tech Stack Summary

```
Frontend:   Jinja2 + HTMX + Plotly  (web UI)  |  Streamlit (legacy)
Backend:    FastAPI + Uvicorn + SQLAlchemy + Pydantic
Database:   PostgreSQL 16
Data:       yfinance + MarketAux + Alpha Vantage APIs
AI/LLM:    Ollama (local) — qwen3:14b model
Infra:      Docker + Docker Compose
```
