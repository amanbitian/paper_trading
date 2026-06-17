# KT-02: Backend Knowledge Transfer
### Paper Trading App — New Intern Onboarding Guide

---

## Table of Contents
1. [What is the Backend?](#1-what-is-the-backend)
2. [Tech Stack](#2-tech-stack)
3. [High-Level Architecture](#3-high-level-architecture)
4. [Directory Structure](#4-directory-structure)
5. [FastAPI Application Setup](#5-fastapi-application-setup)
6. [Database Layer](#6-database-layer)
7. [ER Diagram](#7-er-diagram)
8. [API Routing Map](#8-api-routing-map)
9. [Services Layer](#9-services-layer)
10. [Authentication & Security](#10-authentication--security)
11. [Request Lifecycle](#11-request-lifecycle)
12. [Configuration & Environment](#12-configuration--environment)
13. [Running & Migrations](#13-running--migrations)
14. [Common Tasks for Interns](#14-common-tasks-for-interns)

---

## 1. What is the Backend?

The backend is a **FastAPI** application written in Python. It does everything:
- Serves the web UI (HTML pages)
- Exposes a JSON REST API (used by Streamlit and JS)
- Runs business logic (trading, backtesting, signals)
- Manages the database (PostgreSQL via SQLAlchemy ORM)
- Authenticates users (JWT tokens)
- Ingests market data (via yfinance)

The backend lives entirely in `backend/` and runs on port **8000**.

---

## 2. Tech Stack

| Component | Technology | Version | Why |
|-----------|-----------|---------|-----|
| Web Framework | FastAPI | 0.115.6 | Async Python, auto OpenAPI docs, type safety |
| ASGI Server | Uvicorn | 0.34.0 | High-performance async server for FastAPI |
| ORM | SQLAlchemy | 2.0.36 | Python ORM for database access |
| Migrations | Alembic | 1.14.0 | Versioned DB schema changes |
| Validation | Pydantic | 2.10.4 | Request/response schema validation |
| Auth | python-jose + cryptography | 3.3.0 | JWT token creation and verification |
| Password | Bcrypt | 4.0.1 | Secure password hashing |
| Rate Limiting | Slowapi | 0.1.9 | Prevent brute force attacks |
| Templating | Jinja2 | 3.1.6 | HTML template rendering |
| HTTP Client | Httpx | 0.27.0 | Async HTTP calls to external APIs |
| Data | yfinance | 1.3.0 | Stock price data from Yahoo Finance |
| Numerics | Pandas + NumPy | 2.2.3 / 2.2.1 | Data wrangling, price calculations |

---

## 3. High-Level Architecture

```mermaid
graph TB
    subgraph "Clients"
        Browser["🌐 Browser"]
        Streamlit["Streamlit UI"]
        CLI["CLI Scripts"]
    end

    subgraph "FastAPI Application"
        direction TB
        MainApp["main.py\n(App factory, middleware, routers)"]
        
        subgraph "Routers (HTTP layer)"
            AuthR["auth.py\n/api/auth/*"]
            StocksR["stocks.py\n/api/stocks/*"]
            PortR["portfolios.py\n/api/portfolios/*"]
            TxnR["transactions.py\n/api/transactions/*"]
            PaperR["paper_trading.py\n/api/paper-trading/*"]
            StratR["strategies.py\n/api/strategies/*"]
            BackR["backtest.py\n/api/backtest/*"]
            WebR["web.py\n/web/* (HTML pages)"]
            PartR["web_*_partials.py\n/web/partials/*"]
        end

        subgraph "Services (Business logic)"
            PortSvc["portfolio_service.py"]
            PaperSvc["paper_trading_service.py"]
            StratSvc["strategy_service.py"]
            BackSvc["backtest_service.py"]
            MktSvc["market_data_service.py"]
            NewsSvc["news_service.py"]
            AISvc["web_ai_think_tank_helpers.py"]
        end

        subgraph "Models (SQLAlchemy ORM)"
            UserM["user.py"]
            PortM["portfolio.py"]
            StockM["stock.py"]
            StratM["strategy.py"]
            BackM["backtest.py"]
            NewsM["news.py"]
        end
    end

    subgraph "Database"
        PG["PostgreSQL 16"]
    end

    subgraph "External APIs"
        YF["Yahoo Finance\n(yfinance)"]
        MKTAUX["MarketAux API\n(news)"]
        AV["Alpha Vantage\n(news)"]
        Ollama["Ollama LLM\n(local AI)"]
    end

    Browser --> MainApp
    Streamlit --> MainApp
    CLI --> MainApp
    MainApp --> AuthR & StocksR & PortR & TxnR & PaperR & StratR & BackR & WebR & PartR
    AuthR & StocksR & PortR & PaperR & StratR & BackR --> PortSvc & PaperSvc & StratSvc & BackSvc & MktSvc
    PortSvc & PaperSvc & StratSvc & BackSvc & MktSvc & NewsSvc --> UserM & PortM & StockM & StratM & BackM & NewsM
    UserM & PortM & StockM & StratM & BackM & NewsM --> PG
    MktSvc --> YF
    NewsSvc --> MKTAUX & AV
    AISvc --> Ollama
```

---

## 4. Directory Structure

```
backend/
├── app/
│   ├── main.py              ← FastAPI app creation, all routers registered here
│   ├── config.py            ← Settings class (reads .env file)
│   ├── database.py          ← SQLAlchemy engine + session factory
│   ├── security.py          ← JWT creation, verification, OAuth2 dependency
│   ├── limiter.py           ← Rate limiter setup (slowapi)
│   ├── web_utils.py         ← Shared template rendering helpers
│   │
│   ├── models/              ← SQLAlchemy ORM models (map to DB tables)
│   │   ├── auth.py          ← UserCredential, AuthSession, PasswordResetToken
│   │   ├── user.py          ← User
│   │   ├── portfolio.py     ← Portfolio, PortfolioHolding, Transaction, PaperOrder, PaperTrade, Snapshot
│   │   ├── stock.py         ← Stock, StockPrice, IngestionRun, PerformanceSnapshot, AnalyticsCache
│   │   ├── strategy.py      ← StrategyTemplate, UserStrategy, StrategySignal, SignalOutcome
│   │   ├── backtest.py      ← BacktestRun, BacktestTrade
│   │   ├── index_fund.py    ← IndexFund, IndexFundPrice
│   │   ├── market_index.py  ← MarketIndex, StockIndexMembership
│   │   ├── fundamentals.py  ← StockFundamentalsLatest
│   │   ├── news.py          ← StockNewsArticle, StockNewsLink, CompanyAlias, quotas
│   │   └── telemetry.py     ← SearchQueryLog, AiActionLog
│   │
│   ├── schemas/             ← Pydantic schemas (request/response shapes)
│   │   ├── auth.py
│   │   ├── portfolio.py
│   │   ├── stock.py
│   │   ├── strategy.py
│   │   ├── backtest.py
│   │   ├── transaction.py
│   │   ├── paper_trading.py
│   │   ├── market.py
│   │   ├── news.py
│   │   ├── ai.py
│   │   ├── algo.py
│   │   ├── index_fund.py
│   │   └── data.py
│   │
│   ├── routers/             ← HTTP route handlers
│   │   ├── auth.py          ← /api/auth/*
│   │   ├── stocks.py        ← /api/stocks/*
│   │   ├── portfolios.py    ← /api/portfolios/*
│   │   ├── transactions.py  ← /api/transactions/*
│   │   ├── paper_trading.py ← /api/paper-trading/*
│   │   ├── strategies.py    ← /api/strategies/*
│   │   ├── backtest.py      ← /api/backtest/*
│   │   ├── market.py        ← /api/market/*
│   │   ├── index_funds.py   ← /api/index-funds/*
│   │   ├── news.py          ← /api/news/*
│   │   ├── data.py          ← /api/data/*
│   │   ├── ai.py            ← /api/ai/*
│   │   ├── web.py           ← /web/* (HTML)
│   │   └── web_*_partials.py ← /web/partials/* (HTMX)
│   │
│   ├── services/            ← Business logic (pure Python, no HTTP)
│   │   ├── portfolio_service.py
│   │   ├── paper_trading_service.py
│   │   ├── strategy_service.py
│   │   ├── backtest_service.py
│   │   ├── market_data_service.py
│   │   ├── market_overview_service.py
│   │   ├── news_service.py
│   │   ├── risk_service.py
│   │   ├── charges_service.py
│   │   ├── fundamentals_service.py
│   │   ├── ticker_service.py
│   │   └── web_*_helpers.py ← Page-specific service helpers
│   │
│   ├── strategies/          ← Trading strategy implementations
│   │   ├── base.py          ← BaseStrategy, SignalResult
│   │   ├── rsi_strategy.py
│   │   ├── sma_crossover_strategy.py
│   │   ├── macd_strategy.py
│   │   ├── breakout_strategy.py
│   │   └── advanced_strategies.py
│   │
│   ├── constants/           ← Static lookup data
│   │   └── market_indices.py ← Index definitions (NIFTY50 symbols, etc.)
│   │
│   ├── utils/               ← Shared helper functions
│   ├── jobs/                ← Background scheduled tasks
│   ├── templates/           ← Jinja2 HTML templates
│   └── static/              ← CSS, JS, images
│
├── alembic/                 ← Database migration scripts
│   ├── alembic.ini
│   ├── env.py
│   └── versions/
│       ├── 0001_initial_schema.py
│       ├── 0002_split_auth.py
│       └── ... (17 total)
│
├── Dockerfile
└── requirements.txt
```

---

## 5. FastAPI Application Setup

### main.py Structure

`main.py` is the entry point. It:
1. Creates the FastAPI `app` object
2. Registers all middleware (CORS, timing, rate limiting)
3. Mounts all routers
4. Adds startup/shutdown event handlers

```python
# Simplified structure of main.py

app = FastAPI(title="Paper Trading App", version="1.0")

# Middleware
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:8501"])
app.add_middleware(TimingMiddleware)
app.state.limiter = limiter  # slowapi rate limiter

# Register all routers
app.include_router(auth_router,        prefix="/api/auth")
app.include_router(stocks_router,      prefix="/api/stocks")
app.include_router(portfolios_router,  prefix="/api/portfolios")
app.include_router(paper_trading_router, prefix="/api/paper-trading")
app.include_router(strategies_router,  prefix="/api/strategies")
app.include_router(backtest_router,    prefix="/api/backtest")
app.include_router(web_router,         prefix="/web")
# ... more routers

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")
```

### Dependency Injection (FastAPI Depends)

FastAPI uses `Depends()` to inject shared dependencies:

```python
# database.py defines this
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# security.py defines this
def get_current_user(token: str = Depends(oauth2_scheme), db = Depends(get_db)):
    # decode JWT, find user in DB
    return user

# A router uses both
@router.get("/portfolios")
def list_portfolios(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return portfolio_service.get_user_portfolios(db, current_user.id)
```

---

## 6. Database Layer

### SQLAlchemy Setup

`database.py` creates the connection pool and session factory:

```python
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,    # test connections before using
    pool_size=20,          # keep 20 connections open
    max_overflow=40,       # allow 40 extra under load
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()  # all models inherit from this
```

### Model Pattern

Every model follows this pattern:

```python
# models/user.py
from app.database import Base
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import relationship

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    user_name = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    starting_cash = Column(Float, default=1_000_000.0)
    current_cash = Column(Float)
    risk_profile = Column(String, default="moderate")
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    portfolios = relationship("Portfolio", back_populates="user", cascade="all, delete")
    credentials = relationship("UserCredential", back_populates="user", uselist=False)
```

---

## 7. ER Diagram

```mermaid
erDiagram
    USERS {
        int id PK
        string name
        string user_name UK
        string email UK
        float starting_cash
        float current_cash
        string risk_profile
        datetime created_at
    }

    USER_CREDENTIALS {
        int id PK
        int user_id FK
        string hashed_password
        datetime created_at
    }

    AUTH_SESSIONS {
        int id PK
        int user_id FK
        string jti_hash UK
        datetime expires_at
        bool is_active
    }

    PORTFOLIOS {
        int id PK
        int user_id FK
        string portfolio_name
        string type
        float starting_value
        float cash_balance
        datetime created_at
    }

    PORTFOLIO_HOLDINGS {
        int id PK
        int portfolio_id FK
        int stock_id FK
        float qty
        float avg_buy_price
        float total_invested
        float realized_pnl
    }

    TRANSACTIONS {
        int id PK
        int portfolio_id FK
        int stock_id FK
        string side
        float qty
        float price
        float charges
        float net_amount
        string source
        string notes
        datetime created_at
    }

    PAPER_ORDERS {
        int id PK
        int portfolio_id FK
        int stock_id FK
        string order_type
        string side
        float quantity
        float limit_price
        float stop_price
        string status
        datetime expires_at
        datetime created_at
    }

    PAPER_TRADES {
        int id PK
        int paper_order_id FK
        float executed_price
        float charges
        float slippage
        datetime executed_at
    }

    STOCKS {
        int id PK
        string symbol UK
        string yahoo_symbol UK
        string exchange
        string sector
        string industry
        bool is_nifty50
        bool is_nifty500
        bool is_delisted
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
    }

    STRATEGY_TEMPLATES {
        int id PK
        string name UK
        string description
        json default_parameters
        datetime created_at
    }

    USER_STRATEGIES {
        int id PK
        int user_id FK
        int template_id FK
        string name
        json parameters
        json risk_settings
        bool is_active
        datetime created_at
    }

    STRATEGY_SIGNALS {
        int id PK
        int strategy_id FK
        int stock_id FK
        date signal_date
        string signal_type
        float confidence_score
        string reason
        json indicators
    }

    BACKTEST_RUNS {
        int id PK
        int strategy_id FK
        int stock_id FK
        date start_date
        date end_date
        float total_return_pct
        float sharpe_ratio
        float max_drawdown_pct
        float win_rate
        float alpha
        float beta
        string benchmark_code
        datetime created_at
    }

    STOCK_NEWS_ARTICLES {
        int id PK
        string headline
        string summary
        string url
        string source
        float sentiment_score
        datetime published_at
    }

    STOCK_NEWS_LINKS {
        int id PK
        int article_id FK
        int stock_id FK
        float match_confidence
    }

    USERS ||--o{ PORTFOLIOS : "owns"
    USERS ||--|| USER_CREDENTIALS : "has"
    USERS ||--o{ AUTH_SESSIONS : "has"
    USERS ||--o{ USER_STRATEGIES : "creates"
    PORTFOLIOS ||--o{ PORTFOLIO_HOLDINGS : "contains"
    PORTFOLIOS ||--o{ TRANSACTIONS : "records"
    PORTFOLIOS ||--o{ PAPER_ORDERS : "places"
    PAPER_ORDERS ||--o{ PAPER_TRADES : "executes"
    STOCKS ||--o{ PORTFOLIO_HOLDINGS : "held in"
    STOCKS ||--o{ STOCK_PRICES : "has"
    STOCKS ||--o{ STRATEGY_SIGNALS : "receives"
    STOCKS ||--o{ BACKTEST_RUNS : "tested on"
    STOCKS ||--o{ STOCK_NEWS_LINKS : "linked to"
    STOCK_NEWS_ARTICLES ||--o{ STOCK_NEWS_LINKS : "links to"
    STRATEGY_TEMPLATES ||--o{ USER_STRATEGIES : "instantiated as"
    USER_STRATEGIES ||--o{ STRATEGY_SIGNALS : "generates"
    USER_STRATEGIES ||--o{ BACKTEST_RUNS : "run as"
```

---

## 8. API Routing Map

```
/api/auth/
  POST  /register              ← Create new user (rate: 5/min)
  POST  /login                 ← Get JWT token (rate: 10/min)
  POST  /logout                ← Invalidate token
  POST  /forgot-password       ← Send reset email (rate: 3/min)
  POST  /reset-password        ← Reset with token (rate: 5/min)

/api/stocks/
  GET   /search?q=RELIANCE     ← Fuzzy stock search
  GET   /{symbol}              ← Stock detail + prices
  GET   /                      ← List all stocks (with filters)
  GET   /{symbol}/prices       ← OHLCV price history
  GET   /{symbol}/fundamentals ← PE, ROE, market cap

/api/portfolios/
  GET   /                      ← All portfolios for user
  POST  /                      ← Create portfolio
  GET   /{id}                  ← Portfolio detail
  DELETE /{id}                 ← Delete portfolio
  GET   /{id}/holdings         ← Current holdings with PnL
  GET   /{id}/performance      ← Portfolio performance history

/api/transactions/
  GET   /                      ← Transaction history
  POST  /                      ← Record a transaction (buy/sell)

/api/paper-trading/
  GET   /orders                ← Open/filled orders
  POST  /orders                ← Place an order
  DELETE /orders/{id}          ← Cancel an order
  GET   /trades                ← Executed trade history

/api/strategies/
  GET   /templates             ← All strategy templates
  GET   /                      ← User's strategies
  POST  /                      ← Create a user strategy
  GET   /{id}                  ← Strategy detail
  GET   /{id}/signals          ← Latest signals for strategy
  POST  /{id}/generate-signals ← Run signal generation now

/api/backtest/
  POST  /run                   ← Start a backtest
  GET   /                      ← Backtest history
  GET   /{id}                  ← Backtest detail + metrics
  GET   /{id}/trades           ← Trades within backtest

/api/market/
  GET   /indices               ← NIFTY50, SENSEX, etc.
  GET   /movers/top-gainers    ← Top gainers today
  GET   /movers/top-losers     ← Top losers today
  GET   /overview              ← Market summary

/api/index-funds/
  GET   /                      ← List index funds
  GET   /{symbol}              ← Index fund detail + prices
  POST  /                      ← Add index fund

/api/news/
  GET   /{symbol}              ← News for a stock

/api/ai/
  POST  /analyze               ← LLM analysis of a stock
  GET   /logs                  ← AI action log

/api/data/
  GET   /ingestion/status      ← Data sync status
  POST  /ingestion/trigger     ← Trigger sync
```

---

## 9. Services Layer

The services layer contains all business logic. Routers call services; services call models.

```mermaid
graph LR
    subgraph "Router (HTTP)"
        R["portfolios.py\nPOST /api/portfolios/{id}/buy"]
    end

    subgraph "Services (Business Logic)"
        PS["paper_trading_service.py\nplace_order()"]
        CS["charges_service.py\ncalculate_charges()"]
        ES["execution_service.py\napply_slippage()"]
        MDS["market_data_service.py\nget_latest_price()"]
    end

    subgraph "Models (DB)"
        PO["PaperOrder"]
        PT["PaperTrade"]
        PH["PortfolioHolding"]
        PF["Portfolio (cash_balance)"]
        SP["StockPrice"]
    end

    R --> PS
    PS --> CS
    PS --> ES
    PS --> MDS
    MDS --> SP
    PS --> PO
    PS --> PT
    PS --> PH
    PS --> PF
```

### Key Service Files

| Service | File | Key Functions |
|---------|------|--------------|
| Portfolio | `portfolio_service.py` | `get_holdings()`, `calculate_portfolio_value()`, `create_portfolio()` |
| Paper Trading | `paper_trading_service.py` | `place_order()`, `cancel_order()`, `match_limit_orders()` |
| Market Data | `market_data_service.py` | `fetch_prices_from_yfinance()`, `sync_stock_prices()`, `get_latest_price()` |
| Strategy | `strategy_service.py` | `get_strategy_instance()`, `generate_signals()` |
| Backtest | `backtest_service.py` | `run_backtest()`, `calculate_metrics()`, `walk_forward_test()` |
| News | `news_service.py` | `fetch_news_for_stock()`, `link_articles_to_stocks()`, `score_sentiment()` |
| Risk | `risk_service.py` | `calculate_position_size()`, `calculate_var()` |
| Charges | `charges_service.py` | `calculate_zerodha_charges()`, `calculate_stt()` |
| Ticker | `ticker_service.py` | `search_tickers()`, `deduplicate_stocks()` |

---

## 10. Authentication & Security

### Password Flow

```mermaid
flowchart TD
    A[User registers\npassword: 'MyPass123'] --> B[bcrypt.hashpw\nhash: '$2b$12$...']
    B --> C[Store in user_credentials table\nnever store plain password]

    D[User logs in\npassword: 'MyPass123'] --> E[bcrypt.checkpw\npassword vs stored hash]
    E -->|Match| F[Create JWT token]
    E -->|No match| G[Return 401 Unauthorized]
    F --> H[Store jti in auth_sessions]
    H --> I[Return token to client]
```

### JWT Token Structure

```
Header:  { "alg": "HS256", "typ": "JWT" }
Payload: {
  "sub": "user@example.com",   ← subject (email)
  "user_id": 42,
  "jti": "abc-123-uuid",       ← unique token ID (for revocation)
  "exp": 1735689600            ← expiry timestamp
}
Signature: HMAC-SHA256(base64(header) + "." + base64(payload), JWT_SECRET_KEY)
```

### Token Revocation

Unlike simple JWTs that can't be revoked, this app stores `jti_hash` in `auth_sessions`:

```mermaid
flowchart LR
    A[Request arrives\nAuthorization: Bearer eyJ...] --> B[Decode JWT\nExtract jti]
    B --> C{jti in auth_sessions\nAND is_active=True?}
    C -->|Yes| D[Allow request]
    C -->|No| E[Return 401\nToken revoked or expired]
    
    F[User logs out] --> G[DELETE auth_sessions\nWHERE jti_hash=...]
    G --> H[Future requests with\nthis token → 401]
```

### Rate Limiting

Implemented with `slowapi` (mirrors Flask-Limiter):

```python
@router.post("/login")
@limiter.limit("10/minute")  ← max 10 login attempts per minute per IP
async def login(request: Request, ...):
    ...

@router.post("/register")
@limiter.limit("5/minute")   ← max 5 registrations per minute per IP
async def register(request: Request, ...):
    ...
```

---

## 11. Request Lifecycle

### Complete Flow for `GET /api/portfolios/7/holdings`

```mermaid
sequenceDiagram
    participant Client
    participant ASGI as Uvicorn (ASGI)
    participant MW as Middleware
    participant Router as portfolios.py router
    participant Dep as Depends(get_current_user)
    participant Svc as portfolio_service
    participant DB as PostgreSQL

    Client->>ASGI: GET /api/portfolios/7/holdings\nAuthorization: Bearer eyJ...
    ASGI->>MW: TimingMiddleware starts timer
    MW->>Router: Route matched: get_holdings(id=7)
    Router->>Dep: Resolve get_db() → SQLAlchemy Session
    Router->>Dep: Resolve get_current_user(token, db)
    Dep->>DB: SELECT * FROM auth_sessions WHERE jti_hash=...
    DB-->>Dep: Session row (valid)
    Dep->>DB: SELECT * FROM users WHERE id=...
    DB-->>Dep: User row
    Dep-->>Router: User object (user_id=42)
    Router->>Svc: get_holdings(db, portfolio_id=7, user_id=42)
    Svc->>DB: SELECT * FROM portfolios WHERE id=7 AND user_id=42
    DB-->>Svc: Portfolio (ownership verified)
    Svc->>DB: SELECT * FROM portfolio_holdings\nJOIN stocks ON...\nWHERE portfolio_id=7
    DB-->>Svc: Holdings rows
    Svc->>DB: SELECT close FROM stock_prices\nWHERE stock_id IN (...) AND price_datetime=(latest)
    DB-->>Svc: Latest prices
    Svc-->>Router: List[HoldingRead] with computed unrealized_pnl
    Router-->>MW: JSON response 200 OK
    MW->>MW: Record duration_ms in logs
    MW-->>Client: 200 { holdings: [...] }
```

---

## 12. Configuration & Environment

### Settings Hierarchy (config.py)

Settings are read from environment variables (or a `.env` file):

```python
class Settings(BaseSettings):
    # App
    APP_ENV: str = "local"             # "local" | "production"
    
    # Database
    DATABASE_URL: str                  # postgresql://user:pass@host/dbname
    
    # Auth
    JWT_SECRET_KEY: str = "changeme"   # MUST change for production
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    
    # Dev bypass (NEVER enable in production)
    DEBUG_AUTH_BYPASS: bool = True
    DEBUG_AUTH_USER_EMAIL: str = "test@example.com"
    
    # Market data
    YFINANCE_DEFAULT_PERIOD: str = "1y"
    YFINANCE_DEFAULT_INTERVAL: str = "1d"
    
    # AI
    AI_FEATURES_ENABLED: bool = False
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_DEFAULT_MODEL: str = "qwen3:14b"
    
    # News APIs
    MARKETAUX_API_TOKEN: str = ""
    ALPHA_VANTAGE_API_KEY: str = ""
    
    # Email
    EMAIL_ALERTS_ENABLED: bool = False
    SMTP_HOST: str = ""
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""

    class Config:
        env_file = ".env"
```

### Production Validation

```python
# In config.py — prevents insecure production deployments
if settings.APP_ENV == "production":
    if settings.JWT_SECRET_KEY == "changeme":
        raise ValueError("JWT_SECRET_KEY must be changed for production!")
    if settings.DEBUG_AUTH_BYPASS:
        raise ValueError("DEBUG_AUTH_BYPASS must be False in production!")
```

---

## 13. Running & Migrations

### Start the Backend

```bash
# Via Docker (recommended)
docker compose up -d backend postgres

# Direct (for local dev without Docker)
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Database Migrations with Alembic

```bash
# Run all pending migrations (applies new schema changes)
alembic upgrade head

# Create a new migration file (after changing models/)
alembic revision --autogenerate -m "add_column_to_stocks"

# See current migration state
alembic current

# Rollback one migration
alembic downgrade -1
```

### Migration File Structure

```python
# alembic/versions/0011_stock_delisted_flags.py

def upgrade() -> None:
    op.add_column("stocks", sa.Column("is_delisted", sa.Boolean(), default=False))
    op.add_column("stocks", sa.Column("delisted_at", sa.DateTime(), nullable=True))

def downgrade() -> None:
    op.drop_column("stocks", "is_delisted")
    op.drop_column("stocks", "delisted_at")
```

### API Documentation

FastAPI auto-generates interactive API docs:
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **OpenAPI JSON**: `http://localhost:8000/openapi.json`

---

## 14. Common Tasks for Interns

### Task: Add a new API endpoint

1. Create a Pydantic schema in `schemas/`
2. Add a service function in `services/`
3. Add a route in the appropriate `routers/` file
4. Register the router in `main.py` (if it's a new file)
5. Test via `http://localhost:8000/docs`

### Task: Add a new database column

1. Edit the model in `models/`
2. Run `alembic revision --autogenerate -m "describe_your_change"`
3. Review the generated file in `alembic/versions/`
4. Apply: `alembic upgrade head`

### Task: Debug a 500 error

```bash
# Check backend logs
docker compose logs backend --tail=100 -f

# OR with timestamps
docker compose logs backend -t --tail=50
```

### Task: Test an API endpoint manually

```bash
# Login first
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "password": "password123"}'
# → {"access_token": "eyJ..."}

# Use token
curl http://localhost:8000/api/portfolios/ \
  -H "Authorization: Bearer eyJ..."
```

---

## Quick Reference Card

```
Backend tech:
  FastAPI 0.115 + Uvicorn → HTTP server
  SQLAlchemy 2.0 → ORM (models → tables)
  Alembic → DB migrations
  Pydantic → schema validation
  python-jose → JWT tokens
  bcrypt → password hashing
  slowapi → rate limiting

Important URLs (local dev):
  http://localhost:8000/docs       ← Swagger UI (explore all APIs)
  http://localhost:8000/web/*      ← Web UI pages
  http://localhost:8000/api/*      ← JSON API

Key files:
  backend/app/main.py              ← App factory
  backend/app/config.py            ← All settings
  backend/app/database.py          ← DB connection
  backend/app/security.py          ← JWT + auth
  backend/app/models/              ← DB table definitions
  backend/app/schemas/             ← Request/response shapes
  backend/app/routers/             ← HTTP endpoints
  backend/app/services/            ← Business logic

Migrations:
  alembic upgrade head             ← Apply all migrations
  alembic revision --autogenerate  ← Create new migration
```
