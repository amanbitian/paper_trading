# paper_trading_app

Local-first paper trading and portfolio tracking for Indian equities.

Stack:

- Python
- FastAPI backend
- FastAPI web UI
- PostgreSQL
- SQLAlchemy ORM
- Alembic migrations
- yfinance for Yahoo Finance price data
- NSE/BSE stock tickers plus index/commodity Yahoo tickers
- Plotly charts
- pandas analytics

Disclaimer: this is a paper trading and educational tool. It does not provide financial advice.

## Architecture

- `backend/app/main.py` creates the FastAPI app and mounts routers.
- `backend/app/models` defines SQLAlchemy tables.
- `backend/app/schemas` defines Pydantic request and response models.
- `backend/app/services` contains business logic for prices, portfolios, paper orders, strategies, and backtests.
- `backend/app/strategies` contains RSI, SMA crossover, breakout, execution algo proxies, statistical models, ML-style proxies, and risk sizing logic.
- `backend/app/templates` and `backend/app/static` contain the FastAPI web UI.
- `scripts` contains CLI helpers for migrations, ticker loading, price fetching, and strategy seeding.
- `data` contains sample NSE/BSE ticker CSV files and index/commodity ticker CSV data.

## Authentication Storage

Authentication data is separated from user profile data:

- `users` stores identity/profile fields such as `name`, unique public `user_name`, email, cash balance, and risk profile.
- `user_credentials` stores only the bcrypt `password_hash` for each user. Plain passwords are never stored.
- `auth_sessions` stores a hashed JWT ID (`token_jti_hash`) for each login session, with expiry and revocation timestamps.
- `password_reset_tokens` is reserved for a future reset-password flow and stores only token hashes.

`name` is the person's name. `user_name` is the unique handle shown on the platform, for example `@aman_123`, so multiple people with the same real name can still be differentiated.

On login, the backend verifies the password hash, creates an `auth_sessions` row, and returns a JWT. Protected APIs validate both the JWT signature and the server-side session record. Logout revokes the current session.

## Runtime Timing Logs

The app logs runtime for critical flows in local logs.

Backend request timing:

```text
operation=http_request method=POST path=/auth/register status_code=201 duration_ms=123.45
```

Backend service/function timing:

```text
operation=portfolio.calculate_portfolio_value status=ok duration_ms=35.12
operation=market_data.fetch_stock_history status=ok duration_ms=890.44
operation=backtest.run_backtest status=ok duration_ms=1420.91
```

Frontend API and page timing:

```text
operation=api_request method=GET path=/portfolios/1/performance status_code=200 duration_ms=84.77
operation=page_load page=Explore status=ok duration_ms=210.34
```

These logs cover registration, login, dashboard loading, yfinance fetching/syncing, portfolio calculations, manual transactions, paper orders, strategy signals, and backtests.

## Quick start (run script)

**1. Start Docker Desktop** and wait until it shows **Engine running** (whale icon in the system tray).

**2. Check setup:**

```powershell
cd "C:\Users\Aman\Documents\New project\paper_trading_app"
py -3 scripts/run.py check
```

**3. Start the app:**

```powershell
py -3 scripts/run.py -d
```

Or on Windows:

```powershell
.\run.ps1 -d
```

Open http://localhost:8000/web/explore

**4. Optional: load index funds and commodities**

For a quick smoke test:

```powershell
py -3 scripts/run.py index-funds --limit 3
```

For the full index/commodity universe:

```powershell
py -3 scripts/run.py index-funds
```

For daily updates after the first backfill:

```powershell
py -3 scripts/run.py ingest-index-funds --incremental
```

**5. Optional: tag stocks by index membership**

This loads NIFTY 50, NIFTY 100, NIFTY 200, NIFTY 500, BANKNIFTY, FINNIFTY,
MIDCPNIFTY, and Sensex membership tags into the DB:

```powershell
py -3 scripts/run.py load-index-memberships
```

If online NSE downloads are blocked, use the bundled fallback sample:

```powershell
py -3 scripts/run.py load-index-memberships --membership-source csv
```

