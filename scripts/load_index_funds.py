from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.append(str(BACKEND))

from app.database import SessionLocal  # noqa: E402
from app.services.index_fund_service import load_index_funds_from_csv  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Load index/commodity Yahoo tickers into index_funds.")
    parser.add_argument(
        "--csv-path",
        default=str(ROOT / "data" / "indexes_commodities_prepared.csv"),
        help="CSV with Symbol,Yahoo_Ticker,Base_Currency,Latest_Price,Value_in_INR.",
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        result = load_index_funds_from_csv(db, args.csv_path)

    print(
        f"Loaded index funds: upserted={result['upserted']} failed={result['failed_count']}",
        flush=True,
    )
    failed = result.get("failed") or []
    if failed:
        print("First failed rows:", flush=True)
        for row in failed:
            print(f"  {row}", flush=True)


if __name__ == "__main__":
    main()
