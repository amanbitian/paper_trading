from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, joinedload

from app.models.index_fund import IndexFund, IndexFundPrice
from app.models.portfolio import PortfolioDailySnapshot, PortfolioHolding
from app.models.stock import StockPrice
from app.services.market_data_service import DAILY_TIMEFRAME, prices_to_dataframe
from app.services.market_data_service import get_latest_prices_map
from app.services.portfolio_service import D, calculate_portfolio_value


def _nifty_returns(db: Session, lookback_days: int) -> pd.Series:
    fund = db.scalar(select(IndexFund).where(IndexFund.yahoo_symbol == "^NSEI"))
    if fund is None:
        return pd.Series(dtype=float)
    prices = list(
        db.scalars(
            select(IndexFundPrice)
            .where(IndexFundPrice.index_fund_id == fund.id, IndexFundPrice.timeframe == DAILY_TIMEFRAME)
            .order_by(desc(IndexFundPrice.price_datetime))
            .limit(lookback_days + 5)
        )
    )
    prices.reverse()
    if len(prices) < 30:
        return pd.Series(dtype=float)
    frame = prices_to_dataframe(prices)
    return frame["close"].astype(float).pct_change().dropna()


def compute_portfolio_beta(db: Session, portfolio_id: int, lookback_days: int = 252) -> dict:
    holdings = list(
        db.scalars(
            select(PortfolioHolding)
            .where(PortfolioHolding.portfolio_id == portfolio_id)
            .options(joinedload(PortfolioHolding.stock))
        )
    )
    nifty_returns = _nifty_returns(db, lookback_days)
    if nifty_returns.empty or not holdings:
        return {
            "portfolio_beta": None,
            "stock_betas": {},
            "benchmark": "NIFTY 50",
            "lookback_days": lookback_days,
            "interpretation": "Insufficient data for beta",
            "status": "insufficient_data",
        }

    nifty_var = float(nifty_returns.var())
    if nifty_var == 0:
        nifty_var = 1e-12

    values = calculate_portfolio_value(db, portfolio_id)
    total_mv = D(values.get("market_value", 0))
    stock_betas: dict[str, float] = {}
    weighted_beta = 0.0
    weight_sum = 0.0

    for holding in holdings:
        prices = list(
            db.scalars(
                select(StockPrice)
                .where(
                    StockPrice.stock_id == holding.stock_id,
                    StockPrice.timeframe == DAILY_TIMEFRAME,
                )
                .order_by(desc(StockPrice.price_datetime))
                .limit(lookback_days + 5)
            )
        )
        prices.reverse()
        if len(prices) < 30:
            continue
        frame = prices_to_dataframe(prices)
        stock_returns = frame["close"].astype(float).pct_change().dropna()
        aligned = pd.concat([stock_returns, nifty_returns], axis=1, join="inner").dropna()
        if len(aligned) < 20:
            continue
        beta_i = float(aligned.iloc[:, 0].cov(aligned.iloc[:, 1]) / nifty_var)
        symbol = holding.stock.symbol if holding.stock else str(holding.stock_id)
        stock_betas[symbol] = round(beta_i, 4)
        latest = D(holding.quantity) * D(holding.average_buy_price)
        price_map = get_latest_prices_map(db, [holding.stock_id])
        mv = D(holding.quantity) * D(price_map.get(holding.stock_id, holding.average_buy_price))
        if total_mv > 0:
            w = float(mv / total_mv)
            weighted_beta += beta_i * w
            weight_sum += w

    port_beta = round(weighted_beta / weight_sum, 4) if weight_sum else None
    interpretation = "Insufficient data for beta"
    if port_beta is not None:
        if port_beta > 1.05:
            interpretation = f"Beta {port_beta}: portfolio moves more than NIFTY on average"
        elif port_beta < 0.95:
            interpretation = f"Beta {port_beta}: portfolio moves less than NIFTY on average"
        else:
            interpretation = f"Beta {port_beta}: roughly market-neutral vs NIFTY"

    return {
        "portfolio_beta": port_beta,
        "stock_betas": stock_betas,
        "benchmark": "NIFTY 50",
        "lookback_days": lookback_days,
        "interpretation": interpretation,
        "status": "ok" if port_beta is not None else "insufficient_data",
    }


