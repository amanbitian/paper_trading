from datetime import date, datetime

from pydantic import BaseModel


class ExchangeStockCountsRead(BaseModel):
    exchange: str
    total_stocks: int
    stocks_with_prices: int


class IngestionRunSummaryRead(BaseModel):
    id: int
    status: str
    ingestion_mode: str
    exchange: str | None = None
    total_symbols: int
    success_count: int
    failed_count: int
    rows_saved: int
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    error_message: str | None = None


class DatabaseTableRead(BaseModel):
    schema_name: str
    table_name: str
    row_estimate: int | None = None


class DatabaseInfoRead(BaseModel):
    database_name: str | None = None
    schema_name: str | None = None
    database_user: str | None = None
    server_host: str | None = None
    server_port: int | None = None
    postgres_version: str | None = None
    table_count: int
    tables: list[DatabaseTableRead]


class SearchTimingRead(BaseModel):
    id: int
    search_type: str
    query_text: str
    filter_name: str | None = None
    filter_value: str | None = None
    result_count: int
    duration_ms: float
    status: str
    created_at: datetime


class SearchTimingAverageRead(BaseModel):
    search_type: str
    query_text: str
    filter_name: str | None = None
    filter_value: str | None = None
    search_count: int
    avg_response_ms: float | None = None
    max_response_ms: float | None = None
    latest_search_at: datetime | None = None


class SearchLatencySummaryRead(BaseModel):
    total_searches: int
    avg_response_ms: float | None = None
    max_response_ms: float | None = None
    p95_response_ms: float | None = None
    latest_search_at: datetime | None = None
    recent_searches: list[SearchTimingRead]
    average_by_query: list[SearchTimingAverageRead]


class DataIngestionDashboardRead(BaseModel):
    as_of: datetime
    database_info: DatabaseInfoRead | None = None
    search_latency: SearchLatencySummaryRead | None = None
    sync_is_running: bool
    last_synced_at: datetime | None = None
    last_sync_status: str | None = None
    last_sync_duration_seconds: float | None = None
    last_sync_mode: str | None = None
    last_sync_symbols_attempted: int | None = None
    last_sync_symbols_succeeded: int | None = None
    last_sync_symbols_failed: int | None = None
    last_sync_rows_saved: int | None = None
    latest_price_date: date | None = None
    earliest_price_date: date | None = None
    analytics_refreshed_at: datetime | None = None
    total_stocks: int
    active_stocks: int
    stocks_with_daily_prices: int
    price_coverage_pct: float
    total_daily_price_rows: int
    performance_snapshots: int
    stocks_with_sector: int
    stocks_with_industry: int
    distinct_sectors: int
    distinct_industries: int
    movers_universe_count: int | None = None
    exchange_breakdown: list[ExchangeStockCountsRead]
    recent_runs: list[IngestionRunSummaryRead]
