from datetime import date, datetime

from pydantic import BaseModel, Field


class MarketQuoteRead(BaseModel):
    label: str
    symbol: str
    yahoo_symbol: str
    kind: str
    price: float
    change: float
    change_pct: float
    volume: int | None = None
    record_date: date | None = None
    sparkline: list[float] = Field(default_factory=list)


class MarketOverviewRead(BaseModel):
    as_of: datetime
    record_date: date | None = None
    source: str
    movers_universe_count: int | None = None
    indices: list[MarketQuoteRead]
    most_bought: list[MarketQuoteRead]
    top_gainers: list[MarketQuoteRead]
    top_losers: list[MarketQuoteRead]
    volume_shockers: list[MarketQuoteRead]


class MarketMoversRead(BaseModel):
    as_of: datetime
    record_date: date | None = None
    source: str = "database"
    eligible_count: int = 0
    nifty_index: str | None = None
    nifty_index_label: str | None = None
    top_gainers: list[MarketQuoteRead]
    top_losers: list[MarketQuoteRead]
    volume_shockers: list[MarketQuoteRead]
    most_bought: list[MarketQuoteRead] = Field(default_factory=list)


class SequentialRankingItemRead(BaseModel):
    stock_id: int
    symbol: str
    yahoo_symbol: str
    exchange: str
    company_name: str | None = None
    action: str
    confidence_score: float
    sequence_score: float
    latest_close: float | None = None
    as_of_date: date | None = None
    reason: str


class SequentialRankingsRead(BaseModel):
    as_of: datetime
    rows_scanned: int
    eligible_count: int
    top_buys: list[SequentialRankingItemRead]
    top_sells: list[SequentialRankingItemRead]


class MarketTrendFilterOptionRead(BaseModel):
    label: str
    value: str
    constituent_count: int | None = None


class MarketTrendFiltersRead(BaseModel):
    markets: list[MarketTrendFilterOptionRead]
    industry_groups: list[str]
    nifty_indices: list[MarketTrendFilterOptionRead] = Field(default_factory=list)
    all_stocks_eligible_max: int = 5000
    sort_options: list[MarketTrendFilterOptionRead] = Field(default_factory=list)


class MarketTrendItemRead(BaseModel):
    instrument_type: str = "stock"
    instrument_id: int
    stock_id: int | None = None
    index_fund_id: int | None = None
    symbol: str
    yahoo_symbol: str
    exchange: str
    market_bucket: str | None = None
    company_name: str
    sector: str
    industry: str
    industry_group: str | None = None
    latest_price_datetime: datetime | None = None
    latest_price: float | None = None
    latest_return_price: float | None = None
    latest_volume: int | None = None
    baseline_price_datetime: datetime | None = None
    baseline_price: float | None = None
    baseline_return_price: float | None = None
    change_pct: float | None = None
    change_amount: float | None = None
    size_value: float
    calculation_basis: str | None = None


class MarketTrendsRead(BaseModel):
    as_of: datetime
    period: str
    period_label: str
    lookback_days: int
    market_filter: str | None = None
    market_label: str | None = None
    nifty_index: str | None = None
    nifty_index_label: str | None = None
    industry_group: str | None = None
    sort_by: str | None = None
    limit_requested: int | None = None
    universe_eligible_count: int | None = None
    record_date: date | None = None
    baseline_date: date | None = None
    calculation_basis: str | None = None
    row_count: int
    items: list[MarketTrendItemRead]


class MarketSyncRunRead(BaseModel):
    id: int
    status: str
    ingestion_mode: str
    total_symbols: int
    success_count: int
    failed_count: int
    rows_saved: int
    started_at: datetime
    finished_at: datetime | None = None
    error_message: str | None = None


class MarketSyncStatusRead(BaseModel):
    is_running: bool
    run_id: int | None = None
    last_synced_at: datetime | None = None
    last_sync_status: str | None = None
    record_date: date | None = None
    current_run: MarketSyncRunRead | None = None
    last_run: MarketSyncRunRead | None = None


class MarketSyncStartRead(BaseModel):
    started: bool
    message: str
    run_id: int | None = None
