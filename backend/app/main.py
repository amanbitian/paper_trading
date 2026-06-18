import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.config import settings
from app.services.web_ai_think_tank_helpers import validation_error_view
from app.web_utils import templates
from app.database import engine
from app.limiter import limiter
from app.utils import route_timing as _route_timing
from app.routers import (
    ai,
    auth,
    backtest,
    data,
    index_funds,
    market,
    news,
    paper_trading,
    portfolios,
    stocks,
    strategies,
    transactions,
    web,
    web_paper_partials,
    web_partials,
    web_portfolio_partials,
    web_backtesting_partials,
    web_risk_partials,
    web_strategy_lab_partials,
    web_trends_partials,
    web_data_partials,
    web_index_fund_partials,
    web_ai_think_tank_partials,
    web_explore_stock_partials,
    web_legacy,
)


logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("app.timing")
BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_DIR = Path(__file__).resolve().parent


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logging.getLogger("app.ai").setLevel(logging.INFO)
logging.getLogger("models.ollama_client").setLevel(logging.INFO)


def run_startup_migrations() -> None:
    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    script = ScriptDirectory.from_config(alembic_cfg)
    target_heads = set(script.get_heads())

    with engine.connect() as connection:
        current_heads = set(MigrationContext.configure(connection).get_current_heads())

    if current_heads == target_heads:
        logger.info("Database schema already at Alembic head: %s", ", ".join(sorted(target_heads)))
        return

    logger.info(
        "Applying Alembic migrations: current=%s target=%s",
        ", ".join(sorted(current_heads)) or "<base>",
        ", ".join(sorted(target_heads)),
    )
    command.upgrade(alembic_cfg, "head")


def _warm_market_overview_cache() -> None:
    """Prime the market overview cache with real data (yfinance + movers SQL) on startup."""
    try:
        from app.database import SessionLocal
        from app.services.market_overview_service import get_market_overview
        with SessionLocal() as db:
            get_market_overview(db=db, refresh=True)  # Force full compute, skip fast-path
        logger.info("Market overview cache warmed on startup")
    except Exception:
        logger.exception("Market overview cache warmup failed (non-fatal)")


def _ensure_price_index() -> None:
    """Create a partial index on stock_prices for 1d timeframe if it doesn't exist.

    Uses CONCURRENTLY so it never blocks reads/writes. Safe to call at startup.
    """
    try:
        from sqlalchemy import text
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(
                text(
                    """
                    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_sp_stock_dt_1d
                    ON stock_prices (stock_id, price_datetime)
                    WHERE timeframe = '1d'
                    """
                )
            )
        logger.info("stock_prices partial index verified/created")
    except Exception:
        logger.exception("Failed to create stock_prices partial index (non-fatal)")


def _warm_trends_cache() -> None:
    """Prime the trend filters + default daily trend data on startup."""
    try:
        from app.database import SessionLocal
        from app.services.web_analytics_helpers import (
            fetch_market_trends,
            get_cached_trend_filters,
            parse_trend_query,
        )
        with SessionLocal() as db:
            get_cached_trend_filters(db)
            default_query = parse_trend_query(period="daily", market_filter="stocks", limit=100)
            fetch_market_trends(db, default_query)
        logger.info("Trends cache warmed on startup")
    except Exception:
        logger.exception("Trends cache warmup failed (non-fatal)")


_NEWS_SYNC_INTERVAL_HOURS = 6


def _run_news_sync_loop() -> None:
    """Background thread: sync news for all bhav_index stocks every 6 hours."""
    import time as _time
    from app.database import SessionLocal
    from app.services.news_service import bulk_sync_news as _bulk_sync

    _time.sleep(120)  # Let the app fully start before first run
    while True:
        try:
            with SessionLocal() as db:
                result = _bulk_sync(db, limit_stocks=500)
            logger.info(
                "Scheduled news sync done: synced=%d skipped=%d failed=%d links_new=%d",
                result.get("synced", 0),
                result.get("skipped", 0),
                result.get("failed", 0),
                result.get("links_new", 0),
            )
        except Exception:
            logger.exception("Scheduled news sync failed (non-fatal)")
        _time.sleep(_NEWS_SYNC_INTERVAL_HOURS * 3600)


def _refresh_strategy_explainers_on_startup() -> None:
    import time as _time
    from app.database import SessionLocal
    from app.services.strategy_explainer_service import refresh_strategy_explanations_for_stocks

    delay = max(0, int(settings.strategy_explainer_startup_delay_seconds))
    if delay:
        _time.sleep(delay)
    exchange = (settings.strategy_explainer_startup_exchange or "").strip().upper() or None
    limit = max(1, min(int(settings.strategy_explainer_startup_limit or 100), 500))
    try:
        with SessionLocal() as db:
            result = refresh_strategy_explanations_for_stocks(
                db,
                exchange=exchange,
                limit=limit,
            )
        logger.info(
            "Startup strategy explainer refresh done: exchange=%s selected=%d refreshed=%d failed=%d",
            exchange or "ALL",
            result.get("selected", 0),
            result.get("refreshed", 0),
            result.get("failed", 0),
        )
    except Exception:
        logger.exception("Startup strategy explainer refresh failed (non-fatal)")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if settings.auto_migrate_on_start:
        logger.info("AUTO_MIGRATE_ON_START enabled; checking Alembic migration state")
        run_startup_migrations()
    threading.Thread(target=_warm_market_overview_cache, daemon=True).start()
    threading.Thread(target=_warm_trends_cache, daemon=True).start()
    threading.Thread(target=_ensure_price_index, daemon=True).start()
    threading.Thread(target=_run_news_sync_loop, daemon=True).start()
    if settings.strategy_explainer_refresh_on_start:
        threading.Thread(target=_refresh_strategy_explainers_on_startup, daemon=True).start()
    yield