### Troubleshooting

| Problem | Fix |
|--------|-----|
| `python was not found` | Use `py -3` instead of `python` on Windows |
| `failed to connect to the docker API` | Start **Docker Desktop** and wait until the engine is running |
| Port 8000 in use | Stop local `uvicorn` or old containers: `py -3 run.py stop` |
| Web UI not loading | Wait for backend health; check `py -3 run.py logs` |

Other commands:

```powershell
py -3 scripts/run.py stop      # stop containers
py -3 scripts/run.py status    # show container status
py -3 scripts/run.py logs      # follow backend logs
py -3 scripts/run.py migrate   # run Alembic migrations
py -3 scripts/run.py load-index-funds
py -3 scripts/run.py ingest-index-funds --start-date 2010-01-01
py -3 scripts/run.py load-index-memberships
```

Fundamentals are synced into the latest-snapshot table `stock_fundamentals_latest` after the normal market sync. For a small manual test without clicking the UI:

```powershell
cd "C:\Users\Aman\Documents\New project\paper_trading_app"
.\backend\.venv\Scripts\python.exe backend\scripts\sync_fundamentals_once.py --limit 5
.\backend\.venv\Scripts\python.exe backend\scripts\sync_fundamentals_once.py --all
```

Deep historical statement rows are stored in `stock_financials`. Start with a small Screener export sync before attempting the full active universe:

```powershell
.\backend\.venv\Scripts\python.exe backend\scripts\sync_historical_fundamentals.py --symbol RELIANCE
.\backend\.venv\Scripts\python.exe backend\scripts\sync_historical_fundamentals.py --limit 5
```

The historical sync prints a terminal progress bar and checkpoints after every symbol to `data/ingestion_checkpoints/historical_fundamentals_screener.json`. Re-running the same command resumes by skipping completed symbols; pass `--no-resume` to force a fresh attempt.

The `Quality Momentum` strategy uses long-term price momentum, trend, volatility, liquidity, ATR risk controls, and available point-in-time fundamental quality data from `stock_financials`.

Stock detail pages can be precomputed into `stock_detail_snapshots` so opening a stock mostly reads cached chart payloads, algorithm findings, fundamentals, news, and strategy options:

```powershell
.\backend\.venv\Scripts\python.exe backend\scripts\refresh_stock_detail_snapshots.py --symbol RELIANCE --exchange NSE
.\backend\.venv\Scripts\python.exe backend\scripts\refresh_stock_detail_snapshots.py --exchange NSE --limit 100
```

## Docker Setup

From the parent workspace:

```powershell
cd "C:\Users\Aman\Documents\New project\paper_trading_app"
docker compose up --build
```

Open:

- Web UI (default): http://localhost:8000/web/explore
- FastAPI: http://localhost:8000
- API docs: http://localhost:8000/docs

The backend container runs `alembic upgrade head` before starting Uvicorn.

## Exact PostgreSQL Commands

Start only PostgreSQL:

```powershell
cd "C:\Users\Aman\Documents\New project\paper_trading_app"
docker compose up -d postgres
```

Connection URL:

```text
postgresql+psycopg2://postgres:postgres@localhost:5432/paper_trading
```

## Exact Docker Migration And Seed Commands

Run migrations manually:

```powershell
cd "C:\Users\Aman\Documents\New project\paper_trading_app"
docker compose run --rm backend alembic upgrade head
```

Load sample Indian tickers:

```powershell
docker compose run --rm backend python /app/scripts/load_indian_tickers.py --source csv
```

Try online ticker loading with CSV fallback:

```powershell
docker compose run --rm backend python /app/scripts/load_indian_tickers.py --source online
```

Seed strategy templates:

```powershell
docker compose run --rm backend python /app/scripts/seed_strategy_templates.py
```

Load index/commodity tickers:

```powershell
py -3 scripts/run.py load-index-funds
```

Backfill index/commodity daily history from 2010 through T-1:

