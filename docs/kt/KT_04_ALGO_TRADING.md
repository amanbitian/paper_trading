# KT-04: Algo Trading Knowledge Transfer
### Paper Trading App — New Intern Onboarding Guide

---

## Table of Contents
1. [What is Algo Trading in This Project?](#1-what-is-algo-trading-in-this-project)
2. [Core Concepts You Must Know First](#2-core-concepts-you-must-know-first)
3. [System Architecture](#3-system-architecture)
4. [Strategy Framework](#4-strategy-framework)
5. [Implemented Strategies](#5-implemented-strategies)
6. [Signal Generation Flow](#6-signal-generation-flow)
7. [Backtesting Engine](#7-backtesting-engine)
8. [Paper Trading Engine](#8-paper-trading-engine)
9. [Risk Management](#9-risk-management)
10. [Cost & Charges Model](#10-cost--charges-model)
11. [Performance Metrics](#11-performance-metrics)
12. [Walk-Forward Testing](#12-walk-forward-testing)
13. [Database Schema for Algo](#13-database-schema-for-algo)
14. [Common Tasks for Interns](#14-common-tasks-for-interns)

---

## 1. What is Algo Trading in This Project?

This project is a **paper trading platform** — it simulates real stock trading without using real money. The algo/trading module has three main components:

| Component | What it Does | Files |
|-----------|-------------|-------|
| **Strategies** | Generate BUY/SELL/HOLD signals based on price patterns | `app/strategies/` |
| **Backtesting** | Replay historical prices through a strategy to see how it would have performed | `app/services/backtest_service.py` |
| **Paper Trading** | Execute simulated orders in real-time using current market prices | `app/services/paper_trading_service.py` |

> **No real money is involved.** Orders are placed in a simulated portfolio with virtual cash. This is how traders test strategies before going live.

---

## 2. Core Concepts You Must Know First

### What is a Trading Signal?
A signal is a recommendation to buy, sell, or hold a stock. Signals come from algorithms that analyze price patterns.

```
Signal: BUY RELIANCE
  Generated on: 2024-12-15
  Confidence:   78%
  Reason:       RSI crossed above 35 from oversold territory
  Current price: ₹2,375
  Suggested stop-loss: ₹2,280  (ATR-based)
  Suggested target:    ₹2,520  (2:1 risk-reward)
```

### What is OHLCV Data?
Every stock has 4 prices per trading day plus volume:

```
Date       | Open    | High    | Low     | Close   | Volume
-----------|---------|---------|---------|---------|----------
2024-12-15 | 2345.50 | 2389.00 | 2310.25 | 2375.80 | 12,450,000
           ↑                             ↑
           Price when              Price when
           market opened            market closed
```

### What is Backtesting?
Backtesting replays historical price data through a strategy to answer: "If I had used this strategy on RELIANCE from Jan 2023 to Dec 2024, what would my returns have been?"

```
Backtest inputs:
  Strategy:   RSI Strategy (period=14, oversold=35, overbought=65)
  Stock:      RELIANCE.NS
  Start date: 2023-01-01
  End date:   2024-12-31
  Capital:    ₹1,000,000

Backtest outputs:
  Total return:    +22.4%
  Sharpe ratio:    1.42       ← risk-adjusted return
  Max drawdown:    -8.3%      ← worst peak-to-trough loss
  Win rate:        58.3%      ← % of trades that were profitable
  Total trades:    24
  vs Benchmark:    NIFTY50 returned +18.1% → Alpha = +4.3%
```

### What is Slippage?
The difference between the signal price and the actual execution price. In real markets, large orders move the price.

```
Signal generated at close: ₹2,375
Next-day open price:       ₹2,382
Slippage (0.1%):           ₹2.38
Actual execution price:    ₹2,384.38
```

### What is ATR?
Average True Range — a measure of how volatile a stock is. Used for stop-loss placement:

```
If ATR = ₹45 (stock moves ±₹45 per day on average)
Stop-loss = Current price − 2 × ATR = 2375 − 90 = ₹2,285

This means: accept a ₹90 risk per share before cutting the loss
```

---

## 3. System Architecture

```mermaid
graph TB
    subgraph "User Interface"
        StratLab["Strategy Lab\n/web/strategy-lab"]
        BacktestUI["Backtesting\n/web/backtesting"]
        PaperUI["Paper Trading\n/web/paper-trading"]
    end

    subgraph "API Layer"
        StratAPI["/api/strategies/*"]
        BackAPI["/api/backtest/*"]
        PaperAPI["/api/paper-trading/*"]
    end

    subgraph "Strategy Engine"
        StratSvc["strategy_service.py\nGet strategy instance"]
        BaseStrat["strategies/base.py\nBaseStrategy interface"]
        RSI["rsi_strategy.py"]
        SMA["sma_crossover_strategy.py"]
        MACD["macd_strategy.py"]
        Break["breakout_strategy.py"]
        Sector["sector_rotation_strategy.py"]
        Adv["advanced_strategies.py"]
        Risk["risk_management.py\nStop-loss + position sizing"]
    end

    subgraph "Backtesting Engine"
        BackSvc["backtest_service.py\nBar-by-bar replay"]
        Metrics["Sharpe, drawdown,\nwin rate, alpha/beta"]
        WalkFwd["Walk-forward\ntesting (IS/OOS)"]
        BenchSvc["benchmark_service.py\nNIFTY50 comparison"]
    end

    subgraph "Paper Trading Engine"
        PaperSvc["paper_trading_service.py"]
        ExecSvc["execution_service.py\nSlippage simulation"]
        ChargesSvc["charges_service.py\nBrokerage fees, STT"]
        CostModel["cost_model_service.py\nZerodha model"]
    end

    subgraph "Data Layer"
        MktData["market_data_service.py\nPrice history → DataFrame"]
        DB[("PostgreSQL\nstock_prices\nuser_strategies\nstrategy_signals\nbacktest_runs\npaper_orders\npaper_trades")]
    end

    StratLab --> StratAPI --> StratSvc
    BacktestUI --> BackAPI --> BackSvc
    PaperUI --> PaperAPI --> PaperSvc

    StratSvc --> BaseStrat
    BaseStrat --> RSI & SMA & MACD & Break & Sector & Adv
    RSI & SMA & MACD --> Risk

    BackSvc --> StratSvc
    BackSvc --> MktData
    BackSvc --> ExecSvc & ChargesSvc
    BackSvc --> Metrics & WalkFwd & BenchSvc

    PaperSvc --> ExecSvc & ChargesSvc & CostModel
    PaperSvc --> MktData

    MktData --> DB
    BackSvc & PaperSvc & StratSvc --> DB
```

---

## 4. Strategy Framework

### BaseStrategy Interface

All strategies inherit from `BaseStrategy` in `app/strategies/base.py`:

```python
from dataclasses import dataclass
from typing import Optional
import pandas as pd

@dataclass
class SignalResult:
    signal_type: str             # "BUY", "SELL", or "HOLD"
    confidence_score: float      # 0.0 to 100.0
    reason: str                  # Human-readable explanation
    indicators: dict             # {"rsi": 32.4, "stop_loss": 2285.0, "target": 2520.0}

class BaseStrategy:
    name: str                    # e.g., "RSI Strategy"
    description: str
    default_parameters: dict     # e.g., {"period": 14, "oversold": 35}

    def generate_signal(
        self,
        prices: pd.DataFrame,    # OHLCV DataFrame, sorted ascending
        parameters: dict         # User's custom parameters (override defaults)
    ) -> SignalResult:
        raise NotImplementedError
```

### Strategy Registry

`strategy_service.py` maps strategy template names to their implementation classes:

```python
STRATEGY_REGISTRY = {
    "RSI Strategy":             RSIStrategy,
    "SMA Crossover":            SMACrossoverStrategy,
    "MACD Strategy":            MACDStrategy,
    "Breakout Strategy":        BreakoutStrategy,
    "Sector Rotation":          SectorRotationStrategy,
    "VWAP Strategy":            VWAPStrategy,
}

def get_strategy_instance(template_name: str) -> BaseStrategy:
    cls = STRATEGY_REGISTRY[template_name]
    return cls()
```

---

## 5. Implemented Strategies

### RSI Strategy (`rsi_strategy.py`)

**Concept**: RSI (Relative Strength Index) measures momentum. Below 30 = oversold (buy signal), above 70 = overbought (sell signal).

```mermaid
flowchart TD
    A[Get last N days\nof close prices] --> B[Calculate RSI\nperiod=14 days]
    B --> C{RSI value?}
    C -->|RSI < oversold threshold\ne.g. RSI < 35| D[BUY signal\nStock is oversold → bounce expected]
    C -->|RSI > overbought threshold\ne.g. RSI > 65| E[SELL signal\nStock is overbought → pullback expected]
    C -->|35 ≤ RSI ≤ 65| F[HOLD signal\nNo clear direction]
    D --> G[Apply risk management:\nStop-loss = price - 2×ATR\nTarget = price + 2×ATR]
    E --> G
    G --> H[Return SignalResult\nBUY/SELL/HOLD + indicators]
```

**RSI Formula**:
```
RSI = 100 - (100 / (1 + RS))
RS  = Average Gain over N days / Average Loss over N days

If RSI = 32: stock has been mostly falling → oversold → buy signal
If RSI = 71: stock has been mostly rising → overbought → sell signal
```

**Parameters**:
```json
{
  "period": 14,
  "oversold": 35,
  "overbought": 65,
  "atr_multiplier": 2.0
}
```

---

### SMA Crossover Strategy (`sma_crossover_strategy.py`)

**Concept**: When a fast moving average (20-day) crosses above a slow moving average (50-day), it signals upward momentum.

```mermaid
flowchart LR
    A[Close prices] --> B[SMA_fast\ne.g. 20-day average]
    A --> C[SMA_slow\ne.g. 50-day average]
    B & C --> D{Crossover?}
    D -->|"SMA_fast crosses ABOVE SMA_slow\n(Golden Cross)"| E["BUY signal\nUptrend starting"]
    D -->|"SMA_fast crosses BELOW SMA_slow\n(Death Cross)"| F["SELL signal\nDowntrend starting"]
    D -->|No crossover| G["HOLD signal"]
```

**Parameters**:
```json
{
  "fast_period": 20,
  "slow_period": 50
}
```

---

### MACD Strategy (`macd_strategy.py`)

**Concept**: MACD (Moving Average Convergence Divergence) uses two EMAs and a signal line to detect trend changes.

```
MACD Line    = EMA(12) - EMA(26)
Signal Line  = EMA(9) of MACD Line
Histogram    = MACD Line - Signal Line

BUY:  MACD crosses above Signal Line (histogram turns positive)
SELL: MACD crosses below Signal Line (histogram turns negative)
```

**Parameters**:
```json
{
  "fast_ema": 12,
  "slow_ema": 26,
  "signal_ema": 9
}
```

---

### Breakout Strategy (`breakout_strategy.py`)

**Concept**: When price breaks above a recent high (resistance), it signals a strong upward move.

```mermaid
flowchart TD
    A[Get last N bars] --> B[Calculate rolling\nhigh = max(close, lookback_period)]
    B --> C[Calculate rolling\nlow = min(close, lookback_period)]
    C --> D{Today's close vs. levels}
    D -->|"Close > rolling_high × (1 + breakout_threshold)"| E["BUY signal\nBreakout above resistance"]
    D -->|"Close < rolling_low × (1 - breakout_threshold)"| F["SELL signal\nBreakdown below support"]
    D -->|Inside range| G["HOLD signal"]
```

**Parameters**:
```json
{
  "lookback_period": 20,
  "breakout_threshold_pct": 1.5
}
```

---

### Sector Rotation Strategy (`sector_rotation_strategy.py`)

**Concept**: Rotate capital into the strongest-performing sector each month.

```mermaid
flowchart TD
    A[Get all stocks in universe] --> B[Group by sector]
    B --> C[Compute 1-month return\nfor each sector]
    C --> D[Rank sectors by return]
    D --> E{Is this stock\nin top sector?}
    E -->|Yes| F[BUY signal]
    E -->|No, in bottom sector| G[SELL signal]
    E -->|Middle sectors| H[HOLD signal]
```

---

## 6. Signal Generation Flow

### How Signals Are Created

```mermaid
sequenceDiagram
    participant User
    participant API as /api/strategies/{id}/generate-signals
    participant StratSvc as strategy_service
    participant MktData as market_data_service
    participant Strategy as RSIStrategy.generate_signal()
    participant RiskMgmt as risk_management.py
    participant DB as PostgreSQL

    User->>API: POST /api/strategies/7/generate-signals
    API->>StratSvc: generate_signals(strategy_id=7, db)
    StratSvc->>DB: SELECT user_strategy WHERE id=7
    DB-->>StratSvc: UserStrategy (template="RSI", params={period:14,...})
    StratSvc->>DB: SELECT stocks WHERE is_nifty50=True (or user's universe)
    DB-->>StratSvc: 50 stock IDs

    loop For each stock
        StratSvc->>MktData: get_prices_dataframe(stock_id, days=200)
        MktData->>DB: SELECT * FROM stock_prices WHERE stock_id=... ORDER BY date
        DB-->>MktData: DataFrame of OHLCV
        MktData-->>StratSvc: prices DataFrame

        StratSvc->>Strategy: generate_signal(prices, parameters)
        Strategy->>Strategy: Calculate RSI, check thresholds
        Strategy-->>StratSvc: SignalResult(BUY, 78%, "RSI=32")

        StratSvc->>RiskMgmt: enrich_with_risk(signal, prices, parameters)
        RiskMgmt->>RiskMgmt: Calculate ATR, stop_loss, target
        RiskMgmt-->>StratSvc: signal.indicators += {stop_loss:2285, target:2520, atr:45}

        StratSvc->>DB: INSERT strategy_signals\n(strategy_id, stock_id, date, BUY, 78%)
    end

    StratSvc-->>API: List[SignalResult]
    API-->>User: 200 [{symbol:"RELIANCE", signal:"BUY", confidence:78}, ...]
```

### Signal Outcome Tracking

After a signal is generated, the system tracks whether it was profitable:

```mermaid
flowchart LR
    A["signal_date = 2024-12-15\nBUY RELIANCE at ₹2,375"] --> B["5 days later: 2024-12-20\nprice = ₹2,421\nreturn_5d = +1.94%"]
    A --> C["10 days later: 2024-12-25\nprice = ₹2,395\nreturn_10d = +0.84%"]
    A --> D["20 days later: 2025-01-04\nprice = ₹2,490\nreturn_20d = +4.84%"]
    B & C & D --> E["strategy_signal_outcomes table\nreturn_5d=+1.94%, return_20d=+4.84%\nis_profitable_20d=True"]
    E --> F["Used to evaluate\nstrategy effectiveness over time"]
```

---

## 7. Backtesting Engine

`backtest_service.py` implements a bar-by-bar replay engine.

### The Core Principle: No Lookahead Bias

**Wrong** (lookahead bias — cheating):
```
Day 5: I know the price on Day 10 will be high → BUY on Day 5
```

**Correct** (what backtest_service.py does):
```
Day 5: I only know prices 1..5 → compute signal → maybe BUY
Day 6: Execute the buy at Day 6's open price
Day 10: Decide to sell based on prices 1..10
```

### Backtest Engine Flow

```mermaid
flowchart TD
    A["Start BacktestRun\nStrategy: RSI | Stock: RELIANCE\nPeriod: 2023-01-01 → 2024-12-31\nCapital: ₹1,000,000"] --> B["Load all OHLCV bars\nfor the period (sorted asc)"]
    B --> C["Initialize state:\ncash = ₹1,000,000\nposition = 0 shares\ntrades = []"]
    C --> D{Loop: next bar}
    D -->|End of data| Z["Compute final metrics"]
    D -->|More bars| E["Slice: bars[0..current_index-1]\nPast bars only → no lookahead"]
    E --> F["strategy.generate_signal(past_bars, params)"]
    F --> G{Signal type?}
    G -->|"BUY\nand no position"| H["Calculate position size\n(risk_service: ATR-based)"]
    H --> I["Apply slippage\n(execution_service)"]
    I --> J["Calculate charges\n(charges_service: Zerodha model)"]
    J --> K["Record BacktestTrade:\n  entry_price, qty, charges, slippage\nUpdate state:\n  cash -= cost\n  position = qty"]
    G -->|"SELL\nand have position"| L["Calculate exit price\nApply slippage + charges"]
    L --> M["Record BacktestTrade:\n  exit_price, pnl, charges\nUpdate state:\n  cash += proceeds\n  position = 0"]
    G -->|HOLD| N["Do nothing, advance bar"]
    K & M & N --> D
    Z --> AA["Calculate metrics:\n  Total return\n  Sharpe ratio\n  Max drawdown\n  Win rate\n  Alpha vs NIFTY50"]
    AA --> BB["INSERT backtest_runs with all metrics"]
```

### Execution Modes

The engine supports different timing assumptions:

| Mode | Signal Generated | Trade Executed | Notes |
|------|-----------------|---------------|-------|
| **Default** | At close of Day N | At open of Day N+1 | Most realistic |
| Intraday Conservative | At open of Day N | At close of Day N | Conservative assumption |
| Intraday Optimistic | At open | At high/low | Optimistic assumption |

---

## 8. Paper Trading Engine

Paper trading simulates real-time trading with live prices (from the database).

### Order Types

| Order Type | Execution | Use Case |
|-----------|-----------|---------|
| **MARKET** | Execute immediately at latest price | "Buy now at any price" |
| **LIMIT** | Execute only if price reaches limit | "Buy RELIANCE only if it drops to ₹2,350" |
| **STOP_LOSS** | Sell if price drops to stop level | "Protect a position; sell if RELIANCE falls to ₹2,280" |
| **STOP_LIMIT** | Limit order triggered by stop price | More complex stop-loss |

### Order States

```mermaid
stateDiagram-v2
    [*] --> PENDING : User places order

    PENDING --> FILLED : Market order (immediate execution)
    PENDING --> FILLED : Limit order: price reached the limit
    PENDING --> CANCELLED : User cancels
    PENDING --> EXPIRED : expires_at timestamp reached

    FILLED --> [*]
    CANCELLED --> [*]
    EXPIRED --> [*]
```

### Market Order Flow

```mermaid
sequenceDiagram
    participant User
    participant API as PaperTradingRouter
    participant PaperSvc as paper_trading_service
    participant MktData as market_data_service
    participant ChargesSvc as charges_service
    participant ExecSvc as execution_service
    participant DB

    User->>API: POST /api/paper-trading/orders\n{symbol:"RELIANCE", side:"BUY", qty:10, order_type:"MARKET"}
    API->>PaperSvc: place_order(...)

    PaperSvc->>DB: SELECT portfolio WHERE id=... AND user_id=... (ownership check)
    PaperSvc->>MktData: get_latest_price(stock_id)
    MktData->>DB: SELECT close FROM stock_prices WHERE stock_id=... ORDER BY date DESC LIMIT 1
    DB-->>MktData: close = ₹2,375.80
    MktData-->>PaperSvc: latest_price = ₹2,375.80

    PaperSvc->>ExecSvc: apply_slippage(price=2375.80, side="BUY", slippage_bps=10)
    ExecSvc-->>PaperSvc: exec_price = ₹2,378.18  (10 bps = 0.1%)

    PaperSvc->>ChargesSvc: calculate_charges(price=2378.18, qty=10, side="BUY", segment="equity_delivery")
    ChargesSvc-->>PaperSvc: charges = ₹23.78 (brokerage + STT + exchange fees)

    PaperSvc->>PaperSvc: total_cost = 10 × ₹2,378.18 + ₹23.78 = ₹23,805.58
    PaperSvc->>DB: Check portfolio.cash_balance >= ₹23,805.58
    DB-->>PaperSvc: cash_balance = ₹500,000 ✓

    PaperSvc->>DB: INSERT paper_orders (status='FILLED')
    PaperSvc->>DB: INSERT paper_trades (exec_price=2378.18, charges=23.78, slippage=2.38)
    PaperSvc->>DB: UPSERT portfolio_holdings (qty+=10, avg_price recalculated)
    PaperSvc->>DB: UPDATE portfolios SET cash_balance -= 23805.58

    PaperSvc-->>API: OrderResult(order_id=..., status='FILLED', exec_price=2378.18)
    API-->>User: 200 Order filled at ₹2,378.18
```

### Limit Order Matching

Limit orders are checked during the next price sync:

```python
# Simplified: runs during market data sync (jobs/)
def match_pending_limit_orders(db: Session):
    pending_orders = db.query(PaperOrder).filter(
        PaperOrder.status == "PENDING",
        PaperOrder.order_type == "LIMIT"
    ).all()

    for order in pending_orders:
        latest_price = get_latest_price(db, order.stock_id)
        
        if order.side == "BUY" and latest_price <= order.limit_price:
            execute_order(db, order, latest_price)  # Execute!
        elif order.side == "SELL" and latest_price >= order.limit_price:
            execute_order(db, order, latest_price)  # Execute!
```

---

## 9. Risk Management

`risk_management.py` adds stop-loss and position sizing to every signal.

### ATR-Based Stop-Loss

```
ATR (Average True Range) = average daily price range over N days

True Range for each day = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
ATR = rolling mean of True Range

Stop-loss = Entry Price - (ATR_multiplier × ATR)

Example:
  Entry = ₹2,375
  ATR   = ₹45
  Multiplier = 2.0
  Stop  = ₹2,375 - (2.0 × 45) = ₹2,285
```

### Position Sizing by Risk Profile

The system adjusts position size based on user's risk profile:

```
Risk per trade = (Portfolio value × risk_pct_per_trade)

Example:
  Portfolio value = ₹500,000
  Risk profile    = "moderate" → risk 1% per trade
  Risk per trade  = ₹5,000
  Stop distance   = ₹2,375 - ₹2,285 = ₹90 per share
  Max shares      = ₹5,000 / ₹90 = 55 shares

Conservative:  0.5% risk per trade
Moderate:      1.0% risk per trade
Aggressive:    2.0% risk per trade
```

```mermaid
graph LR
    A["Portfolio Value\n₹500,000"] --> B["Risk % from\nuser risk_profile\n(0.5% / 1% / 2%)"]
    B --> C["Risk per trade\n₹5,000"]
    C --> D["Stop distance\n(price - stop_loss)\n₹90 per share"]
    D --> E["Max position size\n= Risk ÷ Stop distance\n= 55 shares"]
    E --> F["Cost check:\n55 × ₹2,375 = ₹130,625\nWithin cash balance?"]
    F -->|Yes| G["Order: BUY 55 shares"]
    F -->|No, not enough cash| H["Reduce qty to fit\ncash balance"]
```

---

## 10. Cost & Charges Model

`charges_service.py` and `cost_model_service.py` implement the Zerodha brokerage fee model (standard Indian broker fees).

### Charges Breakdown

```
For a ₹2,375 × 10 shares = ₹23,750 BUY (equity delivery):

Brokerage:              ₹0           (Zerodha: free for delivery)
STT (Securities Transaction Tax):   ₹23.75  (0.1% of turnover)
Exchange Transaction Charge:         ₹1.78   (NSE: 0.00345%)
SEBI Charges:                        ₹0.17   (₹10 per crore)
GST (18% on brokerage+charges):      ₹0.35
Stamp Duty:                          ₹11.88  (0.015% on buy side)
                                    ------
Total Charges:                       ₹37.93

Net cost = ₹23,750 + ₹37.93 = ₹23,787.93
```

The charges are stored in a JSONB column `charges_breakdown` in `paper_trades` and `backtest_trades`:

```json
{
  "brokerage": 0.0,
  "stt": 23.75,
  "exchange_fee": 1.78,
  "sebi_charges": 0.17,
  "gst": 0.35,
  "stamp_duty": 11.88,
  "total": 37.93
}
```

---

## 11. Performance Metrics

### Key Metrics Computed by backtest_service.py

```mermaid
graph TD
    BT["Backtest trades list:\n  trade 1: +4.2%\n  trade 2: -1.8%\n  trade 3: +6.1%\n  ..."]
    
    BT --> TR["Total Return\n= (final_value - initial_capital) / initial_capital × 100\n= +22.4%"]
    
    BT --> WR["Win Rate\n= profitable_trades / total_trades × 100\n= 14/24 = 58.3%"]
    
    BT --> PF["Profit Factor\n= gross_profit / gross_loss\n= 1.8  (above 1 = overall profitable)"]
    
    BT --> SR["Sharpe Ratio\n= (mean daily return - risk_free_rate) / std(daily returns)\n= 1.42  (above 1 = good)"]
    
    BT --> MD["Max Drawdown\n= worst peak-to-trough % loss\n= -8.3%  (lower is better)"]
    
    BT --> BENCH["vs Benchmark (NIFTY50)\n  Alpha = strategy return - benchmark return\n  Beta  = correlation with market\n  Info Ratio = Alpha / Tracking Error"]
    
    BT --> WF["Walk-Forward\n  In-Sample Sharpe:    1.42\n  Out-of-Sample Sharpe: 1.19\n  Overfitting Score:   0.84  (closer to 1 = not overfit)"]
```

### Sharpe Ratio Interpretation

```
Sharpe < 0:   Strategy loses money vs risk-free rate (very bad)
0 ≤ Sharpe < 1: Some return but not great risk-adjusted
1 ≤ Sharpe < 2: Good strategy
2 ≤ Sharpe < 3: Very good strategy
Sharpe ≥ 3:   Exceptional (rare in practice)
```

---

## 12. Walk-Forward Testing

Walk-forward testing prevents overfitting — where a strategy looks great on historical data but fails in live trading.

### How It Works

```mermaid
gantt
    title Walk-Forward Testing on 2 Years of Data
    dateFormat  YYYY-MM
    section In-Sample (Train)
    Optimize params   :done, 2023-01, 12M
    section Out-of-Sample (Test)
    Validate on unseen :active, 2024-01, 6M
    section Rolling Window 2
    Optimize params 2   :done, 2023-07, 12M
    section Out-of-Sample 2
    Validate 2          :active, 2024-07, 6M
```

```
in_sample_period:   12 months (optimize parameters here)
out_of_sample_period: 6 months (test WITHOUT changing parameters)

Step 1: Fit strategy parameters on Jan 2023 - Dec 2023 (in-sample)
Step 2: Test those SAME parameters on Jan 2024 - Jun 2024 (out-of-sample)
Step 3: Roll forward: Fit Jul 2023 - Jun 2024, Test Jul 2024 - Dec 2024

Overfitting score = OOS Sharpe / IS Sharpe
  Score > 0.7 → Not overfit (good)
  Score < 0.5 → Possibly overfit (bad, strategy may not work live)
```

### Walk-Forward Database Fields

```sql
-- backtest_runs table has both in-sample and out-of-sample results
SELECT 
  is_sharpe_ratio,          -- In-sample Sharpe (looks good but may be overfit)
  oos_sharpe_ratio,         -- Out-of-sample Sharpe (true measure of robustness)
  overfitting_score,        -- oos/is ratio (want > 0.7)
  walk_forward_windows,     -- How many rolling windows were tested
  in_sample_months,
  out_of_sample_months
FROM backtest_runs WHERE id = 42;
```

---

## 13. Database Schema for Algo

```mermaid
erDiagram
    STRATEGY_TEMPLATES {
        int id PK
        string name UK
        string description
        string strategy_class
        json default_parameters
        bool is_active
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
        string universe
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
        datetime created_at
    }

    STRATEGY_SIGNAL_OUTCOMES {
        int id PK
        int signal_id FK
        float return_5d_pct
        float return_10d_pct
        float return_20d_pct
        bool is_profitable_5d
        bool is_profitable_20d
        date outcome_date
    }

    BACKTEST_RUNS {
        int id PK
        int strategy_id FK
        int stock_id FK
        date start_date
        date end_date
        float initial_capital
        float final_value
        float total_return_pct
        float sharpe_ratio
        float max_drawdown_pct
        float win_rate
        float profit_factor
        int total_trades
        float gross_pnl
        float total_charges
        float slippage_cost
        float net_pnl
        float alpha
        float beta
        float tracking_error
        float information_ratio
        string benchmark_code
        float is_sharpe_ratio
        float oos_sharpe_ratio
        float overfitting_score
        int walk_forward_windows
        datetime created_at
    }

    BACKTEST_TRADES {
        int id PK
        int backtest_run_id FK
        int stock_id FK
        string side
        date entry_date
        date exit_date
        float entry_price
        float exit_price
        int quantity
        float gross_pnl
        float charges
        float slippage
        float net_pnl
        float return_pct
        string exit_reason
        json charges_breakdown
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
        float quantity
        float brokerage
        float stt
        float exchange_fee
        float gst
        float stamp_duty
        float total_charges
        float slippage
        json charges_breakdown
        datetime executed_at
    }

    STRATEGY_TEMPLATES ||--o{ USER_STRATEGIES : "instantiated as"
    USER_STRATEGIES ||--o{ STRATEGY_SIGNALS : "generates"
    USER_STRATEGIES ||--o{ BACKTEST_RUNS : "tested by"
    STRATEGY_SIGNALS ||--o| STRATEGY_SIGNAL_OUTCOMES : "tracked by"
    BACKTEST_RUNS ||--o{ BACKTEST_TRADES : "contains"
    PAPER_ORDERS ||--o{ PAPER_TRADES : "executes"
```

---

## 14. Common Tasks for Interns

### Task: Add a new strategy

1. Create `app/strategies/my_new_strategy.py`:
```python
from app.strategies.base import BaseStrategy, SignalResult
import pandas as pd

class MyNewStrategy(BaseStrategy):
    name = "My New Strategy"
    description = "Strategy description"
    default_parameters = {"period": 14, "threshold": 0.5}

    def generate_signal(self, prices: pd.DataFrame, parameters: dict) -> SignalResult:
        period = parameters.get("period", self.default_parameters["period"])
        # ... your signal logic here ...
        return SignalResult(
            signal_type="BUY",
            confidence_score=75.0,
            reason="My condition met",
            indicators={"my_indicator": 42.0}
        )
```

2. Register in `strategy_service.py`:
```python
STRATEGY_REGISTRY["My New Strategy"] = MyNewStrategy
```

3. Add a seed template in `scripts/seed_strategy_templates.py`

### Task: Debug a backtest giving zero trades

```python
# Check: does the stock have price data in the backtest period?
SELECT COUNT(*), MIN(price_datetime), MAX(price_datetime)
FROM stock_prices
WHERE stock_id = (SELECT id FROM stocks WHERE symbol = 'XYZ')
  AND price_datetime BETWEEN '2023-01-01' AND '2024-12-31';

# Check: what signals did the strategy generate?
SELECT * FROM strategy_signals
WHERE strategy_id = 7
  AND signal_date BETWEEN '2023-01-01' AND '2024-12-31'
ORDER BY signal_date;
```

### Task: Add a new performance metric

1. In `backtest_service.py`, find `calculate_metrics()` function
2. Add your metric calculation after the existing ones
3. Add the column to `BacktestRun` model in `models/backtest.py`
4. Create an Alembic migration to add the column to the DB
5. Add it to the Pydantic response schema in `schemas/backtest.py`

---

## Quick Reference Card

```
Strategy flow:
  User configures strategy parameters in Strategy Lab
  → strategy_service.get_strategy_instance(template_name)
  → BaseStrategy.generate_signal(prices_df, parameters)
  → SignalResult (BUY/SELL/HOLD + confidence + indicators)
  → risk_management.enrich_with_risk(signal, prices)
  → Saved to strategy_signals table

Backtest flow:
  User picks strategy + stock + date range + capital
  → backtest_service.run_backtest()
  → Bar-by-bar replay (no lookahead!)
  → Each bar: generate signal → execute trade → track PnL
  → compute_metrics() → Sharpe, drawdown, win rate, alpha
  → Saved to backtest_runs + backtest_trades tables

Paper trading flow:
  User places MARKET/LIMIT/STOP order
  → market_data_service.get_latest_price()
  → execution_service.apply_slippage()
  → charges_service.calculate_charges() (Zerodha model)
  → Saved to paper_orders + paper_trades tables
  → portfolio_holdings + cash_balance updated

Key strategies:
  RSI:            rsi_strategy.py         → mean reversion
  SMA Crossover:  sma_crossover_strategy  → trend following
  MACD:           macd_strategy.py        → momentum
  Breakout:       breakout_strategy.py    → support/resistance
  Sector Rotation: sector_rotation_strategy → relative strength
  VWAP:           advanced_strategies.py → intraday mean reversion
```
