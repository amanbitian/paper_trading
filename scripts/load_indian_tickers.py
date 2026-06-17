from __future__ import annotations

import argparse
from io import StringIO
import logging
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DATA_DIR = ROOT / "data"
sys.path.append(str(BACKEND))

from app.database import SessionLocal  # noqa: E402
from app.services.ticker_service import (  # noqa: E402
    normalize_bse_symbol,
    normalize_nse_symbol,
    upsert_stock,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NSE_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
BSE_URL = "https://www.bseindia.com/download/BhavCopy/Equity/EQ_ISINCODE_010124.CSV"
FAILED_LOG = DATA_DIR / "failed_tickers.log"


def _request_csv(url: str) -> pd.DataFrame:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,application/csv,text/plain,*/*",
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return pd.read_csv(StringIO(response.text))


def load_nse_online() -> pd.DataFrame:
    dataframe = _request_csv(NSE_URL)
    return pd.DataFrame(
        {
            "symbol": dataframe["SYMBOL"].astype(str),
            "company_name": dataframe.get("NAME OF COMPANY", dataframe["SYMBOL"]).astype(str),
        }
    )


def load_bse_online() -> pd.DataFrame:
    dataframe = _request_csv(BSE_URL)
    code_col = "SC_CODE" if "SC_CODE" in dataframe.columns else dataframe.columns[0]
    name_col = "SC_NAME" if "SC_NAME" in dataframe.columns else code_col
    return pd.DataFrame(
        {
            "symbol": dataframe[code_col].astype(str).str.strip(),
            "company_name": dataframe[name_col].astype(str).str.strip(),
        }
    )


def load_csv(exchange: str) -> pd.DataFrame:
    file_name = "nse_tickers_sample.csv" if exchange == "NSE" else "bse_tickers_sample.csv"
    path = DATA_DIR / file_name
    return pd.read_csv(path)


def _write_failed(failed: list[str]) -> None:
    if not failed:
        return
    DATA_DIR.mkdir(exist_ok=True)
    FAILED_LOG.write_text("\n".join(failed), encoding="utf-8")
    logger.warning("Logged %s failed ticker rows to %s", len(failed), FAILED_LOG)


def import_exchange(dataframe: pd.DataFrame, exchange: str) -> tuple[int, list[str]]:
    inserted = 0
    failed: list[str] = []
    with SessionLocal() as db:
        for _, row in dataframe.iterrows():
            try:
                raw_symbol = str(row.get("symbol", "")).strip().upper()
                if not raw_symbol or raw_symbol == "NAN":
                    raise ValueError("Missing symbol")
                company_name = str(row.get("company_name", "")).strip() or None
                yahoo_symbol = (
                    normalize_nse_symbol(raw_symbol)
                    if exchange == "NSE"
                    else normalize_bse_symbol(raw_symbol)
                )
                upsert_stock(
                    db,
                    symbol=raw_symbol,
                    yahoo_symbol=yahoo_symbol,
                    exchange=exchange,
                    company_name=company_name,
                    is_active=True,
                )
                inserted += 1
            except Exception as exc:
                failed.append(f"{exchange},{row.to_dict()},{exc}")
                logger.debug("Failed ticker row: %s", exc)
        db.commit()
    return inserted, failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Load Indian NSE/BSE tickers into stocks table.")
    parser.add_argument("--source", choices=["online", "csv"], default="csv")
    args = parser.parse_args()

    failed: list[str] = []
    if args.source == "online":
        try:
            nse = load_nse_online()
            bse = load_bse_online()
        except Exception:
            logger.exception("Online loading failed. Falling back to sample CSV files.")
            nse = load_csv("NSE")
            bse = load_csv("BSE")
    else:
        nse = load_csv("NSE")
        bse = load_csv("BSE")

    nse_count, nse_failed = import_exchange(nse, "NSE")
    bse_count, bse_failed = import_exchange(bse, "BSE")
    failed.extend(nse_failed)
    failed.extend(bse_failed)
    _write_failed(failed)
    logger.info("Loaded %s NSE rows and %s BSE rows", nse_count, bse_count)


if __name__ == "__main__":
    main()