app = FastAPI(
    title="Paper Trading App",
    description="Local-first paper trading and portfolio tracking for Indian equities.",
    version="0.1.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(data.router)
app.include_router(index_funds.router)
app.include_router(market.router)
app.include_router(news.router)
app.include_router(stocks.router)
app.include_router(portfolios.router)
app.include_router(transactions.router)
app.include_router(paper_trading.router)
app.include_router(strategies.router)
app.include_router(backtest.router)
app.include_router(ai.router)
app.include_router(web.router)
app.include_router(web_partials.router)
app.include_router(web_portfolio_partials.router)
app.include_router(web_paper_partials.router)
app.include_router(web_trends_partials.router)
app.include_router(web_risk_partials.router)
app.include_router(web_backtesting_partials.router)
app.include_router(web_strategy_lab_partials.router)
app.include_router(web_data_partials.router)
app.include_router(web_index_fund_partials.router)
app.include_router(web_ai_think_tank_partials.router)
app.include_router(web_explore_stock_partials.router)
app.include_router(web_legacy.router)


@app.middleware("http")
async def log_request_timing(request: Request, call_next):
    started_at = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        # Tell browsers to cache static assets for 1 hour and serve stale for up
        # to 24 hours while revalidating — cuts per-page round-trips for CSS/JS.
        if request.url.path.startswith("/static/") and status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=86400"
        return response
    finally:
        duration_ms = (time.perf_counter() - started_at) * 1000
        timing_logger.info(
            "operation=http_request method=%s path=%s status_code=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            status_code,
            duration_ms,
        )
        # Record into in-memory route-timing table (skips static assets)
        path = request.url.path
        if not path.startswith("/static/"):
            matched_route = request.scope.get("route")
            route_key = (
                f"{request.method} {matched_route.path}"
                if matched_route and hasattr(matched_route, "path")
                else f"{request.method} {path}"
            )
            _route_timing.record(route_key, duration_ms)


def _format_validation_error(error: dict) -> dict[str, str]:
    loc_parts = [str(part) for part in error.get("loc", []) if part != "body"]
    field = ".".join(loc_parts) or "request"
    error_type = str(error.get("type", "validation_error"))
    message = str(error.get("msg", "Invalid value"))

    if field == "password" and error_type == "string_too_short":
        message = "Password must be at least 8 characters."
    elif field == "password" and error_type == "string_too_long":
        message = "Password must be 72 characters or fewer."
    elif field == "password" and error_type == "value_error":
        message = message.removeprefix("Value error, ")
    elif field == "name" and error_type == "string_too_short":
        message = "Name must be at least 2 characters."
    elif field == "user_name" and error_type == "string_too_short":
        message = "Username must be at least 3 characters."
    elif field == "user_name" and error_type == "string_too_long":
        message = "Username must be 30 characters or fewer."
    elif field == "user_name" and error_type == "string_pattern_mismatch":
        message = "Username can contain only letters, numbers, and underscores."
    elif field == "email":
        message = "Enter a valid email address."
    elif field == "starting_cash":
        message = "Starting paper cash must be zero or greater."

    return {"field": field, "message": message, "type": error_type}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse | HTMLResponse:
    errors = [_format_validation_error(error) for error in exc.errors()]
    messages = [item["message"] for item in errors if item.get("message")]

    if (
        request.headers.get("X-Requested-With") == "fastapi-web"
        and "/web/partials/ai-think-tank/" in request.url.path
    ):
        mode = request.query_params.get("mode") or ""
        if not mode and request.method == "POST":
            try:
                form = await request.form()
                mode = str(form.get("mode") or "")
            except Exception:
                mode = ""
        if any("backtest" in msg.lower() for msg in messages):
            messages = [
                "Select a backtest run before using Backtest Interpreter.",
                *[
                    m
                    for m in messages
                    if "backtest" not in m.lower() and "integer" not in m.lower()
                ],
            ]
        view = validation_error_view(mode, messages or ["Check the selected mode and inputs."])
        return templates.TemplateResponse(
            "partials/ai_analysis_result.html",
            {"request": request, "result": view},
            status_code=200,
        )

    return JSONResponse(
        status_code=422,
        content={
            "detail": "Please fix the registration details and try again.",
            "errors": errors,
        },
    )


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/web/explore", status_code=302)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