def compute_var(db: Session, portfolio_id: int, confidence: float = 0.95, horizon_days: int = 1) -> dict:
    snapshots = list(
        db.scalars(
            select(PortfolioDailySnapshot)
            .where(PortfolioDailySnapshot.portfolio_id == portfolio_id)
            .order_by(PortfolioDailySnapshot.snapshot_date.asc())
            .limit(400)
        )
    )
    if len(snapshots) < 30:
        return {
            "var_95_1d": None,
            "var_95_1d_pct": None,
            "method": "historical",
            "confidence": confidence,
            "sample_days": len(snapshots),
            "status": "insufficient_data",
        }
    values = pd.Series([float(s.total_value) for s in snapshots], dtype=float)
    returns = values.pct_change().dropna()
    if returns.empty:
        return {"var_95_1d": None, "var_95_1d_pct": None, "status": "insufficient_data", "sample_days": 0}
    var_pct = float(np.percentile(returns, (1 - confidence) * 100))
    current_value = float(values.iloc[-1])
    var_amount = abs(var_pct * current_value)
    return {
        "var_95_1d": round(var_amount, 2),
        "var_95_1d_pct": round(abs(var_pct) * 100, 4),
        "method": "historical",
        "confidence": confidence,
        "sample_days": len(returns),
        "status": "ok",
    }


def compute_concentration_risk(db: Session, portfolio_id: int) -> dict:
    holdings = list(
        db.scalars(
            select(PortfolioHolding)
            .where(PortfolioHolding.portfolio_id == portfolio_id)
            .options(joinedload(PortfolioHolding.stock))
        )
    )
    if not holdings:
        return {"holdings": [], "top_holding_pct": 0, "top_3_pct": 0, "hhi": 0, "concentration_level": "Low", "status": "ok"}

    price_map = get_latest_prices_map(db, [h.stock_id for h in holdings])
    rows = []
    for h in holdings:
        price = D(price_map.get(h.stock_id, h.average_buy_price))
        mv = float(D(h.quantity) * price)
        rows.append(
            {
                "symbol": h.stock.symbol if h.stock else str(h.stock_id),
                "market_value": mv,
            }
        )
    total = sum(r["market_value"] for r in rows) or 1.0
    for r in rows:
        r["weight_pct"] = round(r["market_value"] / total * 100, 2)
    rows.sort(key=lambda x: x["weight_pct"], reverse=True)
    weights = [r["weight_pct"] / 100 for r in rows]
    hhi = round(sum(w * w for w in weights) * 10_000, 2)
    top3 = sum(r["weight_pct"] for r in rows[:3])
    level = "Low"
    if hhi >= 2500:
        level = "Very High"
    elif hhi >= 1800:
        level = "High"
    elif hhi >= 1000:
        level = "Moderate"
    return {
        "holdings": rows,
        "top_holding_pct": rows[0]["weight_pct"] if rows else 0,
        "top_3_pct": round(top3, 2),
        "hhi": hhi,
        "concentration_level": level,
        "status": "ok",
    }


def compute_max_drawdown_vs_benchmark(db: Session, portfolio_id: int, lookback_days: int = 252) -> dict:
    cutoff = datetime.now(UTC).date() - timedelta(days=lookback_days)
    snapshots = list(
        db.scalars(
            select(PortfolioDailySnapshot)
            .where(
                PortfolioDailySnapshot.portfolio_id == portfolio_id,
                PortfolioDailySnapshot.snapshot_date >= cutoff,
            )
            .order_by(PortfolioDailySnapshot.snapshot_date.asc())
        )
    )
    if len(snapshots) < 10:
        return {
            "portfolio_max_drawdown_pct": None,
            "benchmark_max_drawdown_pct": None,
            "status": "insufficient_data",
        }
    port_series = pd.Series(
        [float(s.total_value) for s in snapshots],
        index=[s.snapshot_date for s in snapshots],
        dtype=float,
    )
    port_dd = (port_series / port_series.cummax() - 1) * 100
    port_max_dd = float(port_dd.min())

    fund = db.scalar(select(IndexFund).where(IndexFund.yahoo_symbol == "^NSEI"))
    bench_dd = None
    if fund:
        prices = list(
            db.scalars(
                select(IndexFundPrice)
                .where(
                    IndexFundPrice.index_fund_id == fund.id,
                    IndexFundPrice.timeframe == DAILY_TIMEFRAME,
                )
                .order_by(IndexFundPrice.price_datetime.asc())
            )
        )
        if len(prices) >= 10:
            frame = prices_to_dataframe(prices)
            bench_series = frame["close"].astype(float)
            bench_dd_series = (bench_series / bench_series.cummax() - 1) * 100
            bench_dd = float(bench_dd_series.min())

    return {
        "portfolio_max_drawdown_pct": round(abs(port_max_dd), 4),
        "benchmark_max_drawdown_pct": round(abs(bench_dd), 4) if bench_dd is not None else None,
        "status": "ok",
    }


def get_portfolio_risk_metrics(db: Session, portfolio_id: int, lookback_days: int = 252) -> dict:
    return {
        "beta": compute_portfolio_beta(db, portfolio_id, lookback_days),
        "var": compute_var(db, portfolio_id),
        "concentration": compute_concentration_risk(db, portfolio_id),
        "drawdown": compute_max_drawdown_vs_benchmark(db, portfolio_id, lookback_days),
        "refreshed_at": datetime.now(UTC).isoformat(),
    }
