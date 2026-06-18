"""Downloads missing NSE and BSE bhavcopy files from official archives.

Supports two NSE formats:
  · Classic ZIP  — archives.nseindia.com/content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip
  · New CSV      — nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv  (2024/2025+)

BSE format (plain CSV):
  · www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_YYYYMMDD_F.CSV
  · Fallback: www.bseindia.com/download/BhavCopy/Equity/EQ{DDMMYYYY}_CSV.ZIP
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

from app.services.exchange_bhavcopy_service import BHAVCOPY_DIR, clear_bhavcopy_cache

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
_BSE_HEADERS = {**_HEADERS, "Referer": "https://www.bseindia.com/"}
_TIMEOUT = 30

_MON = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR",
    5: "MAY", 6: "JUN", 7: "JUL", 8: "AUG",
    9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}


# ── URL builders ──────────────────────────────────────────────────────────────

def _nse_classic_url(d: date) -> str:
    mon = _MON[d.month]
    return (
        f"https://archives.nseindia.com/content/historical/EQUITIES"
        f"/{d.year}/{mon}/cm{d.day:02d}{mon}{d.year}bhav.csv.zip"
    )


def _nse_new_url(d: date) -> str:
    return (
        f"https://nsearchives.nseindia.com/products/content"
        f"/sec_bhavdata_full_{d.day:02d}{d.month:02d}{d.year}.csv"
    )


def _bse_new_url(d: date) -> str:
    return (
        f"https://www.bseindia.com/download/BhavCopy/Equity"
        f"/BhavCopy_BSE_CM_0_0_0_{d.year}{d.month:02d}{d.day:02d}_F.CSV"
    )


def _bse_old_url(d: date) -> str:
    return (
        f"https://www.bseindia.com/download/BhavCopy/Equity"
        f"/EQ{d.day:02d}{d.month:02d}{str(d.year)[2:]}_CSV.ZIP"
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_weekday(d: date) -> bool:
    return d.weekday() < 5  # Mon–Fri


def _nse_dest(d: date) -> Path:
    mon = _MON[d.month]
    return BHAVCOPY_DIR / "NSE" / str(d.year) / mon / f"cm{d.day:02d}{mon}{d.year}bhav.csv.zip"


def _nse_new_dest(d: date) -> Path:
    mon = _MON[d.month]
    return BHAVCOPY_DIR / "NSE" / str(d.year) / mon / f"sec_bhavdata_full_{d.day:02d}{d.month:02d}{d.year}.csv"


def _bse_dest(d: date) -> Path:
    return BHAVCOPY_DIR / "BSE" / str(d.year) / f"BhavCopy_BSE_CM_{d.year}{d.month:02d}{d.day:02d}_F.CSV"


def _already_have(d: date) -> bool:
    """Return True if we already have at least one file for this date."""
    return _nse_dest(d).exists() or _nse_new_dest(d).exists() or _bse_dest(d).exists()


def _fetch(url: str, headers: dict) -> bytes | None:
    try:
        resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
        if resp.status_code == 200 and len(resp.content) > 500:
            return resp.content
        logger.debug("Bhavcopy fetch %s → %s", url, resp.status_code)
    except Exception as exc:
        logger.debug("Bhavcopy fetch error %s: %s", url, exc)
    return None


# ── Per-day download ──────────────────────────────────────────────────────────

def _download_nse(d: date) -> bool:
    """Try classic ZIP first, then new CSV format. Returns True if saved."""
    # Classic format
    data = _fetch(_nse_classic_url(d), _HEADERS)
    if data:
        dest = _nse_dest(d)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True

    # New format (2024/2025+)
    data = _fetch(_nse_new_url(d), _HEADERS)
    if data:
        dest = _nse_new_dest(d)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True

    return False


def _download_bse(d: date) -> bool:
    """Try new BSE CSV URL first, then old ZIP format. Returns True if saved."""
    data = _fetch(_bse_new_url(d), _BSE_HEADERS)
    if data:
        dest = _bse_dest(d)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True

    data = _fetch(_bse_old_url(d), _BSE_HEADERS)
    if data:
        old_dest = BHAVCOPY_DIR / "BSE" / str(d.year) / f"EQ{d.day:02d}{d.month:02d}{str(d.year)[2:]}_CSV.ZIP"
        old_dest.parent.mkdir(parents=True, exist_ok=True)
        old_dest.write_bytes(data)
        return True

    return False


# ── Public API ────────────────────────────────────────────────────────────────

def get_last_available_date() -> date | None:
    """Scan local bhavcopy directory and return the latest date we have data for."""
    from app.services.exchange_bhavcopy_service import _date_from_filename

    latest: date | None = None
    for path in BHAVCOPY_DIR.rglob("*"):
        if path.suffix.lower() not in {".zip", ".csv"}:
            continue
        d = _date_from_filename(path)
        if d and (latest is None or d > latest):
            latest = d
    return latest


def sync_bhavcopy(
    start: date | None = None,
    end: date | None = None,
    *,
    delay_seconds: float = 0.5,
) -> dict[str, Any]:
    """Download all missing weekday bhavcopy files between start and end (inclusive).

    If start is None, resumes from the day after the last available local file.
    If end is None, uses today.
    """
    today = date.today()
    if end is None:
        end = today
    if start is None:
        last = get_last_available_date()
        start = (last + timedelta(days=1)) if last else date(2025, 1, 1)

    if start > end:
        return {
            "status": "up_to_date",
            "message": f"Already up to date through {end}.",
            "downloaded_nse": 0,
            "downloaded_bse": 0,
            "skipped": 0,
            "failed": 0,
            "start": str(start),
            "end": str(end),
        }

    downloaded_nse = 0
    downloaded_bse = 0
    skipped = 0
    failed = 0
    total_days = 0

    current = start
    while current <= end:
        if not _is_weekday(current):
            current += timedelta(days=1)
            continue

        total_days += 1

        if _already_have(current):
            skipped += 1
            current += timedelta(days=1)
            continue

        nse_ok = _download_nse(current)
        bse_ok = _download_bse(current)

        if nse_ok:
            downloaded_nse += 1
        if bse_ok:
            downloaded_bse += 1
        if not nse_ok and not bse_ok:
            failed += 1
            logger.info("No data available for %s (holiday or weekend?)", current)

        current += timedelta(days=1)
        if delay_seconds > 0:
            time.sleep(delay_seconds)

    # Invalidate cache so the new files are picked up on next audit
    clear_bhavcopy_cache()

    status = "ok" if (downloaded_nse + downloaded_bse) > 0 else "no_new_data"
    return {
        "status": status,
        "message": (
            f"Downloaded {downloaded_nse} NSE + {downloaded_bse} BSE files "
            f"({skipped} already had, {failed} unavailable/holidays)."
        ),
        "downloaded_nse": downloaded_nse,
        "downloaded_bse": downloaded_bse,
        "skipped": skipped,
        "failed": failed,
        "start": str(start),
        "end": str(end),
        "weekdays_in_range": total_days,
    }