```powershell
py -3 scripts/run.py ingest-index-funds --start-date 2010-01-01 --chunk-days 365 --sleep-seconds 1
```

If index/commodity rows were accidentally loaded into a separate PostgreSQL database named
`index_funds`, migrate them into the main `paper_trading` database without re-calling Yahoo:

```powershell
python scripts/migrate_index_funds_to_main_db.py --source-url postgresql+psycopg2://postgres:postgres@localhost:5432/index_funds --target-url postgresql+psycopg2://postgres:postgres@localhost:5432/paper_trading
```

Run daily incremental index/commodity sync:

```powershell
py -3 scripts/run.py ingest-index-funds --incremental --sleep-seconds 1
```

Run the independent bootstrap ingestion script in Docker:

```powershell
docker compose run --rm backend python /app/scripts/ingest_bootstrap.py --source csv
```

Run bootstrap plus limited price sync:

```powershell
docker compose run --rm backend python /app/scripts/ingest_bootstrap.py --source csv --sync-prices --price-limit 25
```

Run bootstrap and load index/commodity tickers too:

```powershell
docker compose run --rm backend python /app/scripts/ingest_bootstrap.py --source csv --load-index-funds
```

Load stock index membership tags:

```powershell
py -3 scripts/run.py load-index-memberships
```

Fetch prices for one symbol:

```powershell
docker compose run --rm backend python /app/scripts/fetch_prices.py --symbol RELIANCE --period 1y
```

Fetch long Yahoo Finance history for one symbol:

```powershell
docker compose run --rm backend python /app/scripts/fetch_prices.py --symbol RELIANCE --years 15
```

Fetch prices for active stocks with a cap:

```powershell
docker compose run --rm backend python /app/scripts/fetch_prices.py --all --limit 25
```

Fetch long Yahoo Finance history for active NSE stocks with a cap:

```powershell
docker compose run --rm backend python /app/scripts/fetch_prices.py --all --exchange NSE --limit 25 --years 15
```

Batch a larger NSE ingestion:

```powershell
docker compose run --rm backend python /app/scripts/fetch_prices.py --all --exchange NSE --limit 100 --offset 0 --years 15 --chunk-days 365 --sleep-seconds 1
docker compose run --rm backend python /app/scripts/fetch_prices.py --all --exchange NSE --limit 100 --offset 100 --years 15 --chunk-days 365 --sleep-seconds 1
```

Run daily incremental sync through T-1:

```powershell
docker compose run --rm backend python /app/scripts/fetch_prices.py --all --exchange NSE --limit 100 --offset 0 --incremental --chunk-days 365 --sleep-seconds 1
```

## Local Backend Setup

Use `py -3` instead of `python` on Windows if `python` is not on PATH.

```powershell
cd "C:\Users\Aman\Documents\New project\paper_trading_app\backend"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

For anything beyond local development, change `JWT_SECRET_KEY` in `backend/.env`. If `APP_ENV=production`, the backend refuses to start with the sample secret.

`AUTO_MIGRATE_ON_START=false` is the default. This keeps startup predictable and avoids hidden migration work during registration/login requests. Run migrations explicitly before starting FastAPI, or use the independent bootstrap ingestion script from the project root to check the DB connection, apply migrations, load tickers, and seed strategies.

Enable `AUTO_MIGRATE_ON_START=true` only for throwaway local experiments where you intentionally want FastAPI startup to run Alembic.

Local migration command:

```powershell
alembic upgrade head
```

If you see an error like `column users.name does not exist`, your database has old schema. Run:

```powershell
cd "C:\Users\Aman\Documents\New project\paper_trading_app\backend"
alembic upgrade head
```

Alternative migration helper from the project root:

```powershell
cd "C:\Users\Aman\Documents\New project\paper_trading_app"
python scripts/init_db.py
```

Start FastAPI:

```powershell
cd "C:\Users\Aman\Documents\New project\paper_trading_app\backend"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Local Script Commands

Run these from the project root with the backend virtual environment active:

