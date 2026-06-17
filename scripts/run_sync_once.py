"""Run one incremental sync and print summary."""
from __future__ import annotations

import json

from app.database import SessionLocal
from app.services.market_data_service import sync_all_active_stocks


def main() -> None:
    db = SessionLocal()
    try:
        result = sync_all_active_stocks(db, incremental=True, workers=4, download_batch_size=200)
        print(json.dumps(result, default=str, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
