from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import UniqueConstraint, func, inspect, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SCRIPTS = ROOT / "scripts"
sys.path.append(str(BACKEND))
sys.path.append(str(SCRIPTS))

from app import models  # noqa: F401,E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models.index_fund import IndexFund  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.models.strategy import StrategyTemplate  # noqa: E402
from app.services.index_fund_service import load_index_funds_from_csv  # noqa: E402
from app.services.market_data_service import sync_all_active_stocks  # noqa: E402
from load_indian_tickers import (  # noqa: E402
    _write_failed,
    import_exchange,
    load_bse_online,
    load_csv,
    load_nse_online,
)
from seed_strategy_templates import STRATEGIES  # noqa: E402


LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, force=True)


configure_logging()
logger = logging.getLogger(__name__)


def check_database_connection() -> None:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise SystemExit(
            "Database connection failed. Start PostgreSQL and check DATABASE_URL before running ingestion."
        ) from exc
    logger.info("Database connection OK")


def missing_tables() -> list[str]:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    expected_tables = set(Base.metadata.tables.keys())
    return sorted(expected_tables - existing_tables)


def schema_drift() -> list[str]:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    drift: list[str] = []

    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            drift.append(f"{table_name}: missing table")
            continue

        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing_columns = sorted(set(table.columns.keys()) - existing_columns)
        if missing_columns:
            drift.append(f"{table_name}: missing columns {', '.join(missing_columns)}")

        existing_unique_constraints = {
            constraint["name"] for constraint in inspector.get_unique_constraints(table_name)
        }
        for constraint in table.constraints:
            if (
                isinstance(constraint, UniqueConstraint)
                and constraint.name
                and constraint.name not in existing_unique_constraints
            ):
                drift.append(f"{table_name}: missing unique constraint {constraint.name}")

    return drift


def run_migrations() -> None:
    alembic_cfg = Config(str(BACKEND / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(alembic_cfg, "head")
    configure_logging()


def stamp_migration_head() -> None:
    alembic_cfg = Config(str(BACKEND / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.stamp(alembic_cfg, "head")


def ensure_tables() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    expected_tables = set(Base.metadata.tables.keys())
    missing_before = missing_tables()
    existing_app_tables = expected_tables - set(missing_before)
    has_alembic_version = "alembic_version" in existing_tables

    if missing_before and existing_app_tables and not has_alembic_version:
        raise SystemExit(
            "Partial schema exists without Alembic version tracking. "
            "Use a clean database or migrate/stamp it manually before bootstrapping."
        )

    if missing_before:
        logger.info("Missing tables detected: %s", ", ".join(missing_before))
    elif not has_alembic_version:
        drift_before_stamp = schema_drift()
        if drift_before_stamp:
            raise SystemExit(
                "All app tables exist but Alembic is not stamped, and schema drift was found: "
                + "; ".join(drift_before_stamp)
                + ". Use Alembic migrations against a clean database instead of stamping this schema."
            )
        logger.info("All app tables exist and schema matches models; stamping current schema as head")
        stamp_migration_head()
    else:
        logger.info("All expected tables already exist; checking for pending migrations")

    run_migrations()

    drift_after = schema_drift()
    if drift_after:
        raise SystemExit("Migration completed but schema drift remains: " + "; ".join(drift_after))
    logger.info("Database schema is ready")


def load_tickers(source: str) -> None:
    if source == "online":
        try:
            nse = load_nse_online()
            bse = load_bse_online()
        except Exception:
            logger.exception("Online ticker loading failed; falling back to local CSV files")
            nse = load_csv("NSE")
            bse = load_csv("BSE")
    else:
        nse = load_csv("NSE")
        bse = load_csv("BSE")

    nse_count, nse_failed = import_exchange(nse, "NSE")
    bse_count, bse_failed = import_exchange(bse, "BSE")
    failed = [*nse_failed, *bse_failed]
    _write_failed(failed)
    logger.info(
        "Ticker ingestion complete: NSE=%s BSE=%s failed=%s",
        nse_count,
        bse_count,
        len(failed),
    )


def seed_strategies() -> None:
    with SessionLocal() as db:
        for strategy_name, strategy_type, description, default_parameters in STRATEGIES:
            stmt = insert(StrategyTemplate).values(
                strategy_name=strategy_name,
                strategy_type=strategy_type,
                description=description,
                default_parameters=default_parameters,
                is_active=True,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["strategy_name"],
                set_={
                    "strategy_type": stmt.excluded.strategy_type,
                    "description": stmt.excluded.description,
                    "default_parameters": stmt.excluded.default_parameters,
                    "is_active": stmt.excluded.is_active,
                },
            )
            db.execute(stmt)
        db.commit()
    logger.info("Strategy templates seeded")


def load_index_funds(csv_path: str) -> None:
    with SessionLocal() as db:
        result = load_index_funds_from_csv(db, csv_path)
    logger.info(
        "Index fund ingestion complete: upserted=%s failed=%s",
        result["upserted"],
        result["failed_count"],
    )


def sync_prices(limit: int | None) -> None:
    with SessionLocal() as db:
        result = sync_all_active_stocks(db, limit=limit)
    logger.info("Price sync complete: %s", result)


def log_row_counts() -> None:
    with SessionLocal() as db:
        stock_count = db.scalar(select(func.count()).select_from(Stock))
        index_fund_count = db.scalar(select(func.count()).select_from(IndexFund))
        strategy_count = db.scalar(select(func.count()).select_from(StrategyTemplate))
    logger.info(
        "Current row counts: stocks=%s index_funds=%s strategy_templates=%s",
        stock_count,
        index_fund_count,
        strategy_count,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap ingestion: check DB, create/update tables, load tickers, seed strategies."
    )
    parser.add_argument("--source", choices=["csv", "online"], default="csv")
    parser.add_argument("--skip-migrations", action="store_true")
    parser.add_argument("--skip-tickers", action="store_true")
    parser.add_argument("--skip-strategies", action="store_true")
    parser.add_argument("--load-index-funds", action="store_true")
    parser.add_argument(
        "--index-funds-csv-path",
        default=str(ROOT / "data" / "indexes_commodities_prepared.csv"),
    )
    parser.add_argument("--sync-prices", action="store_true")
    parser.add_argument("--price-limit", type=int, default=None)
    args = parser.parse_args()

    check_database_connection()
    if not args.skip_migrations:
        ensure_tables()
    if not args.skip_tickers:
        load_tickers(args.source)
    if not args.skip_strategies:
        seed_strategies()
    if args.load_index_funds:
        load_index_funds(args.index_funds_csv_path)
    if args.sync_prices:
        sync_prices(args.price_limit)
    log_row_counts()


if __name__ == "__main__":
    main()