```powershell
cd "C:\Users\Aman\Documents\New project\paper_trading_app"
python scripts/ingest_bootstrap.py --source csv
python scripts/load_indian_tickers.py --source csv
python scripts/load_indian_tickers.py --source online
python scripts/seed_strategy_templates.py
python scripts/load_index_funds.py --csv-path data/indexes_commodities_prepared.csv
python scripts/ingest_index_funds.py --start-date 2010-01-01 --chunk-days 365 --sleep-seconds 1
python scripts/fetch_prices.py --symbol RELIANCE --period 1y
python scripts/fetch_prices.py --symbol RELIANCE --years 15
python scripts/fetch_prices.py --all --limit 25
python scripts/fetch_prices.py --all --exchange NSE --limit 25 --years 15
python scripts/fetch_prices.py --all --exchange NSE --limit 100 --offset 0 --years 15 --chunk-days 365 --sleep-seconds 1
python scripts/fetch_prices.py --all --exchange NSE --limit 100 --offset 0 --incremental --chunk-days 365 --sleep-seconds 1
```

The independent bootstrap ingestion command checks the database connection, applies Alembic migrations if tables are missing or pending, validates expected tables/columns/unique constraints, loads ticker CSV data from `data/`, and seeds strategy templates:

```powershell
python scripts/ingest_bootstrap.py --source csv
```

Bootstrap plus index/commodity tickers:

```powershell
python scripts/ingest_bootstrap.py --source csv --load-index-funds
```

Optional price sync:

```powershell
python scripts/ingest_bootstrap.py --source csv --sync-prices --price-limit 25
```

## Ticker And Yahoo Finance Ingestion Flow

Ticker loading and price ingestion are separate steps:

1. `scripts/load_indian_tickers.py` loads NSE/BSE symbols from online exchange CSVs or local sample CSV files in `data/`.
2. `backend/app/services/ticker_service.py` normalizes each symbol into the Yahoo Finance format:
   - NSE: `RELIANCE` becomes `RELIANCE.NS`
   - BSE: `500325` becomes `500325.BO`
3. The script upserts rows into `stocks` using unique constraints on `(symbol, exchange)` and `yahoo_symbol`.
4. `scripts/fetch_prices.py` reads active rows from `stocks`.
5. For each row, it passes `stocks.yahoo_symbol` into `yfinance`.
6. `backend/app/services/market_data_service.py` stores daily OHLCV rows in `stock_prices` with `timeframe='1d'`.
7. Duplicate candles are avoided with the unique key `(stock_id, price_datetime, timeframe)` and PostgreSQL upsert.
8. Bulk price jobs write an audit row to `ingestion_runs` with attempted symbols, successes, failures, saved rows, and status.
9. Use `--limit` and `--offset` for repeatable symbol batches when syncing large NSE/BSE universes.
10. Use `--chunk-days 365` for first historical loads so each yfinance call requests at most one year of daily candles.
11. Use `--incremental` for ongoing ingestion; it checks the latest stored daily candle per stock and fetches only from `last_date + 1` through T-1.
12. If Yahoo returns a delisted/no-timezone ticker error, the stock is marked `is_delisted=true`, `is_active=false`, appended to `constant.txt`, and skipped by future ingestion runs.

This MVP intentionally supports only daily Yahoo Finance candles. Minute/intraday history is not stored because Yahoo Finance limits intraday history heavily and it would require a different storage and scheduling plan.

Long-history examples:

```powershell
python scripts/fetch_prices.py --symbol RELIANCE --years 10
python scripts/fetch_prices.py --symbol RELIANCE --years 15
python scripts/fetch_prices.py --all --exchange NSE --limit 25 --years 15
python scripts/fetch_prices.py --all --exchange BSE --limit 25 --years 15
python scripts/fetch_prices.py --all --exchange NSE --limit 100 --offset 0 --years 15 --chunk-days 365 --sleep-seconds 1
python scripts/fetch_prices.py --all --exchange NSE --limit 100 --offset 100 --years 15 --chunk-days 365 --sleep-seconds 1
```

You can also pass explicit dates:

