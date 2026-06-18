# KT-05: Data Science & Gen AI Knowledge Transfer
### Paper Trading App — New Intern Onboarding Guide

---

## Table of Contents
1. [What is Data Science & Gen AI in This Project?](#1-what-is-data-science--gen-ai-in-this-project)
2. [AI/ML Features Overview](#2-aiml-features-overview)
3. [System Architecture](#3-system-architecture)
4. [Local LLM Integration (Ollama)](#4-local-llm-integration-ollama)
5. [AI Think Tank Feature](#5-ai-think-tank-feature)
6. [Natural Language Stock Screener](#6-natural-language-stock-screener)
7. [Sentiment Analysis Pipeline](#7-sentiment-analysis-pipeline)
8. [Stock Fundamentals & Scoring](#8-stock-fundamentals--scoring)
9. [Market Analytics & Trend Analysis](#9-market-analytics--trend-analysis)
10. [Signal Outcome Tracking (ML Feedback Loop)](#10-signal-outcome-tracking-ml-feedback-loop)
11. [Performance Analytics & Statistics](#11-performance-analytics--statistics)
12. [Advanced Strategies (ML-Style)](#12-advanced-strategies-ml-style)
13. [Database Schema for DS/AI](#13-database-schema-for-dsai)
14. [Notebooks](#14-notebooks)
15. [Common Tasks for Interns](#15-common-tasks-for-interns)

---

## 1. What is Data Science & Gen AI in This Project?

The data science and Gen AI layer adds **intelligence** on top of the raw data and algo trading systems. It covers:

| Area | What it Does | Tech Used |
|------|-------------|----------|
| **Gen AI / LLM** | Answer natural-language questions about stocks, portfolios, market trends | Ollama (local LLM), `qwen3:14b` model |
| **NL Screener** | Convert "find me undervalued IT stocks" into SQL-like filters | LLM prompt engineering |
| **Sentiment Analysis** | Score news articles as positive/negative for each stock | NLP scoring on article text |
| **Fundamentals Scoring** | Fetch PE, ROE, market cap, rank stocks by value metrics | yfinance fundamentals |
| **Market Analytics** | Trend detection, sector performance, momentum scoring | Pandas + NumPy statistics |
| **Signal ML Feedback** | Track if strategy signals were actually profitable → train/evaluate | Signal outcomes tracking |
| **Statistical Backtesting** | Sharpe ratio, walk-forward testing, overfitting detection | NumPy/SciPy statistics |

---

## 2. AI/ML Features Overview

```mermaid
mindmap
  root((Paper Trading\nAI/ML Features))
    LLM/GenAI
      AI Think Tank
        Stock Analysis Chat
        Portfolio Review
        Market Commentary
      Natural Language Screener
        "Show me oversold IT stocks"
        "Find undervalued mid-caps"
    Data Science
      Sentiment Analysis
        News article scoring
        Per-stock sentiment index
      Fundamentals Analysis
        PE ratio ranking
        ROE screening
        Debt-to-equity filters
    Statistics & Analytics
      Market Trends
        Sector momentum
        Period-over-period returns
        Breadth analysis
      Signal Evaluation
        BUY signal success rate
        Confidence score calibration
      Backtest Statistics
        Sharpe ratio
        Walk-forward testing
        Overfitting detection
    ML Feedback Loop
      Signal outcome tracking
      Strategy performance history
      Algo finding service
```

---

## 3. System Architecture

```mermaid
graph TB
    subgraph "User Interface"
        AIUI["AI Think Tank\n/web/ai-think-tank"]
        ScreenerUI["NL Screener\n/web/explore"]
        TrendsUI["Trends & Analytics\n/web/trends"]
    end

    subgraph "API Layer"
        AIR["/api/ai/*"]
        ExploreR["/api/stocks/* + screener"]
        TrendsR["/api/market/trends/*"]
    end

    subgraph "AI Services"
        ThinkTank["web_ai_think_tank_helpers.py\nOrchestrate LLM queries"]
        NLScreener["web_nl_screener_service.py\nNL → SQL filters"]
        LLMCache["llm_cache_service.py\nCache LLM responses\nwith TTL"]
        AILog["ai_action_log_service.py\nLog every LLM call"]
    end

    subgraph "Local LLM (Ollama)"
        Ollama["Ollama Server\nlocalhost:11434"]
        Model["qwen3:14b model\n(or configured model)"]
    end

    subgraph "Data Science Services"
        FundSvc["fundamentals_service.py\nPE, ROE, market cap fetch"]
        NewsSvc["news_service.py\nSentiment scoring"]
        TrendsSvc["market_trends_service.py\nSector/period analytics"]
        PerfSvc["stock_performance_service.py\nReturn calculations"]
        SigOutSvc["signal_outcome_service.py\nML feedback tracking"]
    end

    subgraph "Statistics Engine"
        BackSvc["backtest_service.py\nSharpe, drawdown, WF testing"]
        BenchSvc["benchmark_service.py\nAlpha, beta, correlation"]
        AlgoFinder["algo_finding_service.py\nOptimize strategy params"]
    end

    subgraph "Data Sources"
        YF["yfinance\n(fundamentals)"]
        NewsAPI["MarketAux\nAlpha Vantage\n(news articles)"]
        PriceDB["stock_prices\n(PostgreSQL)"]
    end

    AIUI --> AIR --> ThinkTank --> LLMCache --> Ollama --> Model
    ScreenerUI --> ExploreR --> NLScreener --> ThinkTank
    TrendsUI --> TrendsR --> TrendsSvc & PerfSvc

    ThinkTank --> AILog
    FundSvc --> YF
    NewsSvc --> NewsAPI
    TrendsSvc & PerfSvc & SigOutSvc --> PriceDB
    BackSvc & BenchSvc --> PriceDB
    AlgoFinder --> BackSvc
```

---

## 4. Local LLM Integration (Ollama)

### What is Ollama?

Ollama is a local LLM server that runs open-source language models on your own machine. No data leaves your computer. This project uses it instead of OpenAI/Anthropic APIs to:
- Keep financial data private
- Avoid per-token API costs
- Work offline

```mermaid
graph LR
    subgraph "Paper Trading App"
        Code["Python code\n(httpx async client)"]
        Cache["llm_cache_service.py\nIn-memory + DB cache"]
    end
    
    subgraph "Local Machine"
        Ollama["Ollama Server\nlocalhost:11434"]
        Model["qwen3:14b\n(14 billion parameter LLM)"]
        GPU["GPU/CPU\nRuns inference locally"]
    end

    Code -->|"POST /api/generate\n{model, prompt}"| Ollama
    Ollama --> Model --> GPU
    GPU --> Model --> Ollama
    Ollama -->|"Streaming tokens"| Code
    Code --> Cache
    Cache -->|"Cache hit: skip LLM"| Code
```

### Configuration

Set in `backend/.env`:
```bash
AI_FEATURES_ENABLED=true
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_DEFAULT_MODEL=qwen3:14b
OLLAMA_TIMEOUT_SECONDS=60
```

### Ollama API Call Pattern

```python
# In web_ai_think_tank_helpers.py
import httpx

async def call_ollama(prompt: str, model: str = "qwen3:14b") -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{settings.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,          # False = wait for complete response
                "options": {
                    "temperature": 0.3,    # Lower = more deterministic
                    "top_p": 0.9,
                }
            }
        )
    return response.json()["response"]
```

### LLM Response Caching

LLM inference is slow (5-30 seconds). Identical prompts are cached:

```python
# llm_cache_service.py pattern
class LLMCacheService:
    def get_cached_response(self, cache_key: str) -> Optional[str]:
        # Check DB for recent response with same key
        row = db.query(AiActionLog).filter(
            AiActionLog.cache_key == cache_key,
            AiActionLog.created_at > (datetime.utcnow() - timedelta(hours=6))
        ).first()
        return row.response_text if row else None

    def cache_response(self, cache_key: str, response: str):
        db.add(AiActionLog(cache_key=cache_key, response_text=response))
        db.commit()
```

---

## 5. AI Think Tank Feature

The AI Think Tank is the main Gen AI feature — it lets users ask natural-language questions about stocks, portfolios, and market conditions.

### How It Works

```mermaid
sequenceDiagram
    participant User
    participant UI as /web/ai-think-tank
    participant Helper as web_ai_think_tank_helpers.py
    participant DB as PostgreSQL
    participant Cache as llm_cache_service
    participant Ollama as Ollama LLM

    User->>UI: "Analyze RELIANCE for me"
    UI->>Helper: analyze_stock(symbol="RELIANCE", user_question="Analyze RELIANCE for me")

    Helper->>DB: Fetch stock data
    DB-->>Helper: Stock fundamentals (PE=22, ROE=14%, sector=Energy)
    Helper->>DB: Fetch recent price history (last 6 months OHLCV)
    DB-->>Helper: 180 rows of price data
    Helper->>DB: Fetch latest news articles
    DB-->>Helper: 5 recent news headlines + sentiment scores

    Helper->>Helper: Build context-enriched prompt:
    Note over Helper: "You are a stock analyst. Here is data about RELIANCE:\n\nFundamentals: PE=22, ROE=14%, Market Cap=₹18L Cr\nRecent performance: +4.2% last month, +12% last 3m\n6-month price range: ₹2,280 - ₹2,890\nRecent news:\n- 'Reliance Q3 profits up 15%' (sentiment: +0.7)\n- 'RIL announces JioMart expansion' (sentiment: +0.5)\n\nUser question: Analyze RELIANCE for me\n\nProvide: 1) Summary 2) Key risks 3) Outlook"

    Helper->>Cache: Check cache for this prompt hash
    Cache-->>Helper: Cache miss

    Helper->>Ollama: POST /api/generate {prompt: ...}
    Ollama-->>Helper: "RELIANCE Industries appears fundamentally strong with...\nKey risks include crude oil price volatility...\nOutlook: Moderately bullish given..."

    Helper->>Cache: Save response (TTL: 6 hours)
    Helper->>DB: INSERT ai_action_logs (prompt, response, duration_ms)
    Helper-->>UI: Analysis text
    UI-->>User: Rendered analysis with formatting
```

### Prompt Engineering Strategy

The system uses **context injection** — feeding structured data into the LLM prompt:

```python
def build_stock_analysis_prompt(stock, prices_df, news_articles, user_question):
    # Compute summary stats from data
    price_change_1m = ((prices_df['close'].iloc[-1] / prices_df['close'].iloc[-22]) - 1) * 100
    price_52w_high = prices_df['close'].max()
    price_52w_low = prices_df['close'].min()

    news_summary = "\n".join([
        f"- '{a.headline}' (sentiment: {a.sentiment_score:+.1f})"
        for a in news_articles[:5]
    ])

    return f"""You are a professional Indian equity market analyst.

STOCK DATA FOR {stock.symbol} ({stock.company_name}):
Exchange: {stock.exchange}
Sector: {stock.sector}
Industry: {stock.industry}

FUNDAMENTALS:
- P/E Ratio: {stock.fundamentals.pe_ratio:.1f}x
- Return on Equity: {stock.fundamentals.roe_pct:.1f}%
- Debt/Equity: {stock.fundamentals.debt_to_equity:.2f}
- Market Cap: ₹{stock.fundamentals.market_cap/1e7:.0f} Cr

RECENT PRICE PERFORMANCE:
- Current Price: ₹{prices_df['close'].iloc[-1]:.2f}
- 1-Month Change: {price_change_1m:+.1f}%
- 52-Week High: ₹{price_52w_high:.2f}
- 52-Week Low: ₹{price_52w_low:.2f}

RECENT NEWS:
{news_summary}

USER QUESTION: {user_question}

Please provide:
1. Brief company overview (2-3 sentences)
2. Fundamental assessment
3. Technical/price trend observation
4. Key risks
5. Short-term outlook (1-3 months)

Be concise and use Indian market context."""
```

### Analysis Types Supported

| Analysis Type | What LLM Gets | Typical Output |
|--------------|---------------|----------------|
| Stock analysis | Fundamentals + prices + news | Buy/Sell reasoning, risks, outlook |
| Portfolio review | Holdings + PnL + sector exposure | Diversification advice, concentration risks |
| Market commentary | Top movers + index data + breadth | Market mood, sector rotation signals |
| Backtest explanation | Backtest metrics in plain language | "Why did this strategy perform well?" |
| Strategy suggestions | Risk profile + capital + goals | Suggested strategies to try |

---

## 6. Natural Language Stock Screener

Located in `web_nl_screener_service.py`, this feature converts plain English queries into database filters.

### How It Works

```mermaid
flowchart TD
    A["User types:\n'Show me oversold large-cap IT stocks\nwith PE below 20'"] --> B["web_nl_screener_service.py"]
    B --> C["Build extraction prompt for LLM"]
    C --> D["LLM extracts structured filters:\n{\n  sector: 'IT',\n  pe_max: 20,\n  rsi_max: 35,\n  index: 'nifty500',\n  size: 'large_cap'\n}"]
    D --> E["Map to SQL conditions:\n  stocks.sector = 'IT'\n  AND fundamentals.pe_ratio < 20\n  AND performance.rsi_14d < 35\n  AND stocks.is_nifty500 = TRUE\n  AND fundamentals.market_cap > 20000Cr"]
    E --> F["Execute query on PostgreSQL"]
    F --> G["Return matching stocks with\nall requested metrics"]
    G --> H["Display results table in UI"]
```

### Extractable Filter Dimensions

| Dimension | Natural Language Examples | DB Field |
|-----------|--------------------------|----------|
| Sector | "IT stocks", "banking", "pharma" | `stocks.sector` |
| Index | "nifty50", "large-cap" | `stocks.is_nifty50` |
| PE Ratio | "PE below 20", "undervalued" | `fundamentals.pe_ratio` |
| RSI | "oversold", "RSI below 35" | Computed from `stock_prices` |
| Return | "outperforming", "+10% last month" | `performance_snapshots` |
| Volume | "high volume", "liquid stocks" | `stock_prices.volume` |
| Market Cap | "large-cap", "> ₹20,000 Cr" | `fundamentals.market_cap` |

---

## 7. Sentiment Analysis Pipeline

### How News Sentiment Is Scored

`news_service.py` assigns sentiment scores to news articles as they are ingested.

```mermaid
flowchart TD
    A["Raw news article:\nheadline: 'Reliance Q3 profit surges 15%, beats estimates'\nsummary: 'RIL reports strong earnings driven by retail and digital segments'"]
    
    A --> B["Keyword-based pre-scoring\n(fast, no LLM needed)"]
    B --> C{Positive keywords found?}
    C -->|"surges, beats, strong, rises, growth, profit"| D["+score contribution"]
    C -->|"falls, miss, loss, decline, weak, crash"| E["-score contribution"]
    C -->|Neutral| F["0 score"]
    D & E & F --> G["Composite score\n-1.0 (very negative) to +1.0 (very positive)"]
    G --> H["Store in stock_news_articles.sentiment_score"]
    
    subgraph "Optional: LLM Sentiment"
        I["For high-importance stocks:\nSend to Ollama for deeper analysis\n'Rate the sentiment of this headline:\n0=very negative, 1=very positive'"]
        I --> J["LLM returns 0.73"]
    end
    
    H --> K["Aggregate per stock:\nAvg sentiment last 7 days\n= sentiment index for stock"]
```

### Sentiment Score Usage

```sql
-- Stocks with most positive recent news
SELECT 
  s.symbol,
  s.company_name,
  AVG(a.sentiment_score) AS avg_sentiment_7d,
  COUNT(a.id) AS article_count
FROM stocks s
JOIN stock_news_links l ON l.stock_id = s.id
JOIN stock_news_articles a ON a.id = l.article_id
WHERE a.published_at > CURRENT_DATE - INTERVAL '7 days'
GROUP BY s.symbol, s.company_name
HAVING COUNT(a.id) >= 3
ORDER BY avg_sentiment_7d DESC
LIMIT 20;
```

---

## 8. Stock Fundamentals & Scoring

`fundamentals_service.py` fetches fundamental data from yfinance and stores it for screening and analysis.

### What Fundamentals Are Fetched

```python
# yfinance.Ticker.info dictionary — key fields extracted
FUNDAMENTAL_FIELDS = {
    "pe_ratio":          "trailingPE",           # Price / Earnings
    "pb_ratio":          "priceToBook",           # Price / Book Value
    "roe_pct":           "returnOnEquity",        # Net Income / Equity × 100
    "debt_to_equity":    "debtToEquity",          # Total Debt / Equity
    "market_cap":        "marketCap",             # Total market value (₹)
    "dividend_yield":    "dividendYield",         # Annual dividend / price
    "revenue_growth":    "revenueGrowth",         # YoY revenue growth %
    "earnings_growth":   "earningsGrowth",        # YoY EPS growth %
    "current_ratio":     "currentRatio",          # Current assets / liabilities
    "profit_margins":    "profitMargins",         # Net profit margin %
    "52w_high":          "fiftyTwoWeekHigh",
    "52w_low":           "fiftyTwoWeekLow",
    "beta":              "beta",                  # Correlation with market
}
```

### Fundamental Refresh Flow

```mermaid
flowchart TD
    A["fundamentals_service.py\nrefresh_fundamentals(db, stocks)"] --> B["For each stock in batch"]
    B --> C["yf.Ticker(yahoo_symbol).info"]
    C --> D{Data returned?}
    D -->|Yes| E["Extract PE, ROE, market_cap..."]
    E --> F["UPSERT stock_fundamentals_latest\n(stock_id, pe_ratio, roe_pct,...)\nON CONFLICT update"]
    D -->|No data / error| G["UPDATE fetch_status='failed'\nlog error"]
    F --> H["fetch_status='success'\nfetched_at=NOW()"]
    H --> B
```

### Using Fundamentals for Value Screening

```python
# Example: screen for value stocks (low PE, high ROE)
def get_value_stocks(db: Session, pe_max: float = 20, roe_min: float = 15):
    return db.query(Stock).join(StockFundamentalsLatest).filter(
        StockFundamentalsLatest.pe_ratio < pe_max,
        StockFundamentalsLatest.pe_ratio > 0,   # exclude negative PE
        StockFundamentalsLatest.roe_pct > roe_min,
        StockFundamentalsLatest.debt_to_equity < 1.0,
        Stock.is_delisted == False,
        Stock.is_nifty500 == True,              # only investable universe
    ).order_by(StockFundamentalsLatest.roe_pct.desc()).limit(50).all()
```

---

## 9. Market Analytics & Trend Analysis

### Market Trends Service

`market_trends_service.py` computes period-over-period returns and sector trends:

```mermaid
graph TD
    subgraph "Inputs"
        Prices["stock_prices table\n~5000 stocks × 252 days/year"]
        Perf["stock_performance_snapshots\n(pre-computed 1m/3m/6m/1y)"]
        Funds["index_fund_prices\n(NIFTY, Gold, Silver)"]
    end

    subgraph "Computed Metrics"
        SectorPerf["Sector Performance\nAverage return per sector\nfor 1m/3m/6m/1y periods"]
        Breadth["Market Breadth\nAdvancers vs Decliners count\n52-week highs/lows"]
        Movers["Top Gainers/Losers\nToday's % change sorted"]
        Momentum["Momentum Scores\n12-1 month momentum\n(avoid last month reversal)"]
    end

    Prices --> SectorPerf & Breadth & Movers
    Perf --> Momentum & SectorPerf
    Funds --> SectorPerf

    subgraph "Outputs / UI"
        SectorHeat["Sector heatmap\non /web/trends"]
        MoversList["Top movers widget\non /web/explore"]
        MarketWidget["Market overview\n(index levels + change)"]
    end

    SectorPerf --> SectorHeat
    Movers --> MoversList
    Breadth --> MarketWidget
```

### Momentum Calculation (12-1 Month)

```python
# Standard momentum: 12-month return minus last month
# (Excludes most recent month to avoid short-term reversal)
def compute_momentum_score(prices_df: pd.DataFrame) -> float:
    if len(prices_df) < 252:
        return 0.0
    
    price_now    = prices_df['close'].iloc[-1]
    price_1m_ago = prices_df['close'].iloc[-22]    # ~22 trading days/month
    price_12m_ago = prices_df['close'].iloc[-252]  # ~252 trading days/year

    # 12-month return excluding last month
    momentum = (price_1m_ago / price_12m_ago) - 1
    return momentum * 100  # as percentage
```

### Sector Rotation Heatmap Data

```sql
-- Compute sector performance for the heatmap
SELECT 
    s.sector,
    COUNT(s.id) AS stock_count,
    AVG(p.return_1m_pct) AS avg_1m_return,
    AVG(p.return_3m_pct) AS avg_3m_return,
    AVG(p.return_6m_pct) AS avg_6m_return,
    AVG(p.return_1y_pct) AS avg_1y_return
FROM stocks s
JOIN stock_performance_snapshots p ON p.stock_id = s.id
WHERE s.is_delisted = FALSE
  AND s.sector IS NOT NULL
  AND p.as_of_date = (SELECT MAX(as_of_date) FROM stock_performance_snapshots)
GROUP BY s.sector
ORDER BY avg_1m_return DESC;
```

---

## 10. Signal Outcome Tracking (ML Feedback Loop)

This is a lightweight ML feedback system. After generating a BUY/SELL signal, the system tracks whether the prediction was correct.

### What Gets Tracked

```
Signal generated on Day 0:
  Strategy: RSI Strategy
  Stock: RELIANCE
  Signal: BUY
  Confidence: 78%
  Price at signal: ₹2,375

Outcomes tracked automatically:
  Day +5:  Price = ₹2,421 → return = +1.94% → is_profitable_5d = True
  Day +10: Price = ₹2,395 → return = +0.84% → is_profitable_10d = True
  Day +20: Price = ₹2,490 → return = +4.84% → is_profitable_20d = True
```

### How Outcomes Are Computed

`signal_outcome_service.py` runs periodically:

```mermaid
flowchart TD
    A["signal_outcome_service.py\nrun_outcome_calculation(db)"] --> B["Find signals with\nno outcome yet\nAND signal_date <= today - 20 days"]
    B --> C["For each signal\n→ get stock_id, signal_date, signal_type"]
    C --> D["Lookup price 5 days after signal"]
    D --> E["Lookup price 10 days after signal"]
    E --> F["Lookup price 20 days after signal"]
    F --> G["Compute returns:\n  return_5d = (p5 / p0) - 1\n  return_10d = (p10 / p0) - 1\n  return_20d = (p20 / p0) - 1"]
    G --> H{Signal was BUY?}
    H -->|Yes| I["is_profitable = return > 0\n(positive price = correct)"]
    H -->|No SELL?| J["is_profitable = return < 0\n(price fell = correct)"]
    I & J --> K["INSERT strategy_signal_outcomes"]
    K --> C
```

### Using Outcomes to Evaluate Strategy Quality

```python
# Analyze: Does RSI Strategy's BUY signals actually predict price increases?
def evaluate_signal_accuracy(db: Session, strategy_id: int) -> dict:
    outcomes = db.query(StrategySignalOutcome)\
        .join(StrategySignal)\
        .filter(StrategySignal.strategy_id == strategy_id)\
        .all()

    buy_signals = [o for o in outcomes if o.signal.signal_type == "BUY"]
    
    accuracy_5d = sum(1 for o in buy_signals if o.is_profitable_5d) / len(buy_signals)
    accuracy_20d = sum(1 for o in buy_signals if o.is_profitable_20d) / len(buy_signals)
    avg_return_20d = sum(o.return_20d_pct for o in buy_signals) / len(buy_signals)

    return {
        "total_buy_signals": len(buy_signals),
        "accuracy_5d": accuracy_5d,      # e.g., 0.61 = 61% accurate at 5 days
        "accuracy_20d": accuracy_20d,    # e.g., 0.58 = 58% accurate at 20 days
        "avg_return_20d": avg_return_20d # e.g., +1.2% average 20-day return
    }
```

### Confidence Score Calibration

High-confidence signals (confidence > 80) should have higher accuracy than low-confidence signals (50-60). This relationship can be plotted to verify calibration:

```
Expected calibration (well-calibrated strategy):
  Confidence 50-60% → Accuracy ~55%
  Confidence 60-70% → Accuracy ~65%
  Confidence 70-80% → Accuracy ~72%
  Confidence 80-90% → Accuracy ~82%
  Confidence 90-100%→ Accuracy ~88%

If the actual accuracy doesn't increase with confidence → miscalibrated strategy
```

---

## 11. Performance Analytics & Statistics

### Sharpe Ratio Calculation

```python
# From backtest_service.py — how Sharpe ratio is computed
def calculate_sharpe_ratio(daily_returns: pd.Series, risk_free_rate_annual: float = 0.065) -> float:
    """
    daily_returns: Series of daily % returns (e.g., [0.012, -0.008, 0.015, ...])
    risk_free_rate_annual: Indian 10-year G-Sec yield (~6.5%)
    """
    daily_rf = risk_free_rate_annual / 252  # Convert annual to daily

    excess_returns = daily_returns - daily_rf
    
    if excess_returns.std() == 0:
        return 0.0
    
    sharpe = (excess_returns.mean() / excess_returns.std()) * np.sqrt(252)  # Annualize
    return round(sharpe, 4)
```

### Maximum Drawdown

```python
def calculate_max_drawdown(portfolio_values: pd.Series) -> float:
    """
    portfolio_values: Series of portfolio value over time
    Returns: Max drawdown as negative percentage (e.g., -0.083 = -8.3%)
    """
    rolling_max = portfolio_values.expanding().max()
    drawdown = (portfolio_values / rolling_max) - 1  # negative = below peak
    max_drawdown = drawdown.min()
    return round(max_drawdown * 100, 2)  # as percentage

# Visualization conceptually:
# Portfolio value over time:
# ₹1,000,000 → ₹1,120,000 (peak) → ₹1,027,000 (trough) → ₹1,180,000
#                                      ↑
#                               Max drawdown = (1,027,000 - 1,120,000) / 1,120,000
#                                            = -8.3%
```

### Alpha & Beta vs Benchmark

```python
# benchmark_service.py — compare strategy vs NIFTY50
def calculate_alpha_beta(strategy_returns: pd.Series, benchmark_returns: pd.Series) -> dict:
    """
    strategy_returns: daily % returns from backtest
    benchmark_returns: daily % returns of NIFTY50 in same period
    """
    # Beta = covariance(strategy, benchmark) / variance(benchmark)
    cov_matrix = np.cov(strategy_returns, benchmark_returns)
    beta = cov_matrix[0, 1] / cov_matrix[1, 1]

    # Alpha = strategy_return - (rf + beta × (benchmark_return - rf))
    annualized_strategy = strategy_returns.mean() * 252
    annualized_benchmark = benchmark_returns.mean() * 252
    rf = 0.065  # Indian 10Y G-Sec

    alpha = annualized_strategy - (rf + beta * (annualized_benchmark - rf))

    # Correlation
    correlation = np.corrcoef(strategy_returns, benchmark_returns)[0, 1]

    return {
        "alpha": round(alpha * 100, 2),         # annualized alpha %
        "beta": round(beta, 4),                  # market sensitivity
        "correlation": round(correlation, 4),    # -1 to +1
        "tracking_error": round(
            (strategy_returns - benchmark_returns).std() * np.sqrt(252) * 100, 2
        ),  # annualized tracking error %
    }
```

---

## 12. Advanced Strategies (ML-Style)

`advanced_strategies.py` contains more sophisticated strategy logic:

### VWAP Strategy

```
VWAP = Volume-Weighted Average Price
     = Σ(Price × Volume) / Σ(Volume)

Logic:
  If close < VWAP × (1 - threshold):  Stock is trading below its
      → BUY signal                     average cost → mean reversion
  If close > VWAP × (1 + threshold):  Stock is trading above its
      → SELL signal                    average cost → pullback expected
```

### Statistical Patterns

The advanced strategies use statistical measures:
- **Z-score normalization**: How many standard deviations from mean is current price?
- **Rolling regression**: Linear trend lines over N days
- **Autocorrelation**: Does yesterday's return predict today's?

```python
# Z-score of price (is stock overextended?)
def price_zscore(prices_df: pd.DataFrame, window: int = 20) -> float:
    rolling_mean = prices_df['close'].rolling(window).mean()
    rolling_std = prices_df['close'].rolling(window).std()
    zscore = (prices_df['close'] - rolling_mean) / rolling_std
    return zscore.iloc[-1]

# ZScore > 2:  Price is 2 std above mean → overbought
# ZScore < -2: Price is 2 std below mean → oversold
```

---

## 13. Database Schema for DS/AI

```mermaid
erDiagram
    AI_ACTION_LOGS {
        int id PK
        string action_type
        string prompt_hash
        string cache_key
        string model_used
        text prompt_text
        text response_text
        bool cache_hit
        bool ollama_connected
        int duration_ms
        int token_count
        datetime created_at
    }

    SEARCH_QUERY_LOGS {
        int id PK
        int user_id FK
        string query_text
        json filters_applied
        int result_count
        int duration_ms
        string session_id
        datetime created_at
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
        float current_ratio
        float profit_margins
        float beta
        float week_52_high
        float week_52_low
        json raw_json
        string fetch_status
        datetime fetched_at
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
        datetime created_at
    }

    STRATEGY_SIGNAL_OUTCOMES {
        int id PK
        int signal_id FK
        float price_at_signal
        float price_5d
        float price_10d
        float price_20d
        float return_5d_pct
        float return_10d_pct
        float return_20d_pct
        bool is_profitable_5d
        bool is_profitable_10d
        bool is_profitable_20d
        date outcome_date
    }

    STOCK_NEWS_ARTICLES {
        int id PK
        string headline
        string summary
        string url UK
        string source
        string provider
        float sentiment_score
        datetime published_at
        datetime created_at
    }

    MARKET_ANALYTICS_CACHE {
        int id PK
        string cache_key UK
        json data
        datetime expires_at
        datetime created_at
    }

    STOCK_FUNDAMENTALS_LATEST }o--|| STOCKS : "for stock"
    STRATEGY_SIGNALS ||--o| STRATEGY_SIGNAL_OUTCOMES : "tracked by"
    STOCK_NEWS_ARTICLES ||--o{ STOCK_NEWS_LINKS : "linked to stocks"
    AI_ACTION_LOGS }|--o{ USERS : "by user (optional)"
```

---

## 14. Notebooks

The `notebooks/` directory contains Jupyter notebooks for data exploration:

```
notebooks/
└── check_nse_csv_tables.ipynb   ← Explore NSE CSV data, verify index constituents
```

### Running Notebooks

```bash
# Install Jupyter
pip install jupyter pandas psycopg2-binary sqlalchemy plotly

# Start Jupyter
cd paper_trading_app
jupyter notebook

# Open: http://localhost:8888
```

### Example Notebook Analyses You Might Write

```python
# In a notebook: explore signal accuracy
import pandas as pd
from sqlalchemy import create_engine

engine = create_engine("postgresql://user:pass@localhost/trading_db")

# Load signal outcomes
df = pd.read_sql("""
    SELECT 
        ss.signal_type,
        ss.confidence_score,
        sso.return_20d_pct,
        sso.is_profitable_20d
    FROM strategy_signals ss
    JOIN strategy_signal_outcomes sso ON sso.signal_id = ss.id
    WHERE ss.strategy_id = 7
""", engine)

# Accuracy by confidence bucket
df['confidence_bucket'] = pd.cut(df['confidence_score'], bins=[50,60,70,80,90,100])
accuracy_by_confidence = df.groupby('confidence_bucket')['is_profitable_20d'].mean()
print(accuracy_by_confidence)

# Plot
import plotly.express as px
fig = px.bar(
    accuracy_by_confidence.reset_index(),
    x='confidence_bucket',
    y='is_profitable_20d',
    title='Signal Accuracy vs Confidence Score'
)
fig.show()
```

---

## 15. Common Tasks for Interns

### Task: Enable AI features

1. Install Ollama: `https://ollama.ai/download`
2. Pull the model: `ollama pull qwen3:14b`
3. Start Ollama: `ollama serve` (runs on port 11434)
4. Set in `backend/.env`:
   ```
   AI_FEATURES_ENABLED=true
   OLLAMA_BASE_URL=http://localhost:11434
   OLLAMA_DEFAULT_MODEL=qwen3:14b
   ```
5. Restart backend: `docker compose restart backend`
6. Visit: `http://localhost:8000/web/ai-think-tank`

### Task: Add a new LLM analysis type

1. Open `backend/app/services/web_ai_think_tank_helpers.py`
2. Add a new function `build_my_analysis_prompt(stock, ...) -> str`
3. Add a new async function `my_analysis(db, stock_id, user_input) -> str`:
   - Fetch needed data from DB
   - Build prompt
   - Call `call_ollama(prompt)`
   - Cache result
   - Log to `ai_action_logs`
4. Add a route in `backend/app/routers/ai.py`
5. Connect to UI in the relevant template/partial

### Task: Analyze signal accuracy with SQL

```sql
-- Overall signal accuracy by strategy
SELECT 
  us.name AS strategy_name,
  ss.signal_type,
  COUNT(*) AS total_signals,
  ROUND(AVG(CASE WHEN sso.is_profitable_20d THEN 1.0 ELSE 0.0 END) * 100, 1) AS accuracy_20d_pct,
  ROUND(AVG(sso.return_20d_pct), 2) AS avg_return_20d
FROM user_strategies us
JOIN strategy_signals ss ON ss.strategy_id = us.id
JOIN strategy_signal_outcomes sso ON sso.signal_id = ss.id
WHERE ss.signal_type IN ('BUY', 'SELL')
GROUP BY us.name, ss.signal_type
ORDER BY accuracy_20d_pct DESC;
```

### Task: Test the NL screener

```python
# Direct test in Python
import asyncio
from app.services.web_nl_screener_service import NLScreenerService

async def test():
    result = await NLScreenerService().screen(
        query="Show me oversold IT stocks with PE below 25 in NIFTY500",
        db=db_session
    )
    print(result)

asyncio.run(test())
```

### Task: Build a simple ML model on signal outcomes

```python
# In a notebook: train a simple classifier to predict signal success
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
import pandas as pd
from sqlalchemy import create_engine

engine = create_engine("postgresql://user:pass@localhost/trading_db")

# Load features
df = pd.read_sql("""
    SELECT 
        ss.confidence_score,
        (ss.indicators->>'rsi')::float AS rsi_at_signal,
        (ss.indicators->>'atr')::float AS atr_at_signal,
        sso.is_profitable_20d AS label
    FROM strategy_signals ss
    JOIN strategy_signal_outcomes sso ON sso.signal_id = ss.id
    WHERE ss.signal_type = 'BUY'
      AND ss.indicators->>'rsi' IS NOT NULL
""", engine).dropna()

X = df[['confidence_score', 'rsi_at_signal', 'atr_at_signal']]
y = df['label'].astype(int)

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)

print(classification_report(y_test, model.predict(X_test)))
print("Feature importances:", dict(zip(X.columns, model.feature_importances_)))
```

---

## Quick Reference Card

```
Gen AI features:
  AI Think Tank:     /web/ai-think-tank → LLM stock analysis
  NL Screener:       /web/explore → "find undervalued IT stocks"
  LLM:               Ollama (local) → qwen3:14b model
  Cache:             llm_cache_service.py (6-hour TTL)
  Logs:              ai_action_logs table

Data science features:
  Sentiment:         news_service.py → score -1 to +1
  Fundamentals:      fundamentals_service.py → PE, ROE, market cap
  Trends:            market_trends_service.py → sector momentum
  Signal outcomes:   signal_outcome_service.py → ML feedback loop
  Statistics:        backtest_service.py → Sharpe, alpha, beta

Key files:
  services/web_ai_think_tank_helpers.py  ← LLM orchestration
  services/web_nl_screener_service.py    ← NL → DB query
  services/llm_cache_service.py          ← Response caching
  services/fundamentals_service.py       ← yfinance fundamentals
  services/news_service.py               ← Sentiment pipeline
  services/market_trends_service.py      ← Trend analytics
  services/signal_outcome_service.py     ← Signal ML feedback
  services/benchmark_service.py          ← Alpha/beta calculation
  services/backtest_service.py           ← Sharpe/drawdown stats
  strategies/advanced_strategies.py      ← VWAP, ML-style algos

Key tables:
  ai_action_logs               ← LLM call history + cache hits
  stock_fundamentals_latest    ← PE, ROE, market cap per stock
  stock_news_articles          ← News with sentiment_score
  strategy_signal_outcomes     ← Were BUY/SELL signals correct?
  market_analytics_cache       ← Pre-computed sector/trend data
  search_query_logs            ← NL screener query history

Enable AI:
  1. ollama pull qwen3:14b
  2. ollama serve
  3. Set AI_FEATURES_ENABLED=true in backend/.env
  4. docker compose restart backend
```
