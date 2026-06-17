from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import SessionLocal  # noqa: E402
from app.services.fundamentals_service import (  # noqa: E402
    audit_fundamentals_table,
    sync_all_stock_fundamentals,
)
from app.utils.json_safe import to_json_safe  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync latest yfinance fundamentals once.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="Sync all eligible active stocks.")
    group.add_argument("--limit", type=int, default=5, help="Limit eligible stocks for a test run.")
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--audit-db",
        action="store_true",
        help="Print stock_fundamentals_latest table audit and exit (no sync).",
    )
    return parser.parse_args()


def _print_summary(result: dict) -> None:
    selected = int(result.get("selected_stocks") or 0)
    duration = float(result.get("duration_seconds") or 0)
    avg_ms = (duration * 1000 / selected) if selected else 0.0
    print("\n=== Fundamentals sync summary ===")
    print(f"status:          {result.get('status')}")
    print(f"table:           {result.get('table_name')}")
    print(f"selected:        {selected}")
    print(f"succeeded:       {result.get('succeeded')}")
    print(f"failed:          {result.get('failed')}")
    print(f"inserted:        {result.get('rows_inserted')}")
    print(f"updated:         {result.get('rows_updated')}")
    print(f"upserted:        {result.get('rows_upserted')}")
    print(f"columns/metrics: {result.get('columns_ingested')}")
    print(f"duration_s:      {duration:.2f}")
    print(f"avg_per_stock_ms:{avg_ms:.1f}")
    failed_symbols = result.get("failed_symbols") or []
    if failed_symbols:
        print("\nTop failures:")
        for row in failed_symbols[:10]:
            print(
                f"  - {row.get('symbol')} ({row.get('ticker')}): "
                f"{row.get('error_type') or 'failed'} — {row.get('error_message')}"
            )


def _print_audit(audit: dict) -> None:
    print("\n=== stock_fundamentals_latest audit ===")
    for key, value in audit.items():
        print(f"{key}: {value}")


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    try:
        if args.audit_db:
            _print_audit(audit_fundamentals_table(db))
            return 0

        limit = None if args.all else max(1, int(args.limit or 5))
        result = sync_all_stock_fundamentals(
            db,
            active_only=True,
            limit=limit,
            sleep_seconds=max(0.0, float(args.sleep_seconds)),
            timeout_seconds=max(1.0, float(args.timeout_seconds)),
        )
        _print_summary(result)
        _print_audit(audit_fundamentals_table(db))
        print("\n=== JSON result ===")
        print(json.dumps(to_json_safe(result), indent=2))
        return 0 if result.get("status") in {"success", "partial", "warning"} else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