```powershell
python scripts/fetch_prices.py --symbol RELIANCE --start-date 2010-01-01 --end-date 2026-05-13
```

Ongoing daily ingestion examples:

```powershell
python scripts/fetch_prices.py --symbol RELIANCE --incremental --sleep-seconds 1
python scripts/fetch_prices.py --all --exchange NSE --limit 100 --offset 0 --incremental --sleep-seconds 1
```

For `--incremental`, the service reads `max(stock_prices.price_datetime)` for each stock at `timeframe='1d'`. If the stock is already current through T-1, it is skipped. Otherwise it fetches daily candles from `last_date + 1` to T-1 and upserts them, so reruns are safe.

The Explore page uses T-1 daily candles. It first tries Yahoo Finance, then stored `stock_prices`, then a clearly marked sample fallback if live data is unavailable.

## Index Fund And Commodity Ingestion Flow

Index and commodity tickers are loaded into separate tables, not mixed into `stocks`:

1. `scripts/load_index_funds.py` reads `data/indexes_commodities_prepared.csv`.
2. Rows are upserted into `index_funds` using unique `symbol` and `yahoo_symbol`.
3. `scripts/ingest_index_funds.py` reads active rows from `index_funds`.
4. For each row, it passes `index_funds.yahoo_symbol` into yfinance.
5. Daily OHLCV rows are stored in `index_fund_prices`.
6. Duplicate candles are avoided with the unique key `(index_fund_id, price_datetime, timeframe)`.
7. Initial backfill should use `--start-date 2010-01-01 --chunk-days 365`.
8. Ongoing ingestion should use `--incremental`, which fetches from `last stored date + 1` through T-1.

Local commands:

```powershell
python scripts/load_index_funds.py --csv-path data/indexes_commodities_prepared.csv
python scripts/ingest_index_funds.py --start-date 2010-01-01 --chunk-days 365 --sleep-seconds 1
python scripts/ingest_index_funds.py --incremental --sleep-seconds 1
python scripts/migrate_index_funds_to_main_db.py --source-url postgresql+psycopg2://postgres:postgres@localhost:5432/index_funds --target-url postgresql+psycopg2://postgres:postgres@localhost:5432/paper_trading
```

The FastAPI web **Index Fund** page shows the stored universe, 1M/3M/6M/1Y return changes, individual historical candles, strategy previews, algorithm findings, and multi-index return comparison plots.

## Alembic Development Commands

Create a future migration after model changes:

```powershell
cd "C:\Users\Aman\Documents\New project\paper_trading_app\backend"
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

## Example Flow

1. Start PostgreSQL and run migrations.
2. Register a user in the web UI or call `POST /auth/register`.
3. Load Indian tickers with `python scripts/load_indian_tickers.py --source csv`.
4. Seed strategies with `python scripts/seed_strategy_templates.py`.
5. Search `RELIANCE` with `GET /stocks/search?query=RELIANCE`.
6. Sync prices with `POST /stocks/{stock_id}/sync-prices`.
7. Add a manual holding from the Add Portfolio page.
8. Place a paper market order from the Paper Trading page.
9. Generate a strategy signal and optionally execute it as a paper order.
10. Run a backtest from the Backtesting page.
11. Load index tickers, ingest index history, compare returns on the Index Fund page, and run stock/index backtests from Backtesting.

## Known Limitations

- Limit and stop-loss orders are stored as pending placeholders; only market orders execute immediately.
- Prices come from yfinance and use latest stored close, not live exchange feeds.
- Brokerage, taxes, and slippage are modeled deterministically for research, but are still approximations.
- Backtesting is daily and single-position. Signals execute through explicit execution modes, conservative intrabar assumptions, transaction costs, and optional benchmark comparison, but corporate actions and liquidity constraints remain simplified.
- Index/commodity ingestion uses Yahoo Finance daily candles only; intraday and order-book strategies are shown as transparent daily proxies or marked as requiring extra data.
- Online ticker downloads may be blocked by exchange websites; CSV fallback is included.
- There is no rate limiting or password reset flow in the MVP.
