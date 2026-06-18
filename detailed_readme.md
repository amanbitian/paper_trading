# Paper Trading App — Detailed Knowledge Transfer (KT)

> **⚠️ Streamlit has been removed.** The frontend is now the server-rendered
> FastAPI + Jinja/HTMX web UI (`backend/app/templates`, `backend/app/static`).
> Any Streamlit / `frontend/` references below are historical.

> **Audience:** New interns and engineers joining this project.  
> **Purpose:** End-to-end system design, data flows, database schema, algorithms, and how to run every part of the stack.  
> **Disclaimer:** This is a **paper trading / educational** tool. It does not provide financial advice, live exchange connectivity, or production brokerage execution.

---

## Table of Contents

1. [What This Application Does](#1-what-this-application-does)
2. [High-Level System Design](#2-high-level-system-design)
3. [Repository Layout](#3-repository-layout)
4. [Technology Stack](#4-technology-stack)
5. [How to Run the Application](#5-how-to-run-the-application)
6. [Configuration & Environment Variables](#6-configuration--environment-variables)
7. [Ticker Ingestion (How We Get Stocks)](#7-ticker-ingestion-how-we-get-stocks)
8. [Price Ingestion (How We Get OHLCV History)](#8-price-ingestion-how-we-get-ohlcv-history)
9. [Database Schema (All Tables)](#9-database-schema-all-tables)
10. [Authentication & Security](#10-authentication--security)
11. [Feature Logic (End-to-End)](#11-feature-logic-end-to-end)
12. [Trading Strategies (Signal Engine)](#12-trading-strategies-signal-engine)
13. [Algorithm Findings & Sequential Rankings](#13-algorithm-findings--sequential-rankings)
14. [Backtesting Engine](#14-backtesting-engine)
15. [REST API Reference](#15-rest-api-reference)
16. [Streamlit Frontend](#16-streamlit-frontend)
17. [CLI Scripts & Operations Runbook](#17-cli-scripts--operations-runbook)
18. [Backend Function Catalog](#18-backend-function-catalog)
19. [Observability & Logging](#19-observability--logging)
20. [Known Limitations & Production Gaps](#20-known-limitations--production-gaps)
21. [Intern Onboarding Checklist](#21-intern-onboarding-checklist)

---

## 1. What This Application Does

This is a **local-first paper trading and portfolio tracker** for **Indian equities (NSE + BSE)**.

Users can:

- Register / login with JWT sessions
- Search Indian stocks stored in PostgreSQL
- View market overview, movers, and an **All stocks** performance table (1M / 3M / 6M / 1Y % change)
- Add manual holdings to portfolios
- Place **paper orders** (simulated fills at latest stored close)
- Configure **RSI / SMA crossover / Breakout** strategies, generate signals, optionally execute as paper orders
- Run **daily backtests** on historical candles
- Inspect **algorithm findings** (VWAP proxy, GARCH proxy, sequence model proxy, etc.) on stock detail pages

**Data source for prices:** Yahoo Finance via the `yfinance` Python library (not live NSE/BSE feeds).

---

## 2. High-Level System Design

### 2.1 Component diagram

```mermaid
flowchart TB
    subgraph Client["Client Layer"]
        ST[Streamlit Frontend :8501]
    end

    subgraph API["API Layer"]
        FA[FastAPI Backend :8000]
    end

    subgraph Data["Data Layer"]
        PG[(PostgreSQL :5432)]
    end

    subgraph External["External"]
        YF[Yahoo Finance / yfinance]
        NSE_CSV[NSE EQUITY_L.csv]
        BSE_CSV[BSE BhavCopy CSV]
    end

    subgraph Jobs["Offline Jobs / Scripts"]
        LT[load_indian_tickers.py]
        FP[fetch_prices.py]
        IMH[ingest_market_history.py]
        IB[ingest_bootstrap.py]
        SS[seed_strategy_templates.py]
    end

    ST -->|HTTP + JWT Bearer| FA
    FA --> PG
    Jobs --> PG
    LT --> NSE_CSV
    LT --> BSE_CSV
    FP --> YF
    IMH --> YF
    FA --> YF
```

### 2.2 Request flow (typical user action)

```mermaid
sequenceDiagram
    participant U as User
    participant S as Streamlit
    participant A as FastAPI
    participant D as PostgreSQL
    participant Y as yfinance

    U->>S: Open Explore page
    S->>A: GET /market/overview (Bearer token)
    A->>D: Read stock_prices / stocks
    A->>Y: Optional live quote fallback
    A-->>S: JSON overview
    S-->>U: Render indices + movers

    U->>S: Open stock detail ?stock=RELIANCE.NS
    S->>A: GET /stocks/search?query=RELIANCE
    S->>A: GET /stocks/{id}/prices
    S->>A: GET /stocks/{id}/algo-findings
    A->>D: OHLCV + analytics
    A-->>S: Charts + findings
```

### 2.3 Design principles

| Principle | Implementation |
|-----------|----------------|
| Local-first | PostgreSQL is the source of truth for tickers, candles, portfolios |
| Thin frontend | Streamlit only calls HTTP APIs; no business logic duplication |
| Daily candles only | `timeframe='1d'` enforced in `market_data_service.ensure_daily_interval` |
| Idempotent ingestion | `stock_prices` upsert on `(stock_id, price_datetime, timeframe)` |
| Separated auth secrets | Password hashes in `user_credentials`; sessions in `auth_sessions` |
| Observable flows | `@timed` decorator + `app.timing` / `frontend.timing` log lines |

---

## 3. Repository Layout

```
paper_trading_app/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, routers, middleware
│   │   ├── config.py            # pydantic-settings
│   │   ├── database.py          # SQLAlchemy engine + SessionLocal
│   │   ├── security.py          # JWT, bcrypt, get_current_user
│   │   ├── models/              # SQLAlchemy ORM tables
│   │   ├── schemas/             # Pydantic request/response models
│   │   ├── routers/             # HTTP endpoints
│   │   ├── services/            # Business logic
│   │   ├── strategies/          # RSI, SMA, Breakout
│   │   └── utils/observability.py
│   ├── alembic/                 # DB migrations
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── streamlit_app.py         # Home / auth
│   ├── api_client.py            # HTTP + formatting helpers
│   └── pages/                   # Multipage Streamlit UI
├── scripts/                     # CLI ingestion & bootstrap
├── data/                        # Sample ticker CSVs
├── docker-compose.yml
├── README.md                    # Quick start
└── detailed_readme.md           # This document
```

---

## 4. Technology Stack

| Layer | Technology |
|-------|------------|
| API | Python 3.12, FastAPI, Uvicorn |
| ORM | SQLAlchemy 2.x |
| Migrations | Alembic |
| DB | PostgreSQL 16 |
| UI | Streamlit (multipage) |
| Market data | yfinance, pandas |
| Charts | Plotly (frontend) |
| Auth | bcrypt, python-jose (JWT) |
| Containers | Docker Compose |

---

## 5. How to Run the Application

### 5.1 Docker (recommended for interns)

```powershell
cd "C:\Users\Aman\Documents\New project\paper_trading_app"
docker compose up --build
```

| Service | URL |
|---------|-----|
| Streamlit UI | http://localhost:8501 |
| FastAPI | http://localhost:8000 |
| API docs (Swagger) | http://localhost:8000/docs |
| PostgreSQL | `localhost:5432` / DB `paper_trading` / user `postgres` / pass `postgres` |

The **backend container** runs `alembic upgrade head` then starts Uvicorn.  
Docker Compose sets `DEBUG_AUTH_BYPASS=true` so you can use the UI without registering (local dev only).

### 5.2 Full market data setup (required for realistic Explore page)

```powershell
# 1) Load all NSE + BSE tickers (online; falls back to sample CSV if blocked)
docker compose run --rm backend python /app/scripts/load_indian_tickers.py --source online

# 2) Seed strategy templates (RSI, SMA, Breakout)
docker compose run --rm backend python /app/scripts/seed_strategy_templates.py

# 3) Backfill daily prices from 2010 (long-running; run in a dedicated terminal)
docker compose run --rm backend python /app/scripts/ingest_market_history.py --exchange NSE --start-date 2010-01-01 --batch-size 100 --sleep-seconds 0.5

# 4) After NSE completes, run BSE (many symbols may have no Yahoo data)
docker compose run --rm backend python /app/scripts/ingest_market_history.py --exchange BSE --start-date 2010-01-01 --batch-size 100 --sleep-seconds 0.5
```

**Monitor progress:**

```powershell
docker exec paper_trading_postgres psql -U postgres -d paper_trading -c "SELECT COUNT(*) FROM stocks; SELECT COUNT(DISTINCT stock_id) FROM stock_prices WHERE timeframe='1d';"
```

### 5.3 Local development (without Docker for API)

**Terminal 1 — Postgres only:**

```powershell
docker compose up -d postgres
```

**Terminal 2 — Backend:**

```powershell
cd paper_trading_app\backend
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Terminal 3 — Frontend:**

```powershell
cd paper_trading_app\frontend
.\.venv\Scripts\activate
pip install -r requirements.txt
$env:PAPER_TRADING_API_URL="http://localhost:8000"
streamlit run streamlit_app.py --server.port 8501
```

### 5.4 Stop services

```powershell
docker compose down          # stop containers
docker compose down -v       # also delete postgres volume (wipes DB)
```

---

## 6. Configuration & Environment Variables

Loaded from `backend/.env` via `app/config.py` (`pydantic-settings`).

| Variable | Default | Purpose |
|----------|---------|---------|
| `APP_ENV` | `local` | Environment name; enables production guardrails |
| `DATABASE_URL` | `postgresql+psycopg2://postgres:postgres@localhost:5432/paper_trading` | SQLAlchemy URL |
| `JWT_SECRET_KEY` | `change_me` | JWT signing secret (**must change in production**) |
| `JWT_ALGORITHM` | `HS256` | JWT algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Token/session TTL (24h) |
| `AUTO_MIGRATE_ON_START` | `false` | Run Alembic on FastAPI startup |
| `AUTH_DEBUG_LOG_PASSWORD_CHECKS` | `false` | Log password verify diagnostics |
| `DEBUG_AUTH_BYPASS` | `false` | Skip JWT; auto debug user (**blocked in production**) |
| `DEBUG_AUTH_USER_EMAIL` | `debug@example.com` | Debug user email |
| `DEBUG_AUTH_USER_NAME` | `debug_user` | Debug username |
| `DEBUG_AUTH_NAME` | `Debug User` | Display name |
| `YFINANCE_DEFAULT_PERIOD` | `1y` | Default yfinance lookback |
| `YFINANCE_DEFAULT_INTERVAL` | `1d` | Default interval (only `1d` accepted at runtime) |

**Frontend env:**

| Variable | Default | Purpose |
|----------|---------|---------|
| `PAPER_TRADING_API_URL` | `http://localhost:8000` | Backend base URL |
| `PAPER_TRADING_DEBUG_AUTH_BYPASS` | unset | Frontend-side bypass flag |

**Production rules:** `APP_ENV=production` rejects `JWT_SECRET_KEY=change_me` and `DEBUG_AUTH_BYPASS=true`.

---

## 7. Ticker Ingestion (How We Get Stocks)

### 7.1 Overview

Tickers are **symbols + metadata** stored in the `stocks` table. Price history is a **separate step** (Section 8).

```mermaid
flowchart LR
    A[Source: online or csv] --> B[load_indian_tickers.py]
    B --> C{Normalize Yahoo symbol}
    C -->|NSE RELIANCE| D[RELIANCE.NS]
    C -->|BSE 500325| E[500325.BO]
    D --> F[upsert_stock]
    E --> F
    F --> G[(stocks table)]
```

### 7.2 Sources

| Source | CLI flag | What it loads |
|--------|----------|---------------|
| **Online NSE** | `--source online` | `https://archives.nseindia.com/content/equities/EQUITY_L.csv` |
| **Online BSE** | `--source online` | BSE equity ISIN CSV (URL in script; may change) |
| **Sample CSV** | `--source csv` (default) | `data/nse_tickers_sample.csv` (10 names) + `data/bse_tickers_sample.csv` (10 codes) |

If online download fails, the script **logs the exception** and **falls back to sample CSV**.

### 7.3 Normalization (`ticker_service.py`)

| Function | Logic |
|----------|-------|
| `normalize_nse_symbol(symbol)` | `RELIANCE` → `RELIANCE.NS` |
| `normalize_bse_symbol(code)` | `500325` → `500325.BO` |
| `upsert_stock(...)` | PostgreSQL upsert on unique `(symbol, exchange)`; sets `is_active=True` |
| `search_stocks(db, query, exchange?, limit)` | `ILIKE` on symbol, yahoo_symbol, company_name |

### 7.4 Why you might only see 20 stocks

If nobody ran `--source online`, only the **demo CSV** (10 NSE + 10 BSE) is loaded. Production-like setup requires:

```powershell
docker compose run --rm backend python /app/scripts/load_indian_tickers.py --source online
```

Expected result: **~2,400 NSE + ~4,400 BSE** rows (≈6,700+ total).

### 7.5 Failed rows

Failures are appended to `data/failed_tickers.log` (created at runtime).

---

## 8. Price Ingestion (How We Get OHLCV History)

### 8.1 Overview

Daily OHLCV candles are stored in `stock_prices` with `timeframe='1d'` and `source='yfinance'`.

```mermaid
flowchart TB
    S[Active stocks in DB] --> F[fetch_prices.py or ingest_market_history.py]
    F --> G[sync_all_active_stocks / sync_stock_prices]
    G --> H[Split date range into 365-day chunks]
    H --> I[yfinance download + Ticker.history fallback]
    I --> J[save_stock_prices - upsert]
    J --> K[(stock_prices)]
    G --> L[(ingestion_runs audit)]
```

### 8.2 Core functions (`market_data_service.py`)

| Function | What it does |
|----------|--------------|
| `fetch_stock_history(yahoo_symbol, period, interval, start_date, end_date)` | Calls `yf.download`, falls back to `Ticker.history` if empty |
| `save_stock_prices(db, stock_id, dataframe, timeframe)` | Upserts each row; skips rows with missing `close` |
| `sync_stock_prices(...)` | Loops date windows; supports incremental mode |
| `sync_all_active_stocks(...)` | Batch run with `IngestionRun` record (status, counts, offsets) |

### 8.3 Full backfill optimizations (since 2010)

When **not** in incremental mode, `sync_stock_prices` now:

1. **Skips** if DB already has history covering `start_date` → `end_date` (±7 days tolerance)
2. **Probes** the last 365 days on Yahoo — if empty, skips the symbol (avoids 16 wasted chunk calls for delisted BSE codes)
3. **Chunks** remaining range in `--chunk-days` (default 365) with `--sleep-seconds` throttle

**Percent change math** (Explore → All stocks) uses latest close vs close at or before 1M / 3M / 6M / 1Y ago:

```
change_pct = ((latest_close - old_close) / old_close) * 100
```

Implemented in `stock_performance_service.list_stock_performance` via SQL `LATERAL` joins.

### 8.4 Incremental daily updates

```powershell
docker compose run --rm backend python /app/scripts/ingest_market_history.py --incremental --batch-size 100
```

Reads `max(stock_prices.price_datetime)` per stock and fetches from `last_date + 1` through **T-1** (previous business day in IST).

### 8.5 Ingestion audit table

Each batch creates an `ingestion_runs` row:

| Field | Meaning |
|-------|---------|
| `status` | `RUNNING`, `SUCCEEDED`, `PARTIAL`, `FAILED` |
| `ingestion_mode` | `FULL` or `INCREMENTAL` |
| `batch_offset` / `batch_limit` | Resume pointers |
| `rows_saved` | Total candles upserted in batch |
| `success_count` / `failed_count` | Per-symbol outcomes |

### 8.6 Data gaps interns should expect

| Cause | Effect |
|-------|--------|
| Backfill still running | Most stocks show `-` in All stocks tab |
| Delisted / illiquid BSE `.BO` | Yahoo returns empty; 0 rows saved |
| IPO after 2010 | No candles before listing (normal) |
| Yahoo throttling | Slow batches; increase `sleep-seconds` |
| BSE code ≠ Yahoo ticker | Some codes never resolve on Yahoo |

---

## 9. Database Schema (All Tables)

### 9.1 ER diagram (logical)

```mermaid
erDiagram
    users ||--o| user_credentials : has
    users ||--o{ auth_sessions : has
    users ||--o{ portfolios : owns
    users ||--o{ transactions : makes
    users ||--o{ paper_orders : places
    users ||--o{ user_strategies : configures
    users ||--o{ backtest_runs : runs

    stocks ||--o{ stock_prices : has
    stocks ||--o{ portfolio_holdings : held_in
    stocks ||--o{ strategy_signals : signaled_on

    portfolios ||--o{ portfolio_holdings : contains
    portfolios ||--o{ portfolio_daily_snapshot : snapshots
    portfolios ||--o{ paper_orders : receives

    strategy_templates ||--o{ user_strategies : instantiates
    user_strategies ||--o{ strategy_signals : emits

    paper_orders ||--o| paper_trades : fills

    backtest_runs ||--o{ backtest_trades : contains
```

### 9.2 Table reference

#### `users` — Profile & paper cash

| Column | Description |
|--------|-------------|
| `id` | Primary key |
| `name` | Display name |
| `user_name` | Unique handle (e.g. `@aman_123`) |
| `email` | Unique login email |
| `starting_cash` | Initial paper cash (default ₹10,00,000) |
| `current_cash` | Spendable cash for paper portfolios |
| `risk_profile` | `conservative` / `moderate` / `aggressive` |

#### `user_credentials` — Password only

| Column | Description |
|--------|-------------|
| `user_id` | FK → users (1:1) |
| `password_hash` | bcrypt hash; plaintext never stored |

#### `auth_sessions` — Server-side JWT sessions

| Column | Description |
|--------|-------------|
| `token_jti_hash` | SHA-256 of JWT `jti` claim |
| `expires_at` | Session expiry |
| `revoked_at` | Set on logout |

#### `password_reset_tokens` — Reserved (no API yet)

Stores hashed reset tokens for a future flow.

#### `stocks` — Master ticker list

| Column | Description |
|--------|-------------|
| `symbol` | Exchange symbol (NSE ticker or BSE scrip code) |
| `yahoo_symbol` | e.g. `RELIANCE.NS`, `500325.BO` (unique) |
| `exchange` | `NSE` or `BSE` |
| `company_name`, `sector`, `industry` | Metadata |
| `is_active` | Included in batch price sync |

**Unique constraints:** `(symbol, exchange)`, `yahoo_symbol`

#### `stock_prices` — Daily OHLCV

| Column | Description |
|--------|-------------|
| `stock_id` | FK → stocks |
| `price_datetime` | Candle timestamp (UTC midnight for daily) |
| `timeframe` | Always `1d` in MVP |
| `open`, `high`, `low`, `close`, `adjusted_close`, `volume` | OHLCV |
| `source` | `yfinance` |

**Unique:** `(stock_id, price_datetime, timeframe)`

#### `ingestion_runs` — Price job audit log

Tracks batch sync jobs (see Section 8.5).

#### `portfolios`

| Column | Description |
|--------|-------------|
| `portfolio_name` | User label |
| `portfolio_type` | `manual`, `paper`, `sip`, `algo` |
| `starting_value` | Initial notional |

On registration, `create_default_portfolios_for_user` creates **Manual Portfolio** and **Paper Trading**.

#### `portfolio_holdings`

| Column | Description |
|--------|-------------|
| `quantity` | Shares held |
| `average_buy_price` | Weighted average cost |
| `total_invested` | Cost basis |
| `realized_pnl` | Accumulated on sells |

#### `transactions` — Manual buy/sell ledger

| Column | Description |
|--------|-------------|
| `transaction_type` | `BUY` / `SELL` |
| `gross_amount`, `charges`, `net_amount` | Cash impact |
| `source` | `manual` |

#### `paper_orders` / `paper_trades`

| Entity | Purpose |
|--------|---------|
| `paper_orders` | User intent (MARKET / LIMIT / STOP_LOSS) |
| `paper_trades` | Executed fill linked 1:1 to order |

**MVP behavior:** Only **MARKET** orders execute immediately at `get_latest_price`. LIMIT/STOP stay `PENDING` placeholders.

#### `portfolio_daily_snapshot`

End-of-day portfolio metrics (value, P&L, return %) — upserted when performance is calculated.

#### `strategy_templates`

Built-in strategy definitions: `rsi`, `sma_crossover`, `breakout` with `default_parameters` JSON.

#### `user_strategies`

User-bound instance: portfolio + template + custom `parameters` + `risk_settings`.

#### `strategy_signals`

Output of `generate_signal`: `BUY`/`SELL`/`HOLD`, confidence, suggested quantity/price, `indicators` JSON.

#### `backtest_runs` / `backtest_trades`

Stored simulation results and per-trade log.

#### `alembic_version`

Migration version tracking (Alembic).

---

## 10. Authentication & Security

```mermaid
sequenceDiagram
    participant C as Client
    participant A as /auth
    participant S as security.py
    participant DB as PostgreSQL

    C->>A: POST /auth/register
    A->>DB: users + user_credentials + default portfolios

    C->>A: POST /auth/login
    A->>DB: verify bcrypt
    A->>DB: INSERT auth_sessions (sha256(jti))
    A-->>C: JWT access_token

    C->>S: Authorization: Bearer ...
    alt DEBUG_AUTH_BYPASS
        S->>DB: get_or_create debug user
    else Normal
        S->>S: jwt.decode + check jti hash
        S->>DB: load User
    end

    C->>A: POST /auth/logout
    A->>DB: SET revoked_at on session
```

| Module | Key functions |
|--------|---------------|
| `security.py` | `verify_password`, `get_password_hash`, `create_access_token`, `decode_token_payload`, `get_current_user` |
| `routers/auth.py` | `register`, `login`, `me`, `logout` |

**Password rules:** 8–72 characters (bcrypt byte limit enforced).

---

## 11. Feature Logic (End-to-End)

### 11.1 Registration & login (`streamlit_app.py` + `auth` router)

1. User submits registration form → `POST /auth/register`
2. Backend creates `users`, `user_credentials`, default portfolios
3. Login → `POST /auth/login` → JWT + `auth_sessions` row
4. Token stored in Streamlit `st.session_state["token"]`
5. All pages call `require_login()` except debug bypass mode

### 11.2 Explore dashboard (`pages/1_Explore.py`)

| UI block | Backend | Logic |
|----------|---------|-------|
| Index strip | `GET /market/overview` | NIFTY/SENSEX-style cards; tries yfinance → DB → sample fallback; 5 min cache |
| Market movers | same | Top gainers/losers/volume shockers on T-1 closes |
| All stocks tab | `GET /stocks/performance` | All active stocks with latest close + 1M/3M/6M/1Y % from stored candles |
| Sequential rankings | `GET /market/sequential-rankings` | Ranks universe by sequence-proxy score (needs ≥80 daily rows per stock) |
| Stock search | `GET /stocks/search` | ILIKE search, links to `?stock=YAHOO_SYMBOL` |
| Stock detail | `/stocks/{id}/prices`, `/algo-findings` | Candlestick + algorithm expanders |
| Your investments | `/portfolios/{id}/performance` | Holdings, snapshots, P&L |

### 11.3 Add Portfolio (`pages/2_Add_Portfolio.py`)

- `POST /portfolios` — create manual/paper/sip/algo portfolio
- `POST /transactions/manual-buy` — increases holding (weighted avg cost), debits cash if paper portfolio

### 11.4 Paper Trading (`pages/3_Paper_Trading.py`)

1. `POST /stocks/{id}/sync-prices` — refresh 1y daily candles from Yahoo
2. `POST /paper-orders` — MARKET fills at latest close; updates holdings + cash for paper portfolios
3. `GET /paper-orders` — list recent orders

### 11.5 Strategy Lab (`pages/4_Strategy_Lab.py`)

1. `GET /strategies/templates` — list RSI / SMA / Breakout templates
2. `POST /strategies/user-strategy` — bind template to portfolio with JSON parameters
3. `POST /strategies/generate-signal` — run strategy on latest stored OHLCV; `risk_management.calculate_position_size` sizes qty
4. `POST /strategies/signals/{id}/execute-paper-order` — places MARKET paper order from signal

### 11.6 Backtesting (`pages/5_Backtesting.py`)

1. `POST /backtest/run` — day-by-day walk-forward on stored candles (see Section 14)
2. Displays return, drawdown, Sharpe, win rate, equity curve, trades table

---

## 12. Trading Strategies (Signal Engine)

Located in `backend/app/strategies/`. Used by **Strategy Lab**, **Backtesting**, and signal execution.

### 12.1 Class hierarchy

```mermaid
classDiagram
    class BaseStrategy {
        +merged_parameters()
        +generate_signal(prices, parameters)* SignalResult
    }
    class RSIStrategy
    class SMACrossoverStrategy
    class BreakoutStrategy
    BaseStrategy <|-- RSIStrategy
    BaseStrategy <|-- SMACrossoverStrategy
    BaseStrategy <|-- BreakoutStrategy
```

`strategy_service.get_strategy_instance(strategy_type)` maps:

| `strategy_type` | Class |
|-----------------|-------|
| `rsi` | `RSIStrategy` |
| `sma_crossover` | `SMACrossoverStrategy` |
| `breakout` | `BreakoutStrategy` |

### 12.2 RSI Mean Reversion (`rsi_strategy.py`)

**Logic:**

1. Compute RSI(14) from close prices: `RS = avg_gain / avg_loss`, `RSI = 100 - 100/(1+RS)`
2. **BUY** if `RSI < buy_rsi_below` (default 30)
3. **SELL** if `RSI > sell_rsi_above` (default 70)
4. **HOLD** otherwise
5. Confidence scales with distance from threshold

**Default parameters:** `rsi_period=14`, `buy_rsi_below=30`, `sell_rsi_above=70`, `stop_loss_pct=5`, `max_position_size_pct=8`

### 12.3 SMA Crossover (`sma_crossover_strategy.py`)

**Logic:**

1. Short SMA (20) and long SMA (50) on close
2. **BUY** on golden cross (short crosses above long)
3. **SELL** on death cross (short crosses below long)
4. Confidence fixed at 80 on crossover event

### 12.4 Breakout (`breakout_strategy.py`)

**Logic:**

1. `lookback_period` (20) — max high of prior bars (excluding latest)
2. **BUY** if latest close > prior high **and** volume > `volume_multiplier` × average volume
3. Otherwise **HOLD**

### 12.5 Risk management (`risk_management.py`)

`calculate_position_size(portfolio_value, current_price, risk_per_trade_pct, stop_loss_pct, max_position_size_pct, available_cash)`:

- Risk amount = `portfolio_value × risk_per_trade_pct / 100`
- Shares by risk = `risk_amount / (stop_loss_pct/100 × price)`
- Cap by `max_position_size_pct` of portfolio
- Return `floor(min(shares_by_risk, shares_by_cap, cash/price))`

Used when generating signals in `strategy_service.generate_signal`.

---

## 13. Algorithm Findings & Sequential Rankings

Implemented in `algo_finding_service.py` (analytics **separate** from the three trade strategies above).

### 13.1 Stock detail — `generate_stock_algo_findings`

Requires **≥ 80** daily candles (`MIN_SIGNAL_ROWS`). Returns a list of finding dicts consumed by Explore stock detail page.

| Algorithm | Category | Status | Logic summary |
|-----------|----------|--------|-----------------|
| **VWAP** | Execution | `daily_proxy` | 20-day volume-weighted typical price vs close; BUY if >1% below, SELL if >1% above |
| **TWAP** | Execution | `daily_proxy` | 20-day time-weighted avg close vs close; thresholds ±1.25% |
| **Implementation Shortfall** | Execution | `daily_proxy` | Compare close vs 5-session arrival benchmark + SMA20 trend |
| **Pairs Trading (Cointegration)** | Stat arb | `requires_pair_asset` | Not runnable — needs second asset + cointegration test |
| **OU Process** | Mean reversion | `daily_proxy` | Ornstein-Uhlenbeck-style mean reversion on returns |
| **Kalman Filter** | State space | `daily_proxy` | Smoothed trend vs close deviation |
| **SARIMAX Proxy** | Forecasting | `daily_proxy` | Baseline next-session return from rolling mean returns |
| **GARCH Proxy** | Volatility | `daily_proxy` | Compare 20d vs 60d volatility + momentum |
| **Avellaneda-Stoikov** | Market making | `requires_order_book_data` | Needs live bid/ask + inventory |
| **Order Book Imbalance** | Market making | `requires_order_book_data` | Needs L2 order book |
| **Tree Ensemble Proxy** | ML | `daily_proxy` | Feature mix: momentum, drawdown, downside vol → score |
| **Sequence Deep Learning Proxy** | ML | `daily_proxy` | Hand-crafted sequence score over return windows (MVP LSTM placeholder) |

**Helper functions:**

| Function | Role |
|----------|------|
| `_action_from_threshold(value, buy_below, sell_above)` | Maps metric → BUY/SELL/HOLD |
| `_confidence(distance, multiplier, base)` | 0–95 confidence score |
| `_chart(...)` | Builds Plotly-ready series for frontend |
| `_not_available(...)` | Placeholder finding with `HOLD`, confidence 0 |

### 13.2 Explore rankings — `generate_sequential_rankings`

1. Count active stocks with daily closes in DB
2. For each eligible stock, compute `_sequence_deep_learning_proxy` score
3. Sort into **top_buys** and **top_sells** (default 15 each)
4. Response includes `eligible_count`, `rows_scanned`, `as_of_date`

**Important:** This is an **MVP proxy**, not a trained production LSTM. Labels in API/UI state this explicitly.

---

## 14. Backtesting Engine

**Entry:** `backtest_service.run_backtest`

```mermaid
flowchart TD
    A[Load OHLCV for date range] --> B[For each trading day]
    B --> C[Slice prices 0..t]
    C --> D[strategy.generate_signal]
    D --> E{BUY and flat?}
    E -->|yes| F[Buy 95% of cash at close]
    D --> G{SELL and long?}
    G -->|yes| H[Sell entire position]
    F --> I[Record equity = cash + qty*price]
    H --> I
    I --> B
    B --> J[Compute return, max DD, Sharpe, win rate]
    J --> K[Persist backtest_runs + backtest_trades]
```

| Rule | Detail |
|------|--------|
| Position model | Single position only (0 or long) |
| Buy sizing | 95% of available cash, floor shares |
| Prices | Daily close from `stock_prices` |
| Metrics | Total return %, max drawdown %, annualized Sharpe (252 scaling), win rate on sell trades |
| Missing data | Attempts `sync_stock_prices` with `period=5y` if DB empty |

---

## 15. REST API Reference

### Public / mixed auth

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | No | Health check |
| POST | `/auth/register` | No | Create account |
| POST | `/auth/login` | No | Login → JWT |
| GET | `/auth/debug-status` | No | Debug bypass flag |
| GET | `/strategies/templates` | No | Strategy templates |

### Authenticated (Bearer JWT unless debug bypass)

| Prefix | Key endpoints |
|--------|---------------|
| `/auth` | `GET /me`, `POST /logout` |
| `/market` | `GET /overview`, `GET /sequential-rankings` |
| `/stocks` | `GET /search`, `GET /performance`, `GET /{id}`, `GET /{id}/prices`, `GET /{id}/algo-findings`, `POST /{id}/sync-prices`, `POST /sync-all` |
| `/portfolios` | CRUD + `GET /{id}/holdings`, `GET /{id}/performance` |
| `/transactions` | `POST /manual-buy`, `POST /manual-sell`, `GET /` |
| `/paper-orders` | `POST /`, `GET /`, `POST /{id}/cancel` |
| `/paper-trades` | `GET /` |
| `/strategies` | user-strategy CRUD, `POST /generate-signal`, `GET /signals`, `POST /signals/{id}/execute-paper-order` |
| `/backtest` | `POST /run`, `GET /{id}`, `GET /{id}/trades` |

Full interactive docs: http://localhost:8000/docs

---

## 16. Streamlit Frontend

### Pages map

| File | Page name | Purpose |
|------|-----------|---------|
| `streamlit_app.py` | Home | Login, register, logout, cash metrics |
| `pages/1_Explore.py` | Explore | Market dashboard + stock detail |
| `pages/2_Add_Portfolio.py` | Add Portfolio | Create portfolio, manual buy |
| `pages/3_Paper_Trading.py` | Paper Trading | Charts, paper orders |
| `pages/4_Strategy_Lab.py` | Strategy Lab | Signals + execution |
| `pages/5_Backtesting.py` | Backtesting | Run simulations |

### `api_client.py` responsibilities

| Category | Functions |
|----------|-----------|
| HTTP | `api_request`, `get`, `post`, `auth_headers` |
| Auth | `require_login`, `clear_auth_state`, `debug_auth_enabled` |
| Formatting | `format_inr`, `format_pct`, `format_indian_number`, ... |
| Widgets | `search_stock_widget`, `portfolio_select` |
| Timing | `log_page_load`, `timed_frontend_block` |

**Rule:** Frontend never talks to PostgreSQL or yfinance directly — always through FastAPI.

---

## 17. CLI Scripts & Operations Runbook

| Script | When to run |
|--------|-------------|
| `init_db.py` | Apply migrations (`alembic upgrade head`) |
| `ingest_bootstrap.py` | One-shot: migrate + tickers + seed strategies (+ optional prices) |
| `load_indian_tickers.py` | Load/update `stocks` from online or CSV |
| `seed_strategy_templates.py` | Insert RSI/SMA/Breakout templates |
| `fetch_prices.py` | Ad-hoc price sync (single symbol or batched `--all`) |
| `ingest_market_history.py` | **Recommended** full-universe backfill with resume offsets |
| `reset_user_password.py` | Local dev password recovery |

### Recommended production-like data pipeline

```text
1. alembic upgrade head
2. load_indian_tickers.py --source online
3. seed_strategy_templates.py
4. ingest_market_history.py --exchange NSE --start-date 2010-01-01
5. ingest_market_history.py --exchange BSE --start-date 2010-01-01
6. (daily cron) ingest_market_history.py --incremental
```

### Resume interrupted backfill

Check last completed offset in `ingestion_runs.batch_offset`, then:

```powershell
docker compose run --rm backend python /app/scripts/ingest_market_history.py --exchange NSE --start-date 2010-01-01 --offset 500 --batch-size 100
```

---

## 18. Backend Function Catalog

### `app/main.py`

| Function | Purpose |
|----------|---------|
| `run_startup_migrations()` | Compare Alembic heads; upgrade if behind |
| `lifespan(app)` | Optional auto-migrate on startup |
| `log_request_timing` | Middleware: log HTTP duration |
| `_format_validation_error` | Friendly 422 messages for registration |
| `health()` | `GET /health` |

### `app/services/market_data_service.py`

| Function | Purpose |
|----------|---------|
| `ensure_daily_interval` | Enforce `1d` only |
| `previous_business_day` | T-1 weekday in IST |
| `fetch_stock_history` | Yahoo download + fallback |
| `save_stock_prices` | Upsert candles |
| `get_latest_price` / `get_last_price_date` / `get_first_price_date` | Price lookups |
| `_history_covers_requested_range` | Skip backfill if range already stored |
| `sync_stock_prices` | Chunked sync with probe + skip logic |
| `sync_all_active_stocks` | Batch sync + `ingestion_runs` |
| `prices_to_dataframe` | ORM → pandas |

### `app/services/stock_performance_service.py`

| Function | Purpose |
|----------|---------|
| `list_stock_performance` | SQL for All stocks table (% changes) |

### `app/services/ticker_service.py`

| Function | Purpose |
|----------|---------|
| `normalize_nse_symbol` / `normalize_bse_symbol` | Yahoo suffix |
| `upsert_stock` | Insert/update ticker |
| `search_stocks` | Search API |

### `app/services/portfolio_service.py`

| Function | Purpose |
|----------|---------|
| `create_default_portfolios_for_user` | Manual + Paper portfolios |
| `update_holding_after_buy` / `update_holding_after_sell` | Cost basis math |
| `add_manual_buy` / `add_manual_sell` | Manual transactions |
| `calculate_portfolio_value` | NAV, holdings breakdown |
| `generate_daily_snapshot` | Upsert daily snapshot row |

### `app/services/paper_trading_service.py`

| Function | Purpose |
|----------|---------|
| `place_paper_order` | MARKET immediate fill; LIMIT/STOP pending |
| `cancel_paper_order` | Cancel PENDING only |

### `app/services/strategy_service.py`

| Function | Purpose |
|----------|---------|
| `get_strategy_instance` | Strategy factory |
| `create_user_strategy` | Bind template to user |
| `generate_signal` | Run strategy + position size |
| `execute_signal_as_paper_order` | Signal → paper order |

### `app/services/market_overview_service.py`

| Function | Purpose |
|----------|---------|
| `get_market_overview` | Indices + movers + caching + fallbacks |

### `app/services/backtest_service.py`

| Function | Purpose |
|----------|---------|
| `_load_prices_for_range` | OHLCV for backtest window |
| `run_backtest` | Simulation engine |

### `app/services/algo_finding_service.py`

| Function | Purpose |
|----------|---------|
| `_daily_vwap` … `_sequence_deep_learning_proxy` | Individual algo findings |
| `generate_stock_algo_findings` | All findings for one stock |
| `generate_sequential_rankings` | Universe ranking |

### `app/strategies/*.py`

| Class / Function | Purpose |
|------------------|---------|
| `BaseStrategy.merged_parameters` | Merge defaults + user overrides |
| `BaseStrategy.generate_signal` | Abstract signal method |
| `RSIStrategy.generate_signal` | RSI rules |
| `SMACrossoverStrategy.generate_signal` | SMA cross rules |
| `BreakoutStrategy.generate_signal` | Breakout + volume rules |
| `calculate_position_size` | Risk-based share count |

### `app/security.py`

| Function | Purpose |
|----------|---------|
| `verify_password` / `get_password_hash` | bcrypt |
| `create_access_token` / `decode_token_payload` | JWT |
| `get_current_user` | Dependency for protected routes |
| `get_debug_user` | Debug bypass user provisioning |

---

## 19. Observability & Logging

### Backend timing log format

```text
operation=http_request method=GET path=/stocks/performance status_code=200 duration_ms=5.24
operation=market_data.fetch_stock_history status=ok duration_ms=1135.88
operation=stocks.list_stock_performance status=ok duration_ms=1.12
```

Enabled via `@timed("operation.name")` in `app/utils/observability.py`.

### Frontend timing

```text
operation=api_request method=GET path=/market/overview status_code=200 duration_ms=5.61
operation=page_load page=Explore status=ok duration_ms=165.52
```

---

## 20. Known Limitations & Production Gaps

| Area | Limitation |
|------|------------|
| Market data | Yahoo Finance daily candles only; not live exchange feed |
| Orders | LIMIT/STOP stored but not matched intraday |
| Costs | Simplified charges; no STT/GST/slippage model |
| Corporate actions | No split/dividend adjustments beyond Yahoo `Adj Close` |
| BSE coverage | Many scrip codes return empty from Yahoo |
| Algos | Several findings are **proxies** or marked `requires_*` |
| Pairs / market making | Need data types we do not store |
| Auth | No password reset API yet (`password_reset_tokens` unused) |
| Backtest | Single-position, daily bar, no shorting |
| Scale | Full 2010 backfill for 6k+ symbols takes hours/days |

---

## 21. Intern Onboarding Checklist

- [ ] Read this document and skim `README.md`
- [ ] Run `docker compose up --build` and open http://localhost:8501
- [ ] Load tickers: `load_indian_tickers.py --source online`
- [ ] Seed strategies: `seed_strategy_templates.py`
- [ ] Start NSE price backfill: `ingest_market_history.py --exchange NSE --start-date 2010-01-01`
- [ ] Register a real user (or use debug bypass in Docker)
- [ ] Explore → verify All stocks tab fills in over time
- [ ] Add a manual holding on **Add Portfolio**
- [ ] Place a paper MARKET order on **Paper Trading**
- [ ] Create user strategy + generate signal on **Strategy Lab**
- [ ] Run a backtest on **Backtesting**
- [ ] Open http://localhost:8000/docs and call `GET /stocks/performance` with token
- [ ] Inspect DB: `\dt` and `SELECT COUNT(*) FROM stock_prices`
- [ ] Read `ingestion_runs` for batch job status

---

## Quick reference — connection string

```text
postgresql+psycopg2://postgres:postgres@localhost:5432/paper_trading
```

---

*Last updated to reflect codebase including: full ticker load, `ingest_market_history.py`, stock performance API, Explore All stocks tab, and yfinance backfill optimizations (recent probe + skip covered history).*
