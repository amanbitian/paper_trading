"""
Download NSE index constituent CSVs and load each into its own Postgres table.

Standalone utility — not wired into the application models or API yet.

Usage (Docker):
  docker compose run --rm backend python /app/scripts/create_nse_index_csv_tables.py

Usage (local, with backend venv + DATABASE_URL):
  py -3 scripts/create_nse_index_csv_tables.py
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
CSV_DIR = ROOT / "data" / "nse_index_csvs"
sys.path.append(str(BACKEND))

from app.database import engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NSE_INDEX_SOURCES: dict[str, str] = {
    "NIFTY 50": "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "NIFTY 100": "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv",
    "NIFTY 200": "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv",
    "NIFTY 500": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "BANKNIFTY": "https://nsearchives.nseindia.com/content/indices/ind_niftybanklist.csv",
    "FINNIFTY": "https://nsearchives.nseindia.com/content/indices/ind_niftyfinancelist.csv",
    "MIDCPNIFTY": "https://nsearchives.nseindia.com/content/indices/ind_niftymidcapselect_list.csv",
}

TABLE_PREFIX = "nse_csv_"


def _table_name(index_label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", index_label.strip().lower())
    slug = slug.strip("_")
    return f"{TABLE_PREFIX}{slug}"


def _download_csv(url: str) -> pd.DataFrame:
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


def _normalize_dataframe(dataframe: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "Company Name": "company_name",
        "NAME OF COMPANY": "company_name",
        "Industry": "industry",
        "Macro-Economic Sector": "industry",
        "Symbol": "symbol",
        "SYMBOL": "symbol",
        "Series": "series",
        "ISIN Code": "isin_code",
        "ISIN": "isin_code",
    }
    normalized = dataframe.rename(columns=rename_map)
    keep = ["company_name", "industry", "symbol", "series", "isin_code"]
    for column in keep:
        if column not in normalized.columns:
            normalized[column] = None
    normalized = normalized[keep].copy()
    for column in keep:
        normalized[column] = normalized[column].astype(str).str.strip()
        normalized[column] = normalized[column].replace({"nan": None, "None": None, "": None})
    normalized = normalized.dropna(subset=["symbol"])
    normalized = normalized[normalized["symbol"].str.len() > 0]
    return normalized.reset_index(drop=True)


def _create_table(conn, table_name: str) -> None:
    conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id SERIAL PRIMARY KEY,
                company_name VARCHAR(255),
                industry VARCHAR(255),
                symbol VARCHAR(50) NOT NULL,
                series VARCHAR(20),
                isin_code VARCHAR(20),
                source_index VARCHAR(80) NOT NULL,
                source_url TEXT NOT NULL,
                loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    conn.execute(
        text(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_symbol ON {table_name} (symbol)")
    )


def _load_table(
    conn,
    *,
    table_name: str,
    index_label: str,
    source_url: str,
    dataframe: pd.DataFrame,
    replace: bool,
) -> int:
    loaded_at = datetime.now(timezone.utc)
    if replace:
        conn.execute(text(f"TRUNCATE TABLE {table_name} RESTART IDENTITY"))

    rows = [
        {
            "company_name": row["company_name"],
            "industry": row["industry"],
            "symbol": row["symbol"],
            "series": row["series"],
            "isin_code": row["isin_code"],
            "source_index": index_label,
            "source_url": source_url,
            "loaded_at": loaded_at,
        }
        for _, row in dataframe.iterrows()
    ]
    if not rows:
        return 0

    conn.execute(
        text(
            f"""
            INSERT INTO {table_name}
                (company_name, industry, symbol, series, isin_code, source_index, source_url, loaded_at)
            VALUES
                (:company_name, :industry, :symbol, :series, :isin_code, :source_index, :source_url, :loaded_at)
            """
        ),
        rows,
    )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download NSE index CSVs into standalone Postgres tables."
    )
    parser.add_argument(
        "--csv-dir",
        default=str(CSV_DIR),
        help="Directory to save downloaded CSV files.",
    )
    parser.add_argument(
        "--no-save-csv",
        action="store_true",
        help="Skip saving CSV files to disk.",
    )
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Append to existing tables instead of truncating before reload.",
    )
    args = parser.parse_args()

    csv_dir = Path(args.csv_dir)
    if not args.no_save_csv:
        csv_dir.mkdir(parents=True, exist_ok=True)

    created_tables: list[dict[str, str | int]] = []

    with engine.begin() as conn:
        for index_label, url in NSE_INDEX_SOURCES.items():
            table_name = _table_name(index_label)
            logger.info("Downloading %s from %s", index_label, url)
            raw_df = _download_csv(url)
            normalized_df = _normalize_dataframe(raw_df)

            if not args.no_save_csv:
                csv_path = csv_dir / f"{table_name}.csv"
                raw_df.to_csv(csv_path, index=False)
                logger.info("Saved CSV to %s", csv_path)

            _create_table(conn, table_name)
            row_count = _load_table(
                conn,
                table_name=table_name,
                index_label=index_label,
                source_url=url,
                dataframe=normalized_df,
                replace=not args.keep_existing,
            )
            created_tables.append(
                {
                    "index": index_label,
                    "table_name": table_name,
                    "rows": row_count,
                }
            )
            logger.info("Loaded %s rows into %s", row_count, table_name)

    print("\nCreated / loaded standalone NSE CSV tables:\n")
    for entry in created_tables:
        print(f"  {entry['table_name']:<28}  ({entry['index']}, {entry['rows']} rows)")
    print("\nThese tables are not connected to the application yet.")


if __name__ == "__main__":
    main()
