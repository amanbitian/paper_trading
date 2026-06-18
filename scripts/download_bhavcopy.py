from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "data" / "bhavcopy"

NSE_ARCHIVE_TEMPLATE = (
    "https://archives.nseindia.com/content/historical/EQUITIES/{year}/{mon}/"
    "cm{day}{mon}{year}bhav.csv.zip"
)
BSE_ZIP_TEMPLATE = "https://www.bseindia.com/download/BhavCopy/Equity/EQ{day}{month}{year2}_CSV.ZIP"
BSE_ISIN_TEMPLATE = "https://www.bseindia.com/download/BhavCopy/Equity/EQ_ISINCODE_{day}{month}{year2}.CSV"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "text/csv,application/zip,application/octet-stream,*/*",
    "Referer": "https://www.nseindia.com/",
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadTarget:
    exchange: str
    trade_date: date
    url: str
    path: Path


def trading_dates(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def previous_business_day(reference: date | None = None) -> date:
    current = reference or date.today()
    current -= timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def default_start_for_years(years: int, end: date) -> date:
    return end - timedelta(days=max(1, years) * 365)


def nse_target(trade_date: date, output_dir: Path) -> DownloadTarget:
    mon = trade_date.strftime("%b").upper()
    day = trade_date.strftime("%d")
    year = trade_date.strftime("%Y")
    url = NSE_ARCHIVE_TEMPLATE.format(year=year, mon=mon, day=day)
    path = output_dir / "NSE" / year / mon / f"cm{day}{mon}{year}bhav.csv.zip"
    return DownloadTarget("NSE", trade_date, url, path)


def bse_targets(trade_date: date, output_dir: Path) -> list[DownloadTarget]:
    day = trade_date.strftime("%d")
    month = trade_date.strftime("%m")
    year2 = trade_date.strftime("%y")
    year = trade_date.strftime("%Y")
    zip_url = BSE_ZIP_TEMPLATE.format(day=day, month=month, year2=year2)
    csv_url = BSE_ISIN_TEMPLATE.format(day=day, month=month, year2=year2)
    return [
        DownloadTarget("BSE", trade_date, zip_url, output_dir / "BSE" / year / f"EQ{day}{month}{year2}_CSV.ZIP"),
        DownloadTarget(
            "BSE",
            trade_date,
            csv_url,
            output_dir / "BSE" / year / f"EQ_ISINCODE_{day}{month}{year2}.CSV",
        ),
    ]


def request_bytes(session: requests.Session, url: str, *, timeout: int) -> bytes | None:
    response = session.get(url, headers=HEADERS, timeout=timeout)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if "text/html" in content_type and b"<html" in response.content[:200].lower():
        return None
    return response.content


def save_target(session: requests.Session, target: DownloadTarget, *, timeout: int, dry_run: bool) -> str:
    if target.path.exists():
        return "exists"
    if dry_run:
        logger.info("[dry-run] %s %s -> %s", target.exchange, target.trade_date, target.url)
        return "dry_run"
    content = request_bytes(session, target.url, timeout=timeout)
    if not content:
        return "missing"
    target.path.parent.mkdir(parents=True, exist_ok=True)
    target.path.write_bytes(content)
    return "downloaded"


def download_bhavcopies(
    *,
    exchanges: list[str],
    start_date: date,
    end_date: date,
    output_dir: Path,
    sleep_seconds: float,
    timeout: int,
    dry_run: bool,
) -> dict[str, int]:
    session = requests.Session()
    counts = {"downloaded": 0, "exists": 0, "missing": 0, "failed": 0, "dry_run": 0}
    dates = trading_dates(start_date, end_date)
    logger.info(
        "Starting bhavcopy download exchanges=%s trading_days=%s start=%s end=%s output=%s",
        ",".join(exchanges),
        len(dates),
        start_date,
        end_date,
        output_dir,
    )

    for trade_date in dates:
        for exchange in exchanges:
            targets = [nse_target(trade_date, output_dir)] if exchange == "NSE" else bse_targets(trade_date, output_dir)
            target_status = "missing"
            for target in targets:
                try:
                    target_status = save_target(session, target, timeout=timeout, dry_run=dry_run)
                    if target_status in {"downloaded", "exists", "dry_run"}:
                        break
                except requests.RequestException as exc:
                    logger.warning("%s %s failed: %s", exchange, trade_date, exc)
                    target_status = "failed"
            counts[target_status] = counts.get(target_status, 0) + 1
            if target_status in {"downloaded", "failed"}:
                logger.info("%s %s %s", exchange, trade_date, target_status)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    return counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download NSE/BSE equity bhavcopy files into data/bhavcopy for second-source validation."
    )
    parser.add_argument("--exchange", choices=["NSE", "BSE", "ALL"], default="ALL")
    parser.add_argument("--years", type=int, default=3, help="Lookback years when --start-date is omitted.")
    parser.add_argument("--start-date", help="YYYY-MM-DD. Defaults to --years before --end-date.")
    parser.add_argument("--end-date", help="YYYY-MM-DD. Defaults to previous business day.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--sleep-seconds", type=float, default=0.25, help="Pause between exchange requests.")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    end = date.fromisoformat(args.end_date) if args.end_date else previous_business_day()
    start = date.fromisoformat(args.start_date) if args.start_date else default_start_for_years(args.years, end)
    if start > end:
        raise SystemExit("--start-date must be on or before --end-date")
    if args.years < 1:
        raise SystemExit("--years must be at least 1")
    if args.sleep_seconds < 0:
        raise SystemExit("--sleep-seconds cannot be negative")

    exchanges = ["NSE", "BSE"] if args.exchange == "ALL" else [args.exchange]
    counts = download_bhavcopies(
        exchanges=exchanges,
        start_date=start,
        end_date=end,
        output_dir=Path(args.output_dir),
        sleep_seconds=args.sleep_seconds,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    logger.info("Finished bhavcopy download: %s", counts)
    print(counts)


if __name__ == "__main__":
    main()
