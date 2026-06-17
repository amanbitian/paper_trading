from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import pandas as pd

from app.models.market_index import MarketIndex, StockIndexMembership
from app.models.stock import Stock, StockPerformanceSnapshot
from app.strategies.base import BaseStrategy, SignalResult

UNIVERSE_INDEX_CODES = {
    "NIFTY_500": "NIFTY500",
    "NIFTY500": "NIFTY500",
}

MOMENTUM_COLUMNS = {
    "1m": StockPerformanceSnapshot.change_1m_pct,
    "3m": StockPerformanceSnapshot.change_3m_pct,
    "6m": StockPerformanceSnapshot.change_6m_pct,
}


def _sector_momentum_rankings(
    db: Session,
    parameters: dict,
) -> tuple[list[dict], list[str], list[str]]:
    top_n = int(parameters.get("top_n_sectors", 2))
    bottom_n = int(parameters.get("bottom_n_sectors", 2))
    momentum_period = str(parameters.get("momentum_period", "1m"))
    min_stocks = int(parameters.get("min_stocks_per_sector", 5))
    universe = str(parameters.get("universe", "NIFTY_500"))
    change_col = MOMENTUM_COLUMNS.get(momentum_period, StockPerformanceSnapshot.change_1m_pct)

    stmt = (
        select(
            StockPerformanceSnapshot.sector,
            func.avg(change_col).label("avg_return"),
            func.count(StockPerformanceSnapshot.stock_id).label("stock_count"),
        )
        .join(Stock, Stock.id == StockPerformanceSnapshot.stock_id)
        .where(
            StockPerformanceSnapshot.sector.is_not(None),
            StockPerformanceSnapshot.sector != "",
            change_col.is_not(None),
        )
        .group_by(StockPerformanceSnapshot.sector)
    )

    index_code = UNIVERSE_INDEX_CODES.get(universe.upper(), universe.upper().replace("_", ""))
    market_index = db.scalar(
        select(MarketIndex).where(
            MarketIndex.index_code == index_code,
            MarketIndex.is_active.is_(True),
        )
    )
    if market_index is not None:
        membership_stock_ids = select(StockIndexMembership.stock_id).where(
            StockIndexMembership.index_id == market_index.id,
            StockIndexMembership.is_active.is_(True),
        )
        stmt = stmt.where(StockPerformanceSnapshot.stock_id.in_(membership_stock_ids))

    rows = db.execute(stmt).all()
    sector_stats = [
        {
            "sector": row.sector,
            "avg_return": float(row.avg_return or 0),
            "stock_count": int(row.stock_count or 0),
        }
        for row in rows
        if int(row.stock_count or 0) >= min_stocks
    ]
    sector_stats.sort(key=lambda item: item["avg_return"], reverse=True)

    for rank, item in enumerate(sector_stats, start=1):
        item["rank"] = rank
        item["rank_from_bottom"] = len(sector_stats) - rank + 1

    top_sectors = [item["sector"] for item in sector_stats[:top_n]]
    bottom_sectors = [item["sector"] for item in sector_stats[-bottom_n:]] if sector_stats else []
    return sector_stats, top_sectors, bottom_sectors


class SectorRotationStrategy(BaseStrategy):
    name = "Sector Rotation Momentum"
    strategy_type = "sector_rotation"
    default_parameters = {
        "top_n_sectors": 2,
        "bottom_n_sectors": 2,
        "momentum_period": "1m",
        "min_stocks_per_sector": 5,
        "universe": "NIFTY_500",
    }

    def generate_signal(
        self,
        prices: pd.DataFrame,
        parameters: dict | None = None,
        *,
        db: Session | None = None,
        stock_id: int | None = None,
    ) -> SignalResult:
        params = self.merged_parameters(parameters)
        if db is None or stock_id is None:
            return SignalResult(
                "HOLD",
                50,
                "Sector rotation requires database context",
                {"status": "missing_sector_data"},
            )

        stock = db.get(Stock, stock_id)
        snapshot = db.get(StockPerformanceSnapshot, stock_id)
        sector = (stock.sector if stock else None) or (snapshot.sector if snapshot else None)
        if not sector:
            return SignalResult(
                "HOLD",
                50,
                "Stock has no sector assignment",
                {"status": "missing_sector_data"},
            )

        sector_stats, top_sectors, bottom_sectors = _sector_momentum_rankings(db, params)
        if not sector_stats:
            return SignalResult(
                "HOLD",
                50,
                "Insufficient sector performance snapshots",
                {"status": "missing_sector_data"},
            )

        sector_row = next((item for item in sector_stats if item["sector"] == sector), None)
        if sector_row is None:
            return SignalResult(
                "HOLD",
                50,
                f"Sector '{sector}' has insufficient stocks in universe",
                {"status": "missing_sector_data", "stock_sector": sector},
            )

        momentum_period = str(params.get("momentum_period", "1m"))
        indicators = {
            "stock_sector": sector,
            "sector_rank": sector_row["rank"],
            "sector_1m_return": sector_row["avg_return"],
            "total_sectors_ranked": len(sector_stats),
            "top_sectors": top_sectors,
            "bottom_sectors": bottom_sectors,
            "status": "ok",
        }

        if sector in top_sectors:
            rank = sector_row["rank"]
            confidence = 90 if rank == 1 else 75
            reason = (
                f"Sector '{sector}' ranks #{rank} in {momentum_period.upper()} momentum "
                f"(+{sector_row['avg_return']:.1f}%); {sector_row['stock_count']} stocks in sector."
            )
            return SignalResult("BUY", confidence, reason, indicators)

        if sector in bottom_sectors:
            confidence = 85
            reason = (
                f"Sector '{sector}' ranks #{sector_row['rank_from_bottom']} weakest; "
                f"1M return {sector_row['avg_return']:.1f}%"
            )
            return SignalResult("SELL", confidence, reason, indicators)

        return SignalResult(
            "HOLD",
            50,
            f"Sector '{sector}' is mid-pack (rank #{sector_row['rank']})",
            indicators,
        )
