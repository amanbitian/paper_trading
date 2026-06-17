"""
Load stock membership tags for NIFTY/Sensex indices.

Online source tries NSE public CSVs saved in app constants and uses the local
CSV fallback for Sensex because BSE does not expose a stable simple CSV
endpoint for this app yet.
"""

from __future__ import annotations

import argparse
from datetime import date
from io import StringIO
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DATA_DIR = ROOT / "data"
sys.path.append(str(BACKEND))

from app.database import SessionLocal  # noqa: E402
from app.constants.market_indices import (  # noqa: E402
    INDEX_DEFINITIONS,
    INDEX_MEMBERSHIP_URLS_BY_CODE,
)
from app.services.market_index_service import load_index_membership_rows  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NSE_INDEX_URLS = INDEX_MEMBERSHIP_URLS_BY_CODE
DEFAULT_FALLBACK_CSV = DATA_DIR / "index_constituents_sample.csv"


def _request_csv(url: str) -> pd.DataFrame:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/csv,application/csv,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/",
    }
    session = requests.Session()
    session.get("https://www.nseindia.com/", headers=headers, timeout=30)
    response = session.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return pd.read_csv(StringIO(response.text))


def _clean(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _normalize_online_nse(dataframe: pd.DataFrame, index_code: str) -> list[dict[str, Any]]:
    definition = INDEX_DEFINITIONS[index_code]
    rows: list[dict[str, Any]] = []
    for _, row in dataframe.iterrows():
        symbol = _clean(row.get("Symbol") or row.get("SYMBOL"))
        if not symbol:
            continue
        rows.append(
            {
                "index_code": index_code,
                "index_name": definition["index_name"],
                "provider": definition["provider"],
                "index_exchange": definition["exchange"],
                "index_yahoo_symbol": definition["yahoo_symbol"],
                "stock_exchange": "NSE",
                "symbol": symbol.upper(),
                "company_name": _clean(row.get("Company Name") or row.get("NAME OF COMPANY")),
                "industry": _clean(row.get("Industry") or row.get("Macro-Economic Sector")),
                "series": _clean(row.get("Series")),
                "isin": _clean(row.get("ISIN Code") or row.get("ISIN")),
                "weight": _clean(row.get("Weightage") or row.get("Weight")),
            }
        )
    return rows


def load_online_rows(fallback_csv: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index_code, url in NSE_INDEX_URLS.items():
        logger.info("Downloading %s constituents from %s", index_code, url)
        dataframe = _request_csv(url)
        normalized = _normalize_online_nse(dataframe, index_code)
        logger.info("Loaded %s rows for %s", len(normalized), index_code)
        rows.extend(normalized)

    fallback_rows = load_csv_rows(fallback_csv)
    sensex_rows = [row for row in fallback_rows if row["index_code"].upper() == "SENSEX"]
    logger.info("Loaded %s Sensex rows from fallback CSV %s", len(sensex_rows), fallback_csv)
    rows.extend(sensex_rows)
    return rows


def load_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Index constituents CSV not found: {path}")
    dataframe = pd.read_csv(path)
    required = {"index_code", "symbol"}
    missing = required - set(dataframe.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {sorted(missing)}")
    return [
        {column: _clean(row.get(column)) for column in dataframe.columns}
        for _, row in dataframe.iterrows()
        if _clean(row.get("index_code")) and _clean(row.get("symbol"))
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Load stock memberships for NIFTY/Sensex indices.")
    parser.add_argument("--source", choices=["online", "csv"], default="online")
    parser.add_argument(
        "--csv-path",
        default=str(DEFAULT_FALLBACK_CSV),
        help="Fallback/local CSV with index_code,symbol,... columns.",
    )
    parser.add_argument("--effective-date", help="Optional YYYY-MM-DD date for this constituent snapshot.")
    parser.add_argument(
        "--deactivate-missing",
        action="store_true",
        help="Deactivate older memberships missing from the loaded snapshot.",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    effective_date = date.fromisoformat(args.effective_date) if args.effective_date else None

    if args.source == "online":
        try:
            rows = load_online_rows(csv_path)
            source = "online"
        except Exception:
            logger.exception("Online constituent loading failed; falling back to %s", csv_path)
            rows = load_csv_rows(csv_path)
            source = "csv_fallback"
    else:
        rows = load_csv_rows(csv_path)
        source = "csv"

    logger.info("Importing %s membership rows source=%s", len(rows), source)
    with SessionLocal() as db:
        result = load_index_membership_rows(
            db,
            rows,
            source=source,
            effective_date=effective_date,
            deactivate_missing=args.deactivate_missing and source == "online",
        )
    print(
        "Loaded index memberships: "
        f"upserted={result['upserted']} failed={result['failed']} "
        f"flags_refreshed={result.get('flags_refreshed', 0)} source={source}",
        flush=True,
    )


if __name__ == "__main__":
    main()
