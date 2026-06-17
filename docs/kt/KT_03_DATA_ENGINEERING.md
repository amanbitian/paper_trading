# KT-03: Data Engineering Knowledge Transfer
### Paper Trading App — New Intern Onboarding Guide

---

## Table of Contents
1. [What is Data Engineering in This Project?](#1-what-is-data-engineering-in-this-project)
2. [Data Sources](#2-data-sources)
3. [Data Pipeline Architecture](#3-data-pipeline-architecture)
4. [Database Schema (Data Perspective)](#4-database-schema-data-perspective)
5. [Ingestion Scripts Deep Dive](#5-ingestion-scripts-deep-dive)
6. [Data Flow Diagrams](#6-data-flow-diagrams)
7. [Batch Processing Patterns](#7-batch-processing-patterns)
8. [Data Quality & Validation](#8-data-quality--validation)
9. [Market Data Service](#9-market-data-service)
10. [News Ingestion Pipeline](#10-news-ingestion-pipeline)
11. [Index & Constituent Loading](#11-index--constituent-loading)
12. [Analytics & Caching Layer](#12-analytics--caching-layer)
13. [How to Run Pipelines](#13-how-to-run-pipelines)
14. [Common Tasks for Interns](#14-common-tasks-for-interns)
15. [Database Connection Configuration](#15-database-connection-configuration)
    - 15.1 [How the App Connects to PostgreSQL](#151-how-the-app-connects-to-postgresql)
    - 15.2 [How to Connect Manually](#152-how-to-connect-manually)
    - 15.3 [Per-Table Configuration & Access Reference](#153-per-table-configuration--access-reference)
    - 15.4 [Complete Table Inventory](#154-complete-table-inventory)

---

## 1. What is Data Engineering in This Project?

Data engineering here means **getting market data into the database** so the trading, backtesting, strategy, and analytics features have real data to work with.

The data engineering work covers:
- **ETL**: Extract price data from Yahoo Finance → Transform into OHLCV bars → Load into PostgreSQL
- **Batch ingestion**: Load thousands of stock tickers and their prices in bulk
- **News pipeline**: Fetch articles from external APIs, deduplicate, link to stocks, score sentiment
- **Index management**: Load NSE/BSE index constituents from CSV files
- **Analytics cache**: Pre-compute expensive aggregations for fast UI queries

Without data engineering, the app has no prices, no stocks, no news, and no market history.

---

## 2. Data Sources

```mermaid
graph LR
    subgraph "External Data Sources"
        YF["Yahoo Finance\n(yfinance library)\nFree, no API key\nOHLCV prices, fundamentals"]
        MKTAUX["MarketAux API\n(news articles)\nPaid API, key required\n1000 req/day free tier"]
        AV["Alpha Vantage API\n(news articles)\nPaid API, key required\n25 req/day free tier"]
        NSE_CSV["NSE Index CSVs\ndata/nse_index_csvs/\nManual download from NSE website\nIndex constituent lists"]
        TICKER_CSV["NSE/BSE Ticker CSVs\ndata/nse_tickers_sample.csv\ndata/bse_tickers_sample.csv\nStock symbol master list"]
    end

    subgraph "Our Database"
        Stocks["stocks table\n~5000+ Indian stocks"]
        Prices["stock_prices table\nDaily OHLCV bars"]
        News["stock_news_articles\nstock_news_links"]
        Indices["market_indices\nstock_index_memberships"]
        Funds["index_funds\nindex_fund_prices"]
        Fundamentals["stock_fundamentals_latest\nPE, ROE, market cap"]
    end

    YF -->|"fetch_prices.py\nmarket_data_service.py"| Prices
    YF -->|"fundamentals_service.py"| Fundamentals
    MKTAUX -->|"news_service.py"| News
    AV -->|"news_service.py"| News
    NSE_CSV -->|"load_index_memberships.py"| Indices
    TICKER_CSV -->|"load_indian_tickers.py"| Stocks

    style YF fill:#27ae60,color:#fff
    style MKTAUX fill:#e67e22,color:#fff
    style AV fill:#e67e22,color:#fff
    style NSE_CSV fill:#3498db,color:#fff
    style TICKER_CSV fill:#3498db,color:#fff
```

### Data Source Details

| Source | What We Get | Format | Frequency | Cost |
|--------|------------|--------|-----------|------|
| Yahoo Finance (yfinance) | OHLCV prices, fundamentals | Python library | Daily (auto) | Free |
| MarketAux API | News articles + sentiment | REST JSON | On-demand | Free tier: 1000/day |
| Alpha Vantage API | News articles + sentiment | REST JSON | On-demand | Free tier: 25/day |
| NSE Index CSVs | Index constituents (NIFTY50, etc.) | CSV files | Manual (monthly) | Free (NSE website) |
| NSE/BSE Ticker CSVs | Stock symbol master | CSV files | Manual (quarterly) | Free |

---

## 3. Data Pipeline Architecture

```mermaid
flowchart TD
    subgraph "Bootstrap (One-time Setup)"
        B1["load_indian_tickers.py\nLoad NSE/BSE stock list\nfrom CSV → stocks table"]
        B2["load_index_memberships.py\nLoad NIFTY50/100/500\nconstituents from CSV"]
        B3["ingest_index_funds.py\nLoad NIFTY, SENSEX,\nGold, Silver ETFs"]
        B4["seed_strategy_templates.py\nCreate default RSI/MACD/SMA\nstrategy templates"]
        B5["init_db.py\nCreate all DB tables\n(alembic upgrade head)"]
        B5 --> B1 --> B2 --> B3 --> B4
    end

    subgraph "Daily Ingestion (Recurring)"
        D1["fetch_prices.py\nPull OHLCV from yfinance\nfor all active stocks"]
        D2["ingest_stock_news.py\nFetch news from MarketAux\n+ Alpha Vantage"]
        D3["refresh_analytics.py\nRe-compute performance\nsnapshots & caches"]
        D4["ingest_market_history.py\nBackfill historical\nprices if gaps found"]
        D1 --> D3
        D2 --> D3
    end

    subgraph "Priority Ingestion"
        P1["ingest_priority.py\nFetch prices for\nhigh-priority stocks\n(NIFTY50 first)"]
        P1 --> D1
    end

    subgraph "Database"
        DB[("PostgreSQL\nstocks\nstock_prices\nstock_news_articles\nstock_index_memberships\nmarket_analytics_cache")]
    end

    Bootstrap --> DB
    D1 --> DB
    D2 --> DB
    D3 --> DB
    P1 --> DB

    subgraph "Monitoring"
        M1["ingestion_runs table\nTracks every batch:\nstatus, counts, errors"]
        DB --> M1
    end
```

---

## 4. Database Schema (Data Perspective)

### Tables Owned by Data Engineering

```mermaid
erDiagram
    STOCKS {
        int id PK
        string symbol
        string yahoo_symbol
        string exchange
        string company_name
        string sector
        string industry
        bool is_nifty50
        bool is_nifty100
        bool is_nifty200
        bool is_nifty500
        bool is_sensex30
        bool is_banknifty
        bool is_finnifty
        bool is_delisted
        datetime delisted_at
        datetime created_at
    }

    STOCK_PRICES {
        int id PK
        int stock_id FK
        date price_datetime
        float open
        float high
        float low
        float close
        bigint volume
        string timeframe
        string source
        datetime created_at
    }

    INGESTION_RUNS {
        int id PK
        string run_type
        string status
        int total_stocks
        int success_count
        int failed_count
        int rows_saved
        int batch_offset
        int batch_limit
        string error_message
        datetime started_at
        datetime completed_at
    }

    STOCK_PERFORMANCE_SNAPSHOTS {
        int id PK
        int stock_id FK
        float return_1m_pct
        float return_3m_pct
        float return_6m_pct
        float return_1y_pct
        float return_ytd_pct
        date as_of_date
        datetime created_at
    }

    MARKET_ANALYTICS_CACHE {
        int id PK
        string cache_key UK
        json data
        datetime expires_at
        datetime created_at
    }

    MARKET_INDICES {
        int id PK
        string index_code UK
        string index_name
        string yahoo_symbol
        datetime created_at
    }

    STOCK_INDEX_MEMBERSHIPS {
        int id PK
        int stock_id FK
        int index_id FK
        float weight_pct
        date effective_date
        date exit_date
        bool is_current
    }

    INDEX_FUNDS {
        int id PK
        string symbol UK
        string yahoo_symbol
        string full_name
        string category
        float latest_price
        datetime last_synced_at
    }

    INDEX_FUND_PRICES {
        int id PK
        int fund_id FK
        date price_datetime
        float open
        float high
        float low
        float close
        bigint volume
    }

    STOCK_FUNDAMENTALS_LATEST {
        int id PK
        int stock_id FK
        float pe_ratio
        float pb_ratio
        float roe_pct
        float debt_to_equity
        float market_cap
        float dividend_yield
        float revenue_growth
        float earnings_growth
        json raw_json
        string fetch_status
        datetime fetched_at
    }

    STOCKS ||--o{ STOCK_PRICES : "has OHLCV bars"
    STOCKS ||--o{ STOCK_PERFORMANCE_SNAPSHOTS : "cached returns"
    STOCKS ||--o{ STOCK_INDEX_MEMBERSHIPS : "member of"
    STOCKS ||--|| STOCK_FUNDAMENTALS_LATEST : "has fundamentals"
    MARKET_INDICES ||--o{ STOCK_INDEX_MEMBERSHIPS : "contains"
    INDEX_FUNDS ||--o{ INDEX_FUND_PRICES : "has prices"
    INGESTION_RUNS }o--|| STOCKS : "tracks ingestion of"
```

### OHLCV Data Layout

```
stock_prices table — one row per stock per day:

stock_id | price_datetime | open    | high    | low     | close   | volume     | timeframe | source
---------|----------------|---------|---------|---------|---------|------------|-----------|--------
   1     | 2024-01-02     | 2345.50 | 2389.00 | 2310.25 | 2375.80 | 12,450,000 | 1d        | yfinance
   1     | 2024-01-03     | 2378.00 | 2412.50 | 2355.10 | 2401.20 | 10,890,000 | 1d        | yfinance
   2     | 2024-01-02     | 1567.30 | 1589.00 | 1543.20 | 1581.40 |  8,230,000 | 1d        | yfinance
...
```

---

## 5. Ingestion Scripts Deep Dive

### scripts/ Directory

```
scripts/
├── init_db.py                    ← Initialize DB schema (run once)
├── ingest_bootstrap.py           ← Full first-time setup orchestrator
├── ingest_priority.py            ← Prioritized daily price fetch
├── ingest_market_history.py      ← Historical backfill
├── ingest_stock_news.py          ← Fetch news articles
├── ingest_index_funds.py         ← Load index fund metadata
├── fetch_prices.py               ← Core price fetching function
├── load_indian_tickers.py        ← Load stock symbol master from CSV
├── load_index_memberships.py     ← Load NSE index constituents
├── load_index_funds.py           ← Utility: load index fund list
├── create_nse_index_csv_tables.py ← Parse raw NSE CSVs into DB-ready format
├── enrich_stock_metadata.py      ← Add sector/industry via yfinance
├── migrate_index_funds_to_main_db.py ← One-time migration utility
├── seed_strategy_templates.py    ← Create built-in strategy templates
├── refresh_analytics.py          ← Recompute caches
├── debug_sync_probe.py           ← Debug sync issues
├── run_sync_once.py              ← One-off manual sync
└── reset_user_password.py        ← Admin utility
```

### fetch_prices.py (Core Ingestion)

This is the heart of the data engineering pipeline. It calls yfinance for each stock:

```
Input:  list of stock symbols (yahoo_symbol like "RELIANCE.NS")
        date range (start_date, end_date)
        interval ("1d", "1wk", "1mo")

Process:
  1. Build batches of N stocks (to avoid yfinance rate limits)
  2. Call yfinance.download(symbols, start, end, interval)
  3. Parse returned DataFrame (MultiIndex if multi-ticker)
  4. Flatten to rows: (stock_id, date, O, H, L, C, V)
  5. Upsert to stock_prices (INSERT ... ON CONFLICT DO UPDATE)
  6. Track results in ingestion_runs table

Output: rows saved to stock_prices table
```

```mermaid
flowchart TD
    A[fetch_prices.py starts] --> B[Query DB: get all stocks\nwhere is_delisted=False]
    B --> C[Split into batches of 50]
    C --> D{More batches?}
    D -->|Yes| E[yfinance.download\nbatch of symbols]
    E --> F{API returned data?}
    F -->|Yes| G[Parse MultiIndex DataFrame\ninto rows]
    F -->|No/Error| H[Log failed symbols\nmark in ingestion_run]
    G --> I[Upsert to stock_prices\nON CONFLICT: update OHLCV]
    I --> J[Increment rows_saved\nin ingestion_runs]
    J --> D
    D -->|No more| K[Update ingestion_run status='completed']
    K --> L[Done]
    H --> D
```

### ingest_priority.py (Priority Queue Pattern)

Not all stocks are equally important. Priority ingestion fetches high-priority indices first:

```
Priority tiers:
  Tier 1 (Highest): NIFTY50 stocks (50 stocks)
  Tier 2:           BANKNIFTY stocks (12 stocks)
  Tier 3:           NIFTY100 stocks (remaining 50)
  Tier 4:           NIFTY500 stocks (remaining 400)
  Tier 5 (Lowest):  All other NSE/BSE stocks

Why this matters:
  - If daily sync runs out of time, at least NIFTY50 is fresh
  - Index tracking, strategy signals need NIFTY50 first
  - Total stocks: ~5000+, but NIFTY50 needed first
```

---

## 6. Data Flow Diagrams

### Complete ETL Flow for Stock Prices

```mermaid
sequenceDiagram
    participant Script as fetch_prices.py
    participant DB as PostgreSQL
    participant YF as Yahoo Finance API
    participant IR as ingestion_runs table
    participant SP as stock_prices table

    Script->>IR: INSERT ingestion_run (status='running')
    Script->>DB: SELECT yahoo_symbol FROM stocks WHERE is_delisted=False
    DB-->>Script: 5,234 stock symbols

    loop For each batch of 50 symbols
        Script->>YF: yfinance.download(symbols=['RELIANCE.NS','TCS.NS',...], period='1y')
        YF-->>Script: pandas DataFrame (date × symbol × OHLCV)
        Script->>Script: Reshape: MultiIndex → flat rows
        Script->>Script: Add stock_id via symbol lookup
        Script->>SP: INSERT INTO stock_prices (...)\nON CONFLICT (stock_id, price_datetime)\nDO UPDATE SET close=EXCLUDED.close,...
        SP-->>Script: rows_saved=240
        Script->>IR: UPDATE ingestion_run SET rows_saved+=240
    end

    Script->>IR: UPDATE ingestion_run SET status='completed', completed_at=NOW()
```

### News Ingestion Pipeline

```mermaid
flowchart TD
    A[ingest_stock_news.py] --> B[Check daily quotas\nnews_provider_quota_state]
    B --> C{MarketAux quota\nremaining > 0?}
    C -->|Yes| D[Fetch articles from\nMarketAux API\nfor priority stocks]
    C -->|No| E[Skip MarketAux today]
    D --> F{Alpha Vantage quota\nremaining > 0?}
    E --> F
    F -->|Yes| G[Fetch articles from\nAlpha Vantage API]
    F -->|No| H[Skip Alpha Vantage today]
    G --> I[Merge articles from both sources]
    H --> I
    I --> J[Deduplicate by URL]
    J --> K[INSERT stock_news_articles\nwhere url not exists]
    K --> L[Link articles to stocks\nvia company_aliases matching]
    L --> M[Calculate sentiment score\nfor each article]
    M --> N[UPDATE news_provider_quota_state\nDecrement daily quota]
    N --> O[Log NewsIngestionRun]
```

---

## 7. Batch Processing Patterns

### IngestionRun Tracking

Every batch job creates an `ingestion_runs` record:

```python
# At start of any batch job
run = IngestionRun(
    run_type="price_sync",
    status="running",
    total_stocks=5234,
    batch_offset=0,
    batch_limit=50,
    started_at=datetime.utcnow()
)
db.add(run)
db.commit()

# During processing
run.success_count += batch_success
run.failed_count += batch_failed
run.rows_saved += rows_inserted
db.commit()

# On completion
run.status = "completed"  # or "failed"
run.completed_at = datetime.utcnow()
db.commit()
```

### Rate Limiting Pattern

Yahoo Finance and other APIs have rate limits. The pipeline handles this:

```python
# Batch with sleep between API calls
for batch in batches:
    try:
        data = yfinance.download(batch_symbols, ...)
        process_and_save(data)
    except Exception as e:
        log_error(e)
        run.failed_count += len(batch_symbols)
    
    # Sleep to avoid rate limiting
    time.sleep(BATCH_SLEEP_SECONDS)  # configurable, usually 1-2 seconds
```

### Upsert Pattern (INSERT ... ON CONFLICT)

Prices are upserted (insert or update) to handle re-runs without duplicates:

```sql
-- Conceptual SQL (SQLAlchemy does this)
INSERT INTO stock_prices (stock_id, price_datetime, open, high, low, close, volume, source)
VALUES (:stock_id, :date, :open, :high, :low, :close, :volume, 'yfinance')
ON CONFLICT (stock_id, price_datetime) DO UPDATE SET
    open   = EXCLUDED.open,
    high   = EXCLUDED.high,
    low    = EXCLUDED.low,
    close  = EXCLUDED.close,
    volume = EXCLUDED.volume;
```

This means you can safely re-run any ingestion script without creating duplicate data.

---

## 8. Data Quality & Validation

### Handling Missing Data

```mermaid
flowchart LR
    A[yfinance returns\nDataFrame] --> B{Any NaN values?}
    B -->|Yes| C[dropna on OHLCV columns\nDrop rows where close=NaN]
    B -->|No| D[Continue]
    C --> D
    D --> E{close price\n> 0?}
    E -->|Yes| F[Accept row]
    E -->|No| G[Discard row\nLog warning]
    F --> H[Check date is\nnot future]
    H -->|Valid| I[INSERT to DB]
    H -->|Future date| J[Discard]
```

### Delisted Stock Registry

The file `constant.txt` (in project root) tracks delisted stocks. The `delisted_registry_service.py` reads this and marks stocks as `is_delisted=True` in the database. Delisted stocks are excluded from daily sync to save API quota.

### Data Freshness Checks

```sql
-- Find stocks with stale prices (not updated in last 5 days)
SELECT s.symbol, MAX(sp.price_datetime) AS last_price_date
FROM stocks s
LEFT JOIN stock_prices sp ON sp.stock_id = s.id
WHERE s.is_delisted = FALSE
GROUP BY s.symbol
HAVING MAX(sp.price_datetime) < CURRENT_DATE - INTERVAL '5 days'
   OR MAX(sp.price_datetime) IS NULL
ORDER BY last_price_date ASC NULLS FIRST;
```

---

## 9. Market Data Service

`market_data_service.py` (1,656 lines) is the central service for all price-related operations.

### Key Functions

```mermaid
graph TD
    subgraph "market_data_service.py"
        A["fetch_prices_from_yfinance(symbols, start, end)\n→ DataFrame of OHLCV"]
        B["sync_stock_prices(db, stocks, period)\n→ Calls yfinance, saves to DB"]
        C["get_latest_price(db, stock_id)\n→ Most recent close price"]
        D["get_price_history(db, stock_id, start, end)\n→ List of StockPrice rows"]
        E["get_prices_dataframe(db, stock_id, days)\n→ Pandas DataFrame for strategies"]
        F["get_market_movers(db, top_n)\n→ Top gainers/losers today"]
        G["calculate_performance(db, stock_id)\n→ 1m/3m/6m/1y returns"]
    end

    A --> B
    B -->|"Saves to"| DB[("stock_prices\ntable")]
    C --> DB
    D --> DB
    E --> DB
    F --> DB
    G --> DB

    B -->|"Called by"| Scripts["ingest scripts"]
    C -->|"Called by"| PaperSvc["paper_trading_service\n(order pricing)"]
    E -->|"Called by"| StratSvc["strategy_service\n(signal generation)"]
    G -->|"Called by"| PerfSnap["performance_snapshot\nrefresh"]
```

### get_prices_dataframe() — Critical for Strategies

This function is called by every strategy before generating signals:

```python
# What it returns — a DataFrame like this:
#
# date         open     high     low      close    volume
# 2024-01-02   2345.50  2389.00  2310.25  2375.80  12450000
# 2024-01-03   2378.00  2412.50  2355.10  2401.20  10890000
# ...          ...      ...      ...      ...      ...
# 2024-12-31   2890.10  2934.50  2875.60  2921.30   9870000
#
# Sorted ascending by date (oldest first)
# This is what RSI, MACD, SMA strategies operate on
```

---

## 10. News Ingestion Pipeline

### Entity Relationship for News

```mermaid
erDiagram
    STOCK_NEWS_ARTICLES {
        int id PK
        string headline
        string summary
        string url UK
        string source
        string provider
        float sentiment_score
        datetime published_at
    }

    STOCK_NEWS_LINKS {
        int id PK
        int article_id FK
        int stock_id FK
        float match_confidence
        string match_method
    }

    COMPANY_ALIASES {
        int id PK
        int stock_id FK
        string alias_text
        string alias_type
    }

    NEWS_PROVIDER_QUOTA_STATE {
        int id PK
        string provider_name UK
        int daily_limit
        int used_today
        date quota_date
    }

    NEWS_INGESTION_META {
        int id PK
        int stock_id FK
        string tier
        string status
        datetime last_fetched_at
    }

    NEWS_INGESTION_RUNS {
        int id PK
        string provider
        int articles_fetched
        int articles_saved
        int stocks_processed
        string status
        datetime started_at
        datetime completed_at
    }

    STOCKS ||--o{ STOCK_NEWS_LINKS : "linked to"
    STOCKS ||--o{ COMPANY_ALIASES : "known as"
    STOCKS ||--|| NEWS_INGESTION_META : "has meta"
    STOCK_NEWS_ARTICLES ||--o{ STOCK_NEWS_LINKS : "linked to stocks"
```

### Company Name Matching Algorithm

The key challenge: an article about "Reliance Industries Limited" needs to be linked to the `RELIANCE` stock. The `company_aliases` table stores normalized name variants:

```
stock_id | alias_text                    | alias_type
---------|-------------------------------|-------------
   1     | RELIANCE                      | symbol
   1     | Reliance Industries           | short_name
   1     | Reliance Industries Limited   | full_name
   1     | RIL                          | acronym
   2     | TCS                          | symbol
   2     | Tata Consultancy Services     | full_name
   2     | Tata Consultancy             | short_name
```

Matching logic:
1. Tokenize article headline + summary
2. Check if any alias appears in the text (case-insensitive)
3. Score confidence (exact symbol match = 0.95, partial name = 0.7)
4. Only link if confidence > 0.5 threshold

---

## 11. Index & Constituent Loading

### NSE Index Files

The NSE website provides CSV files listing which stocks are in which index. These live in:
```
data/
├── nse_index_csvs/
│   ├── NIFTY 50.csv
│   ├── NIFTY 100.csv
│   ├── NIFTY 200.csv
│   ├── NIFTY 500.csv
│   ├── NIFTY Bank.csv
│   ├── NIFTY Financial Services.csv
│   └── ...
└── index_constituents_sample.csv  ← pre-processed sample
```

### Index Loading Flow

```mermaid
flowchart TD
    A["data/nse_index_csvs/\nNIFTY 50.csv"] --> B["create_nse_index_csv_tables.py\nParse CSV:\nCompany Name, Symbol, Series"]
    B --> C["data/index_constituents_sample.csv\n(processed flat file)"]
    C --> D["load_index_memberships.py"]
    D --> E{For each symbol in CSV}
    E --> F[Lookup stock_id\nfrom stocks table\nby symbol]
    F --> G{Symbol found\nin stocks?}
    G -->|Yes| H[INSERT stock_index_memberships\n(stock_id, index_id, weight, effective_date)]
    G -->|No| I[Log warning:\n'Symbol not found: XYZ']
    H --> J[UPDATE stocks\nSET is_nifty50=True\nWHERE symbol='RELIANCE']
    J --> E
    E --> K[All stocks in NIFTY50\nnow have is_nifty50=True flag]
```

### Index Flags on Stocks Table

For fast queries, stocks have boolean flags directly:

```sql
-- Quickly get all NIFTY50 stocks (no JOIN needed)
SELECT symbol, company_name FROM stocks WHERE is_nifty50 = TRUE;

-- Stocks in multiple indices
SELECT symbol FROM stocks 
WHERE is_nifty50 = TRUE AND is_banknifty = TRUE;
```

These flags are set by `load_index_memberships.py` and should be refreshed whenever NSE publishes index reconstitution changes.

---

## 12. Analytics & Caching Layer

### Why a Cache?

Some queries are expensive to compute on every request:
- "What's the 6-month return for all 5000 stocks?" → Full table scan
- "What's the top 10 sector by momentum?" → Complex aggregation

Instead, these are pre-computed and stored:

### Performance Snapshots

`stock_performance_snapshots` stores pre-computed returns:

```
stock_id | return_1m_pct | return_3m_pct | return_6m_pct | return_1y_pct | as_of_date
---------|---------------|---------------|---------------|---------------|------------
   1     | 4.2           | 12.8          | -3.1          | 22.4          | 2024-12-31
   2     | -1.3          | 8.9           | 15.2          | 18.7          | 2024-12-31
...
```

Refreshed daily by `analytics_refresh_service.py` and `refresh_analytics.py`.

### Market Analytics Cache (JSONB)

For flexible cached data:

```sql
-- Example rows in market_analytics_cache
cache_key                    | data                              | expires_at
-----------------------------|-----------------------------------|----------------
"top_gainers_20241231"       | [{"symbol":"TCS","change":4.2}]  | 2024-12-31 18:00
"sector_performance_weekly"  | {"IT":2.1, "Banking":-0.3, ...}  | 2025-01-01 00:00
"market_breadth"             | {"advances":342, "declines":158} | 2024-12-31 16:00
```

### Cache Refresh Flow

```mermaid
flowchart LR
    A["refresh_analytics.py\n(runs daily)"] --> B["analytics_refresh_service.py"]
    B --> C["Compute 1m/3m/6m/1y\nreturns for all stocks"]
    C --> D["UPSERT stock_performance_snapshots"]
    B --> E["Compute market breadth\ntop gainers/losers"]
    E --> F["UPSERT market_analytics_cache\nwith TTL"]
    B --> G["Compute sector performance\naggregations"]
    G --> F
```

---

## 13. How to Run Pipelines

### One-time Bootstrap (New Environment Setup)

```bash
# 1. Start the database
docker compose up -d postgres

# 2. Initialize schema
cd backend
alembic upgrade head

# 3. Load stock symbols from CSV
python scripts/load_indian_tickers.py

# 4. Load index constituents
python scripts/load_index_memberships.py

# 5. Fetch initial price history (1 year)
python scripts/ingest_market_history.py --period 1y

# 6. Load index funds (NIFTY ETF, Gold, etc.)
python scripts/ingest_index_funds.py

# 7. Seed strategy templates
python scripts/seed_strategy_templates.py

# 8. Initial analytics refresh
python scripts/refresh_analytics.py
```

### Daily Sync (Run Every Market Day)

```bash
# Priority order: NIFTY50 first, then rest
python scripts/ingest_priority.py

# Fetch latest news
python scripts/ingest_stock_news.py

# Refresh analytics cache
python scripts/refresh_analytics.py
```

### Manual Price Backfill

```bash
# Fetch last 5 years of data for all stocks
python scripts/ingest_market_history.py --period 5y --interval 1d

# Fetch for a specific stock only
python scripts/fetch_prices.py --symbol RELIANCE.NS --period 1y
```

### Checking Ingestion Status

```sql
-- Via API
GET http://localhost:8000/api/data/ingestion/status

-- Direct SQL
SELECT run_type, status, success_count, failed_count, rows_saved,
       started_at, completed_at,
       EXTRACT(EPOCH FROM (completed_at - started_at)) AS duration_seconds
FROM ingestion_runs
ORDER BY started_at DESC
LIMIT 10;
```

---

## 14. Common Tasks for Interns

### Task: Find why a stock has no price data

```sql
-- Check if stock exists
SELECT id, symbol, yahoo_symbol, is_delisted FROM stocks WHERE symbol = 'XYZ';

-- Check if prices exist
SELECT COUNT(*), MIN(price_datetime), MAX(price_datetime) 
FROM stock_prices WHERE stock_id = (SELECT id FROM stocks WHERE symbol = 'XYZ');

-- Check ingestion history for this stock
SELECT * FROM ingestion_runs WHERE error_message LIKE '%XYZ%' ORDER BY started_at DESC;
```

### Task: Add a new stock manually

```sql
-- Insert the stock
INSERT INTO stocks (symbol, yahoo_symbol, exchange, company_name)
VALUES ('NEWSTOCK', 'NEWSTOCK.NS', 'NSE', 'New Stock Company Ltd');

-- Then run price fetch for it
python scripts/fetch_prices.py --symbol NEWSTOCK.NS
```

### Task: Debug a yfinance failure

```python
import yfinance as yf

# Test directly
ticker = yf.Ticker("RELIANCE.NS")
hist = ticker.history(period="1mo")
print(hist)  # Should show OHLCV DataFrame

# If empty: check yahoo_symbol is correct (needs .NS for NSE, .BO for BSE)
```

### Task: Check news quota usage

```sql
SELECT provider_name, daily_limit, used_today, quota_date
FROM news_provider_quota_state
ORDER BY quota_date DESC;
```

### Task: Verify index constituent data is correct

```sql
-- How many stocks are in each index?
SELECT 
  SUM(CASE WHEN is_nifty50 THEN 1 ELSE 0 END) AS nifty50_count,
  SUM(CASE WHEN is_nifty100 THEN 1 ELSE 0 END) AS nifty100_count,
  SUM(CASE WHEN is_nifty500 THEN 1 ELSE 0 END) AS nifty500_count,
  SUM(CASE WHEN is_banknifty THEN 1 ELSE 0 END) AS banknifty_count
FROM stocks WHERE is_delisted = FALSE;

-- Expected: nifty50=50, nifty100=100, nifty500=500, banknifty=12
```

---

## 15. Database Connection Configuration

### 15.1 How the App Connects to PostgreSQL

The connection is configured via a single environment variable: `DATABASE_URL`. Everything — scripts, backend API, Alembic migrations — reads this one variable.

```
postgresql://<user>:<password>@<host>:<port>/<database>

Local Docker example:
  postgresql://trading_user:trading_pass@localhost:5432/trading_db

Production example:
  postgresql://prod_user:s3cur3P@ss@db.internal:5432/trading_prod
```

#### Where to Set It

| Context | File | Variable |
|---------|------|----------|
| Docker Compose (local) | `docker-compose.yml` → `environment:` | `DATABASE_URL` |
| Backend .env file | `backend/.env` | `DATABASE_URL=postgresql://...` |
| Scripts (direct run) | Shell export before running | `export DATABASE_URL=postgresql://...` |
| Alembic migrations | `backend/alembic.ini` → reads from `config.py` which reads `DATABASE_URL` | — |
| CI/CD | Environment secrets | `DATABASE_URL` |

#### Full Environment Variables Reference

```bash
# backend/.env  (copy from backend/.env.example)

# ─── Database ──────────────────────────────────────────────────────
DATABASE_URL=postgresql://trading_user:trading_pass@postgres:5432/trading_db

# ─── Auth ──────────────────────────────────────────────────────────
JWT_SECRET_KEY=change-this-in-production-to-a-long-random-string
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440       # 24 hours

# ─── Dev bypass (NEVER use in production) ──────────────────────────
DEBUG_AUTH_BYPASS=true
DEBUG_AUTH_USER_EMAIL=test@example.com

# ─── Market data ───────────────────────────────────────────────────
YFINANCE_DEFAULT_PERIOD=1y
YFINANCE_DEFAULT_INTERVAL=1d

# ─── News APIs (optional — get free keys from their websites) ──────
MARKETAUX_API_TOKEN=your_marketaux_key_here
ALPHA_VANTAGE_API_KEY=your_alpha_vantage_key_here

# ─── AI / Ollama ───────────────────────────────────────────────────
AI_FEATURES_ENABLED=false
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_DEFAULT_MODEL=qwen3:14b
OLLAMA_TIMEOUT_SECONDS=60

# ─── Email (optional) ──────────────────────────────────────────────
EMAIL_ALERTS_ENABLED=false
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=your_app_password
```

#### docker-compose.yml — Database Service Config

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: trading_db
      POSTGRES_USER: trading_user
      POSTGRES_PASSWORD: trading_pass
    ports:
      - "5432:5432"           # expose to host for psql / pgAdmin
    volumes:
      - postgres_data:/var/lib/postgresql/data   # persist data across restarts
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U trading_user -d trading_db"]
      interval: 5s
      timeout: 5s
      retries: 5

  backend:
    environment:
      DATABASE_URL: postgresql://trading_user:trading_pass@postgres:5432/trading_db
    depends_on:
      postgres:
        condition: service_healthy   # wait until PG is actually ready
```

#### SQLAlchemy Connection Pool (database.py)

```python
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,   # test connection before each use (handles idle timeouts)
    pool_size=20,          # keep 20 connections open permanently
    max_overflow=40,       # allow 40 extra connections under peak load
    pool_timeout=30,       # wait up to 30s for a free connection
    pool_recycle=1800,     # recycle connections every 30 min (avoids PG idle timeout)
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
```

### 15.2 How to Connect Manually

#### psql (terminal)

```bash
# If running via Docker
docker exec -it paper_trading_app-postgres-1 psql -U trading_user -d trading_db

# If PostgreSQL is installed locally
psql postgresql://trading_user:trading_pass@localhost:5432/trading_db
```

#### pgAdmin (GUI)

```
Host:     localhost
Port:     5432
Database: trading_db
Username: trading_user
Password: trading_pass
```

#### Python Script (direct connection)

```python
from sqlalchemy import create_engine, text

DATABASE_URL = "postgresql://trading_user:trading_pass@localhost:5432/trading_db"
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    result = conn.execute(text("SELECT COUNT(*) FROM stocks"))
    print(result.fetchone())
```

#### Alembic (run migrations)

```bash
cd backend
export DATABASE_URL=postgresql://trading_user:trading_pass@localhost:5432/trading_db
alembic upgrade head          # apply all pending migrations
alembic current               # show current migration version
alembic history               # show all migrations
alembic downgrade -1          # roll back one migration
```

---

### 15.3 Per-Table Configuration & Access Reference

Every table below lists:
- **SQLAlchemy model** — Python class that maps to this table
- **Who writes to it** — which script or service inserts/updates rows
- **Who reads it** — which service or router reads from it
- **Key indexes** — what queries are fast
- **Primary write pattern** — INSERT only / UPSERT / UPDATE in-place
- **Quick connect query** — one SQL statement to see the data

---

#### `stocks` — Stock Master Table

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/stock.py → Stock` |
| **Written by** | `scripts/load_indian_tickers.py`, `scripts/enrich_stock_metadata.py` |
| **Read by** | Almost every service — `ticker_service`, `market_data_service`, all routers |
| **Write pattern** | INSERT on bootstrap; UPDATE for metadata enrichment; never deleted (use `is_delisted=True`) |
| **Key indexes** | `symbol` (unique), `yahoo_symbol` (unique), `exchange`, `sector`, index boolean flags |

```sql
-- Connect and explore
SELECT id, symbol, yahoo_symbol, exchange, sector, industry,
       is_nifty50, is_nifty500, is_delisted, created_at
FROM stocks
ORDER BY symbol
LIMIT 20;

-- Count by exchange
SELECT exchange, COUNT(*) FROM stocks WHERE is_delisted=FALSE GROUP BY exchange;

-- All NIFTY50 stocks
SELECT symbol, company_name FROM stocks WHERE is_nifty50=TRUE ORDER BY symbol;
```

**Configuration to write a new stock:**
```python
from app.models.stock import Stock
from app.database import SessionLocal

db = SessionLocal()
stock = Stock(
    symbol="NEWCORP",
    yahoo_symbol="NEWCORP.NS",
    exchange="NSE",
    company_name="New Corp Ltd",
    sector="Technology",
    industry="Software",
)
db.add(stock)
db.commit()
```

---

#### `stock_prices` — OHLCV Price History

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/stock.py → StockPrice` |
| **Written by** | `scripts/fetch_prices.py`, `scripts/ingest_priority.py`, `scripts/ingest_market_history.py`, `market_data_service.sync_stock_prices()` |
| **Read by** | `market_data_service.get_prices_dataframe()`, `backtest_service`, `strategy_service`, all chart routes |
| **Write pattern** | UPSERT — `ON CONFLICT (stock_id, price_datetime) DO UPDATE` |
| **Key indexes** | `(stock_id, price_datetime)` composite unique, `price_datetime` for date range queries |

```sql
-- Last 5 price bars for RELIANCE
SELECT sp.price_datetime, sp.open, sp.high, sp.low, sp.close, sp.volume
FROM stock_prices sp
JOIN stocks s ON s.id = sp.stock_id
WHERE s.symbol = 'RELIANCE'
ORDER BY sp.price_datetime DESC
LIMIT 5;

-- Count total price rows
SELECT COUNT(*) FROM stock_prices;

-- Stocks with most price history
SELECT s.symbol, COUNT(*) AS bar_count, MIN(sp.price_datetime) AS first_date
FROM stock_prices sp
JOIN stocks s ON s.id = sp.stock_id
GROUP BY s.symbol
ORDER BY bar_count DESC
LIMIT 10;
```

**yfinance fetch configuration:**
```python
import yfinance as yf

# Period options: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max
# Interval options: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo
data = yf.download(
    tickers=["RELIANCE.NS", "TCS.NS"],
    period="1y",
    interval="1d",
    auto_adjust=True,     # adjust for splits and dividends
    progress=False,
)
# Returns MultiIndex DataFrame: (Date, Ticker) × (Open, High, Low, Close, Volume)
```

---

#### `ingestion_runs` — Batch Job Tracker

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/stock.py → IngestionRun` |
| **Written by** | All ingestion scripts — creates one record per batch run |
| **Read by** | `data_ingestion_stats_service`, `/api/data/ingestion/status`, Data page |
| **Write pattern** | INSERT at start → UPDATE rows_saved during → UPDATE status at end |
| **Key indexes** | `created_at`, `status`, `run_type` |

```sql
-- Last 10 ingestion runs with health status
SELECT run_type, status, success_count, failed_count, rows_saved,
       started_at, completed_at,
       EXTRACT(EPOCH FROM (completed_at - started_at))::int AS duration_secs
FROM ingestion_runs
ORDER BY started_at DESC
LIMIT 10;

-- Failed runs only
SELECT * FROM ingestion_runs WHERE status = 'failed' ORDER BY started_at DESC;

-- Today's sync summary
SELECT run_type, SUM(rows_saved) AS total_rows
FROM ingestion_runs
WHERE started_at::date = CURRENT_DATE
GROUP BY run_type;
```

---

#### `stock_performance_snapshots` — Pre-computed Returns Cache

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/stock.py → StockPerformanceSnapshot` |
| **Written by** | `analytics_refresh_service.py`, `scripts/refresh_analytics.py` |
| **Read by** | `market_overview_service`, `market_trends_service`, `explore/all-stocks` partial |
| **Write pattern** | UPSERT daily — one row per stock per `as_of_date` |
| **Key indexes** | `(stock_id, as_of_date)` composite, `as_of_date` for latest-date queries |

```sql
-- Fastest rising stocks last month
SELECT s.symbol, s.sector, p.return_1m_pct, p.return_6m_pct, p.return_1y_pct
FROM stock_performance_snapshots p
JOIN stocks s ON s.id = p.stock_id
WHERE p.as_of_date = (SELECT MAX(as_of_date) FROM stock_performance_snapshots)
  AND s.is_nifty500 = TRUE
ORDER BY p.return_1m_pct DESC
LIMIT 20;

-- Sector average performance
SELECT s.sector, ROUND(AVG(p.return_1m_pct)::numeric, 2) AS avg_1m,
                  ROUND(AVG(p.return_1y_pct)::numeric, 2) AS avg_1y
FROM stock_performance_snapshots p
JOIN stocks s ON s.id = p.stock_id
WHERE p.as_of_date = (SELECT MAX(as_of_date) FROM stock_performance_snapshots)
GROUP BY s.sector ORDER BY avg_1m DESC;
```

**How to force a refresh:**
```bash
python scripts/refresh_analytics.py
# OR via API
curl -X POST http://localhost:8000/web/partials/data/refresh-analytics
```

---

#### `market_analytics_cache` — JSONB Query Cache

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/stock.py → MarketAnalyticsCache` |
| **Written by** | `analytics_refresh_service`, `market_overview_service` |
| **Read by** | `market_overview_service.get_top_movers()`, explore page top-movers partial |
| **Write pattern** | UPSERT on `cache_key` (unique); rows with `expires_at < NOW()` are stale |
| **Key indexes** | `cache_key` (unique), `expires_at` |

```sql
-- See all cached entries and their freshness
SELECT cache_key,
       expires_at,
       CASE WHEN expires_at > NOW() THEN 'FRESH' ELSE 'STALE' END AS status,
       jsonb_array_length(data) AS item_count     -- if data is an array
FROM market_analytics_cache
ORDER BY expires_at DESC;

-- Clear stale cache to force refresh
DELETE FROM market_analytics_cache WHERE expires_at < NOW();

-- Inspect a specific cache entry
SELECT data FROM market_analytics_cache WHERE cache_key = 'top_gainers_20241231';
```

---

#### `market_indices` — Index Definitions

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/market_index.py → MarketIndex` |
| **Written by** | `scripts/load_index_memberships.py` (one-time seed) |
| **Read by** | `market_overview_service`, explore index-cards partial |
| **Write pattern** | INSERT once — rarely changes |
| **Key indexes** | `index_code` (unique), `yahoo_symbol` |

```sql
-- All market indices in the system
SELECT id, index_code, index_name, yahoo_symbol FROM market_indices ORDER BY index_code;

-- Expected rows:
-- NIFTY50, NIFTY100, NIFTY200, NIFTY500, BANKNIFTY, FINNIFTY, SENSEX, etc.
```

**To add a new index:**
```python
from app.models.market_index import MarketIndex
index = MarketIndex(
    index_code="NIFTYMIDCAP150",
    index_name="NIFTY Midcap 150",
    yahoo_symbol="^NSEMDCP150",
)
db.add(index); db.commit()
```

---

#### `stock_index_memberships` — Stock ↔ Index Join Table

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/market_index.py → StockIndexMembership` |
| **Written by** | `scripts/load_index_memberships.py` |
| **Read by** | Used indirectly via boolean flags on `stocks` table (`is_nifty50`, etc.) |
| **Write pattern** | INSERT on bootstrap; UPDATE when index reconstitution happens |
| **Key indexes** | `(stock_id, index_id)` composite, `is_current`, `effective_date` |

```sql
-- All current NIFTY50 members with weights
SELECT s.symbol, s.company_name, m.weight_pct, m.effective_date
FROM stock_index_memberships m
JOIN stocks s ON s.id = m.stock_id
JOIN market_indices i ON i.id = m.index_id
WHERE i.index_code = 'NIFTY50' AND m.is_current = TRUE
ORDER BY m.weight_pct DESC;

-- Stocks added/removed in last reconstitution
SELECT s.symbol, m.effective_date, m.exit_date, m.is_current
FROM stock_index_memberships m
JOIN stocks s ON s.id = m.stock_id
JOIN market_indices i ON i.id = m.index_id
WHERE i.index_code = 'NIFTY50'
ORDER BY m.effective_date DESC
LIMIT 20;
```

---

#### `index_funds` — ETF & Index Fund Master

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/index_fund.py → IndexFund` |
| **Written by** | `scripts/ingest_index_funds.py`, `scripts/load_index_funds.py` |
| **Read by** | `index_fund_service`, index_fund page, backtesting instrument search |
| **Write pattern** | INSERT on bootstrap; UPDATE `latest_price` and `last_synced_at` on sync |
| **Key indexes** | `symbol` (unique), `yahoo_symbol`, `category` |

```sql
-- All index funds by category
SELECT symbol, yahoo_symbol, full_name, category, latest_price, last_synced_at
FROM index_funds
ORDER BY category, symbol;

-- Categories: 'index', 'commodity', 'etf'
```

**To add a new index fund:**
```python
from app.models.index_fund import IndexFund
fund = IndexFund(
    symbol="GOLDBEES",
    yahoo_symbol="GOLDBEES.NS",
    full_name="Nippon India ETF Gold BeES",
    category="commodity",
)
db.add(fund); db.commit()
```

---

#### `index_fund_prices` — ETF OHLCV History

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/index_fund.py → IndexFundPrice` |
| **Written by** | `index_fund_service.sync_prices()`, `scripts/ingest_index_funds.py` |
| **Read by** | `index_fund_service`, index fund page price chart |
| **Write pattern** | UPSERT on `(fund_id, price_datetime)` |
| **Key indexes** | `(fund_id, price_datetime)` composite unique |

```sql
-- Last 30 days of GOLDBEES prices
SELECT f.symbol, p.price_datetime, p.open, p.close, p.volume
FROM index_fund_prices p
JOIN index_funds f ON f.id = p.fund_id
WHERE f.symbol = 'GOLDBEES'
ORDER BY p.price_datetime DESC
LIMIT 30;
```

---

#### `stock_news_articles` — News Articles Store

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/news.py → StockNewsArticle` |
| **Written by** | `news_service.py`, `scripts/ingest_stock_news.py` |
| **Read by** | `news_service.get_news_for_stock()`, explore page news section |
| **Write pattern** | INSERT if URL not exists (deduplicate on `url` unique constraint) |
| **Key indexes** | `url` (unique), `published_at`, `sentiment_score` |

```sql
-- Latest 10 news articles with sentiment
SELECT headline, source, sentiment_score, published_at
FROM stock_news_articles
ORDER BY published_at DESC
LIMIT 10;

-- Most positive news today
SELECT headline, sentiment_score FROM stock_news_articles
WHERE published_at::date = CURRENT_DATE
ORDER BY sentiment_score DESC LIMIT 5;

-- News for a specific stock (via join)
SELECT a.headline, a.sentiment_score, a.published_at, a.url
FROM stock_news_articles a
JOIN stock_news_links l ON l.article_id = a.id
JOIN stocks s ON s.id = l.stock_id
WHERE s.symbol = 'RELIANCE'
ORDER BY a.published_at DESC
LIMIT 10;
```

**API key configuration for news:**
```bash
# In backend/.env
MARKETAUX_API_TOKEN=your_key          # get free key at marketaux.com
ALPHA_VANTAGE_API_KEY=your_key        # get free key at alphavantage.co
NEWS_REQUEST_TIMEOUT_SECONDS=12       # timeout per HTTP request to news APIs
```

---

#### `stock_news_links` — Article ↔ Stock Many-to-Many

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/news.py → StockNewsLink` |
| **Written by** | `news_service.link_articles_to_stocks()` |
| **Read by** | Any query joining articles to stocks |
| **Write pattern** | INSERT — never updated (confidence is set at insert time) |
| **Key indexes** | `(article_id, stock_id)` composite unique, `match_confidence` |

```sql
-- How many articles per stock (top 10)
SELECT s.symbol, COUNT(*) AS article_count, AVG(l.match_confidence) AS avg_confidence
FROM stock_news_links l
JOIN stocks s ON s.id = l.stock_id
GROUP BY s.symbol
ORDER BY article_count DESC
LIMIT 10;
```

---

#### `company_aliases` — Name Variants for News Matching

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/news.py → CompanyAlias` |
| **Written by** | `news_service` during news ingestion; can be seeded manually |
| **Read by** | `news_service.link_articles_to_stocks()` for matching |
| **Write pattern** | INSERT on discovery; rarely deleted |
| **Key indexes** | `(stock_id, alias_text)` composite unique |

```sql
-- See all aliases for RELIANCE
SELECT alias_text, alias_type FROM company_aliases
WHERE stock_id = (SELECT id FROM stocks WHERE symbol = 'RELIANCE')
ORDER BY alias_type;

-- Add a missing alias manually
INSERT INTO company_aliases (stock_id, alias_text, alias_type)
VALUES (
  (SELECT id FROM stocks WHERE symbol = 'RELIANCE'),
  'Jio Platforms', 'subsidiary'
);
```

---

#### `news_provider_quota_state` — Daily API Quota Tracker

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/news.py → NewsProviderQuotaState` |
| **Written by** | `news_service.py` — decrements `used_today` after each API call |
| **Read by** | `news_service` (checks before each API call), Data page |
| **Write pattern** | One row per provider; `used_today` reset daily |
| **Key indexes** | `provider_name` (unique), `quota_date` |

```sql
-- Check today's quota usage
SELECT provider_name, daily_limit, used_today,
       (daily_limit - used_today) AS remaining,
       quota_date
FROM news_provider_quota_state
WHERE quota_date = CURRENT_DATE;

-- Reset quota (if needed for testing)
UPDATE news_provider_quota_state
SET used_today = 0, quota_date = CURRENT_DATE
WHERE provider_name = 'marketaux';
```

---

#### `stock_fundamentals_latest` — Company Financials

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/fundamentals.py → StockFundamentalsLatest` |
| **Written by** | `fundamentals_service.refresh_fundamentals()` — reads from yfinance |
| **Read by** | Explore page stock detail, NL screener, AI Think Tank context |
| **Write pattern** | UPSERT on `stock_id` — one row per stock |
| **Key indexes** | `stock_id` (unique FK), `fetch_status`, `fetched_at` |

```sql
-- Top value stocks: low PE, high ROE
SELECT s.symbol, s.sector, f.pe_ratio, f.roe_pct, f.market_cap, f.debt_to_equity
FROM stock_fundamentals_latest f
JOIN stocks s ON s.id = f.stock_id
WHERE f.pe_ratio BETWEEN 1 AND 20
  AND f.roe_pct > 15
  AND f.debt_to_equity < 1
  AND f.fetch_status = 'success'
ORDER BY f.roe_pct DESC
LIMIT 20;

-- Stocks missing fundamentals data
SELECT s.symbol, f.fetch_status, f.fetched_at
FROM stock_fundamentals_latest f
JOIN stocks s ON s.id = f.stock_id
WHERE f.fetch_status != 'success' OR f.fetched_at < NOW() - INTERVAL '30 days'
ORDER BY f.fetched_at ASC NULLS FIRST;
```

**To trigger a fundamentals refresh:**
```bash
python scripts/enrich_stock_metadata.py   # fetches PE/ROE/market_cap for all stocks
```

---

#### `users` — User Accounts

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/user.py → User` |
| **Written by** | `/api/auth/register` endpoint |
| **Read by** | All authenticated endpoints, portfolio service |
| **Write pattern** | INSERT on registration; UPDATE `current_cash` on trades |
| **Key indexes** | `user_name` (unique), `email` (unique) |

```sql
-- All users
SELECT id, name, user_name, email, starting_cash, current_cash, risk_profile, created_at
FROM users ORDER BY created_at DESC;

-- Reset a user's cash (for testing)
UPDATE users SET current_cash = 1000000 WHERE email = 'test@example.com';
```

---

#### `user_credentials` — Password Hashes

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/auth.py → UserCredential` |
| **Written by** | `/api/auth/register`, `/api/auth/reset-password` |
| **Read by** | `/api/auth/login` for verification |
| **Write pattern** | INSERT on register; UPDATE hash on password reset |
| **Key indexes** | `user_id` (unique FK) |

```sql
-- Check if a user has credentials (never select the hash itself)
SELECT u.email, uc.created_at AS credential_created
FROM user_credentials uc JOIN users u ON u.id = uc.user_id
ORDER BY uc.created_at DESC;
```

> **Security**: Never `SELECT hashed_password` in application code. Only `bcrypt.verify()` in `security.py` should touch the hash.

**Reset a password from the CLI (admin utility):**
```bash
python scripts/reset_user_password.py --email user@example.com --new-password newpassword123
```

---

#### `auth_sessions` — Active JWT Sessions

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/auth.py → AuthSession` |
| **Written by** | `/api/auth/login` (INSERT), `/api/auth/logout` (UPDATE is_active=False) |
| **Read by** | `security.get_current_user()` — every authenticated request |
| **Write pattern** | INSERT on login; UPDATE `is_active=False` on logout; rows expire naturally |
| **Key indexes** | `jti_hash` (unique), `user_id`, `expires_at` |

```sql
-- All active sessions
SELECT u.email, s.expires_at, s.created_at
FROM auth_sessions s JOIN users u ON u.id = s.user_id
WHERE s.is_active = TRUE AND s.expires_at > NOW()
ORDER BY s.created_at DESC;

-- Force logout a user (invalidate all their sessions)
UPDATE auth_sessions SET is_active = FALSE
WHERE user_id = (SELECT id FROM users WHERE email = 'user@example.com');

-- Clean up expired sessions
DELETE FROM auth_sessions WHERE expires_at < NOW();
```

---

#### `portfolios` — Portfolio Instances

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/portfolio.py → Portfolio` |
| **Written by** | `/api/portfolios/` POST, `ensure_default_portfolios()` |
| **Read by** | Portfolio page, paper trading page, strategy lab, backtesting |
| **Write pattern** | INSERT on create; UPDATE `cash_balance` on every trade |
| **Key indexes** | `user_id`, `portfolio_name` |

```sql
-- All portfolios for a user
SELECT id, portfolio_name, type, starting_value, cash_balance, created_at
FROM portfolios
WHERE user_id = (SELECT id FROM users WHERE email = 'test@example.com')
ORDER BY created_at;

-- Portfolio types: 'manual', 'paper', 'sip', 'algo'
```

---

#### `portfolio_holdings` — Current Stock Positions

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/portfolio.py → PortfolioHolding` |
| **Written by** | `paper_trading_service`, `portfolio_service` on every buy/sell |
| **Read by** | Portfolio holdings display, paper trading open positions |
| **Write pattern** | UPSERT on `(portfolio_id, stock_id)` — update qty and avg_price on each trade |
| **Key indexes** | `(portfolio_id, stock_id)` composite unique |

```sql
-- Current holdings with current value
SELECT s.symbol, h.qty, h.avg_buy_price,
       (h.qty * h.avg_buy_price) AS total_invested,
       h.realized_pnl
FROM portfolio_holdings h
JOIN stocks s ON s.id = h.stock_id
WHERE h.portfolio_id = 7
ORDER BY (h.qty * h.avg_buy_price) DESC;
```

---

#### `transactions` — Historical Trade Ledger

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/portfolio.py → Transaction` |
| **Written by** | Every buy/sell — paper trading and manual trades |
| **Read by** | Transaction history page, PnL calculations |
| **Write pattern** | INSERT only — never update or delete (audit trail) |
| **Key indexes** | `portfolio_id`, `stock_id`, `created_at` |

```sql
-- Full transaction history for a portfolio
SELECT s.symbol, t.side, t.qty, t.price, t.charges, t.net_amount, t.source, t.created_at
FROM transactions t
JOIN stocks s ON s.id = t.stock_id
WHERE t.portfolio_id = 7
ORDER BY t.created_at DESC;

-- Total charges paid (useful for measuring cost drag)
SELECT SUM(charges) AS total_charges, COUNT(*) AS trade_count
FROM transactions WHERE portfolio_id = 7;
```

---

#### `paper_orders` — Active & Historical Orders

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/portfolio.py → PaperOrder` |
| **Written by** | `paper_trading_service.place_order()` |
| **Read by** | Paper trading page order history, limit order matching job |
| **Write pattern** | INSERT on placement; UPDATE `status` on fill/cancel/expire |
| **Key indexes** | `portfolio_id`, `status`, `stock_id` |

```sql
-- All open (pending) limit orders
SELECT s.symbol, po.side, po.order_type, po.quantity, po.limit_price, po.created_at
FROM paper_orders po
JOIN stocks s ON s.id = po.stock_id
WHERE po.status = 'PENDING'
ORDER BY po.created_at DESC;

-- Order statuses: 'PENDING', 'FILLED', 'CANCELLED', 'EXPIRED'
```

---

#### `paper_trades` — Executed Trade Records

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/portfolio.py → PaperTrade` |
| **Written by** | `paper_trading_service` when an order fills |
| **Read by** | Trade history display, PnL reporting |
| **Write pattern** | INSERT only — one record per execution |
| **Key indexes** | `paper_order_id`, `executed_at` |

```sql
-- Recent executed trades with full charges breakdown
SELECT s.symbol, pt.executed_price, pt.quantity,
       pt.total_charges, pt.slippage, pt.executed_at,
       pt.charges_breakdown   -- JSONB: {brokerage, stt, gst, stamp_duty}
FROM paper_trades pt
JOIN paper_orders po ON po.id = pt.paper_order_id
JOIN stocks s ON s.id = po.stock_id
ORDER BY pt.executed_at DESC
LIMIT 20;
```

---

#### `strategy_templates` — Built-in Strategy Definitions

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/strategy.py → StrategyTemplate` |
| **Written by** | `scripts/seed_strategy_templates.py` (one-time seed) |
| **Read by** | Strategy lab, backtesting form, signal generation |
| **Write pattern** | INSERT on seed; UPDATE `default_parameters` when a strategy is improved |
| **Key indexes** | `name` (unique) |

```sql
-- All available strategy templates
SELECT id, name, description, default_parameters FROM strategy_templates ORDER BY name;

-- Example default_parameters for RSI Strategy:
-- {"period": 14, "oversold": 35, "overbought": 65, "atr_multiplier": 2.0}
```

**To re-seed strategy templates:**
```bash
python scripts/seed_strategy_templates.py
```

---

#### `user_strategies` — User's Strategy Instances

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/strategy.py → UserStrategy` |
| **Written by** | Strategy lab create form → `run_create_user_strategy()` |
| **Read by** | Strategy lab user strategies list, signal generation, backtesting |
| **Write pattern** | INSERT on create; UPDATE `parameters` if user edits |
| **Key indexes** | `user_id`, `template_id`, `is_active` |

```sql
-- All strategies created by a user
SELECT us.id, us.name, st.name AS template, us.parameters, us.risk_settings, us.created_at
FROM user_strategies us
JOIN strategy_templates st ON st.id = us.template_id
WHERE us.user_id = 42
ORDER BY us.created_at DESC;
```

---

#### `strategy_signals` — Generated Trading Signals

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/strategy.py → StrategySignal` |
| **Written by** | `run_generate_signal()` in `web_strategy_lab_helpers.py` |
| **Read by** | Activity log, signal outcome tracking, AI Think Tank |
| **Write pattern** | INSERT on generation; never updated |
| **Key indexes** | `strategy_id`, `stock_id`, `signal_date`, `signal_type` |

```sql
-- Recent BUY signals across all strategies
SELECT us.name AS strategy, s.symbol, ss.signal_type, ss.confidence_score,
       ss.reason, ss.signal_date, ss.indicators
FROM strategy_signals ss
JOIN user_strategies us ON us.id = ss.strategy_id
JOIN stocks s ON s.id = ss.stock_id
WHERE ss.signal_type = 'BUY'
ORDER BY ss.signal_date DESC, ss.confidence_score DESC
LIMIT 20;
```

---

#### `strategy_signal_outcomes` — Signal Accuracy Tracker

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/strategy.py → StrategySignalOutcome` |
| **Written by** | `signal_outcome_service.run_outcome_calculation()` — runs daily |
| **Read by** | AI Think Tank analysis, strategy evaluation |
| **Write pattern** | INSERT once per signal (after 20 days have elapsed) |
| **Key indexes** | `signal_id` (unique FK), `outcome_date` |

```sql
-- Signal accuracy by strategy
SELECT us.name, ss.signal_type,
       COUNT(*) AS total,
       ROUND(AVG(CASE WHEN sso.is_profitable_20d THEN 1.0 ELSE 0.0 END)*100,1) AS accuracy_20d,
       ROUND(AVG(sso.return_20d_pct)::numeric, 2) AS avg_return_20d
FROM strategy_signal_outcomes sso
JOIN strategy_signals ss ON ss.id = sso.signal_id
JOIN user_strategies us ON us.id = ss.strategy_id
GROUP BY us.name, ss.signal_type
ORDER BY accuracy_20d DESC;
```

---

#### `backtest_runs` — Backtest Results

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/backtest.py → BacktestRun` |
| **Written by** | `backtest_service.run_backtest()` |
| **Read by** | Backtesting page results, AI Think Tank backtest interpreter |
| **Write pattern** | INSERT on completion; never updated |
| **Key indexes** | `strategy_id`, `stock_id`, `created_at` |

```sql
-- Best backtest runs by Sharpe ratio
SELECT st.name AS strategy, s.symbol, br.start_date, br.end_date,
       br.total_return_pct, br.sharpe_ratio, br.max_drawdown_pct,
       br.win_rate, br.alpha, br.beta
FROM backtest_runs br
JOIN user_strategies us ON us.id = br.strategy_id
JOIN strategy_templates st ON st.id = us.template_id
LEFT JOIN stocks s ON s.id = br.stock_id
ORDER BY br.sharpe_ratio DESC NULLS LAST
LIMIT 20;
```

---

#### `ai_action_logs` — LLM Call History

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/telemetry.py → AiActionLog` |
| **Written by** | `ai_action_log_service.log_action()` after every LLM call |
| **Read by** | AI activity log partial, AI analysis history partial |
| **Write pattern** | INSERT only |
| **Key indexes** | `user_id`, `created_at`, `cache_key`, `cache_hit` |

```sql
-- Cache hit rate for LLM calls
SELECT
  COUNT(*) AS total_calls,
  SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) AS cache_hits,
  ROUND(AVG(duration_ms)) AS avg_duration_ms,
  ROUND(SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END)::numeric / COUNT(*) * 100, 1) AS hit_rate_pct
FROM ai_action_logs
WHERE created_at > NOW() - INTERVAL '7 days';

-- Slowest LLM calls
SELECT action_type, model_used, duration_ms, created_at
FROM ai_action_logs ORDER BY duration_ms DESC LIMIT 10;
```

---

#### `search_query_logs` — Search Telemetry

| Property | Value |
|----------|-------|
| **SQLAlchemy model** | `backend/app/models/telemetry.py → SearchQueryLog` |
| **Written by** | `search_telemetry_service` on every stock search |
| **Read by** | Data page search latency partial |
| **Write pattern** | INSERT only — high volume, consider periodic pruning |
| **Key indexes** | `created_at`, `duration_ms`, `user_id` |

```sql
-- Search latency percentiles
SELECT
  PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
  PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
  PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY duration_ms) AS p99_ms,
  COUNT(*) AS total_searches
FROM search_query_logs
WHERE created_at > NOW() - INTERVAL '24 hours';

-- Most common search queries
SELECT query_text, COUNT(*) AS search_count
FROM search_query_logs
GROUP BY query_text ORDER BY search_count DESC LIMIT 20;
```

---

### 15.4 Complete Table Inventory

| Table | ~Rows (typical) | Primary Writer | Primary Reader | Growth Rate |
|-------|----------------|----------------|----------------|-------------|
| `stocks` | 5,000–6,000 | `load_indian_tickers.py` | All services | Low (quarterly updates) |
| `stock_prices` | 5M–30M | `fetch_prices.py` | `market_data_service` | ~5,000 rows/day |
| `ingestion_runs` | Hundreds | All ingestion scripts | Data page | ~5–10 rows/day |
| `stock_performance_snapshots` | 5,000–6,000 | `refresh_analytics.py` | Overview service | ~5,000 rows/day |
| `market_analytics_cache` | Dozens | Analytics service | Market overview | ~10 rows/refresh |
| `market_indices` | 10–20 | `load_index_memberships.py` | Overview service | Very low |
| `stock_index_memberships` | 1,000–2,000 | `load_index_memberships.py` | Index lookup | Low (quarterly) |
| `index_funds` | 50–200 | `ingest_index_funds.py` | Index fund page | Low |
| `index_fund_prices` | 50K–500K | `ingest_index_funds.py` | Index fund chart | ~200 rows/day |
| `stock_fundamentals_latest` | 5,000–6,000 | `fundamentals_service` | Explore, screener | ~5,000 rows/refresh |
| `stock_news_articles` | 10K–100K | `ingest_stock_news.py` | News section | ~100–1,000/day |
| `stock_news_links` | 20K–300K | `news_service` | News join queries | Proportional to articles |
| `company_aliases` | 15K–20K | `news_service` | News matching | Low |
| `news_provider_quota_state` | 2 rows | `news_service` | Quota check | Static |
| `users` | Tens to hundreds | `/api/auth/register` | All auth endpoints | Low |
| `user_credentials` | Same as users | `/api/auth/register` | `/api/auth/login` | Same as users |
| `auth_sessions` | Dozens to hundreds | `/api/auth/login` | Every request | ~1–5/day/user |
| `portfolios` | 4 × users | `ensure_default_portfolios` | Portfolio pages | Low |
| `portfolio_holdings` | Hundreds | `paper_trading_service` | Holdings display | Per trade |
| `transactions` | Hundreds to thousands | Trading services | History display | Per trade |
| `paper_orders` | Hundreds to thousands | `paper_trading_service` | Trading page | Per order |
| `paper_trades` | Hundreds to thousands | `paper_trading_service` | Trade history | Per execution |
| `strategy_templates` | 6–10 | `seed_strategy_templates.py` | Strategy forms | Very low |
| `user_strategies` | Dozens | Strategy lab | Signal generation | Per user action |
| `strategy_signals` | Thousands | `run_generate_signal()` | Activity log | Per signal run |
| `strategy_signal_outcomes` | Thousands | `signal_outcome_service` | AI analysis | Daily batch |
| `backtest_runs` | Hundreds | `backtest_service` | Backtest page | Per run |
| `backtest_trades` | Thousands | `backtest_service` | Trade log | Per backtest trade |
| `ai_action_logs` | Hundreds | `ai_action_log_service` | AI history | Per LLM call |
| `search_query_logs` | Tens of thousands | `search_telemetry_service` | Data page | Per search |

---

## Quick Reference Card

```
Data sources:
  yfinance      → OHLCV prices & fundamentals (free)
  MarketAux     → News articles (1000 req/day free)
  Alpha Vantage → News articles (25 req/day free)
  NSE CSVs      → Index constituent lists (manual)

Key scripts:
  scripts/init_db.py                  ← Initialize schema
  scripts/load_indian_tickers.py      ← Load stock master
  scripts/load_index_memberships.py   ← Load index constituents
  scripts/fetch_prices.py             ← Fetch OHLCV
  scripts/ingest_priority.py          ← Daily price sync
  scripts/ingest_stock_news.py        ← Fetch news
  scripts/refresh_analytics.py        ← Recompute caches
  scripts/seed_strategy_templates.py  ← Create strategies

Key tables:
  stocks                  ← Stock master (symbol, exchange, index flags)
  stock_prices            ← OHLCV bars (one row per stock per day)
  stock_index_memberships ← Which stocks are in which index
  stock_performance_snapshots ← Pre-computed 1m/3m/6m/1y returns
  market_analytics_cache  ← JSONB cache for expensive queries
  ingestion_runs          ← Batch job tracking (monitor health here)
  stock_news_articles     ← News articles
  stock_news_links        ← Article → stock many-to-many

Debug:
  SELECT * FROM ingestion_runs ORDER BY started_at DESC LIMIT 10;
  docker compose logs backend -f
```
