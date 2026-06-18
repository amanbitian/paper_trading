from __future__ import annotations

import csv
import logging
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BHAVCOPY_DIR = PROJECT_ROOT / "data" / "bhavcopy"

# Classic NSE/BSE columns + new NSE 2024/2025 sec_bhavdata_full format (camelCase headers)
DATE_COLUMNS = ("TIMESTAMP", "DATE1", "DATE", "TRADING_DATE", "TRADE_DATE", "TRADDT", "TRAD_DT")
SYMBOL_COLUMNS = ("SYMBOL", "SC_NAME", "SCRIP_NAME", "SCRIP_NM", "SECURITY", "TICKER", "TCKRSYMB", "SMBL")
BSE_CODE_COLUMNS = ("SC_CODE", "SCRIP_CD", "SCRIPCODE")
OPEN_COLUMNS = ("OPEN", "OPEN_PRICE", "OPEN_PRICE ", "OPNPRIC", "STARTPRIC")
HIGH_COLUMNS = ("HIGH", "HIGH_PRICE", "HIGH_PRICE ", "HGHPRIC")
LOW_COLUMNS = ("LOW", "LOW_PRICE", "LOW_PRICE ", "LWPRIC")
CLOSE_COLUMNS = ("CLOSE", "CLOSE_PRICE", "CLOSE_PRICE ", "CLSPRIC", "ENDPRIC")
VOLUME_COLUMNS = ("TOTTRDQTY", "TTL_TRD_QNTY", "NO_OF_SHRS", "VOLUME", "TOTAL_TRADED_QUANTITY", "TTLTRDDQTY")


@dataclass(frozen=True)
class BhavcopyCandle:
    exchange: str
    symbol: str
    trade_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: int | None
    source_file: str


def _clean_key(value: str) -> str:
    return value.strip().upper().replace(" ", "_")


def _clean_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    if "." in text:
        text = text.split(".", 1)[0]
    return " ".join(text.split())


def _decimal_or_none(value: Any) -> Decimal | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"-", "--", "N/A", "NA"}:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    decimal_value = _decimal_or_none(value)
    return None if decimal_value is None else int(decimal_value)


def _row_value(row: dict[str, Any], columns: tuple[str, ...]) -> Any:
    for column in columns:
        value = row.get(column)
        if value not in (None, ""):
            return value
    return None


def _date_from_value(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    formats = ("%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y%m%d")
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _date_from_filename(path: Path) -> date | None:
    digits = "".join(ch for ch in path.stem if ch.isdigit())
    for length in (8, 6):
        if len(digits) < length:
            continue
        for start in range(0, len(digits) - length + 1):
            chunk = digits[start : start + length]
            formats = ("%Y%m%d", "%d%m%Y") if length == 8 else ("%d%m%y",)
            for fmt in formats:
                try:
                    return datetime.strptime(chunk, fmt).date()
                except ValueError:
                    continue
    return None


def _exchange_from_path(path: Path) -> str | None:
    text = str(path).upper()
    name = path.name.upper()
    if "NSE" in text or name.startswith("CM") or "SEC_BHAVDATA" in name or "BHAVDATA" in name:
        return "NSE"
    if "BSE" in text or name.startswith("EQ") or "BHAVCOPY_BSE" in name:
        return "BSE"
    return None


def _normalise_row(row: dict[str, Any]) -> dict[str, Any]:
    return {_clean_key(key): value for key, value in row.items() if key is not None}


def _parse_csv_rows(rows: list[dict[str, Any]], *, path: Path, exchange_hint: str | None) -> dict[tuple[str, str, date], BhavcopyCandle]:
    parsed: dict[tuple[str, str, date], BhavcopyCandle] = {}
    fallback_date = _date_from_filename(path)
    exchange = exchange_hint or _exchange_from_path(path)
    for raw in rows:
        row = _normalise_row(raw)
        row_exchange = str(row.get("EXCHANGE") or row.get("EXCH") or exchange or "").strip().upper()
        if row_exchange not in {"NSE", "BSE"}:
            continue
        symbol = _clean_symbol(_row_value(row, SYMBOL_COLUMNS))
        if not symbol and row_exchange == "BSE":
            symbol = _clean_symbol(_row_value(row, BSE_CODE_COLUMNS))
        trade_date = _date_from_value(_row_value(row, DATE_COLUMNS)) or fallback_date
        if not symbol or not trade_date:
            continue
        candle = BhavcopyCandle(
            exchange=row_exchange,
            symbol=symbol,
            trade_date=trade_date,
            open=_decimal_or_none(_row_value(row, OPEN_COLUMNS)),
            high=_decimal_or_none(_row_value(row, HIGH_COLUMNS)),
            low=_decimal_or_none(_row_value(row, LOW_COLUMNS)),
            close=_decimal_or_none(_row_value(row, CLOSE_COLUMNS)),
            volume=_int_or_none(_row_value(row, VOLUME_COLUMNS)),
            source_file=str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path),
        )
        parsed[(candle.exchange, candle.symbol, candle.trade_date)] = candle
    return parsed


def _read_csv_file(path: Path, *, exchange_hint: str | None) -> dict[tuple[str, str, date], BhavcopyCandle]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return _parse_csv_rows(list(csv.DictReader(handle)), path=path, exchange_hint=exchange_hint)
    except UnicodeDecodeError:
        with path.open("r", encoding="latin-1", newline="") as handle:
            return _parse_csv_rows(list(csv.DictReader(handle)), path=path, exchange_hint=exchange_hint)


def _read_zip_file(path: Path, *, exchange_hint: str | None) -> dict[tuple[str, str, date], BhavcopyCandle]:
    parsed: dict[tuple[str, str, date], BhavcopyCandle] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if not name.lower().endswith(".csv"):
                    continue
                with archive.open(name) as member:
                    text = member.read().decode("utf-8-sig", errors="replace").splitlines()
                    rows = list(csv.DictReader(text))
                    parsed.update(_parse_csv_rows(rows, path=path, exchange_hint=exchange_hint))
    except zipfile.BadZipFile:
        logger.warning("Skipping invalid bhavcopy zip: %s", path)
    return parsed


def _directory_signature(root: Path) -> tuple[int, int, int]:
    if not root.exists():
        return (0, 0, 0)
    count = 0
    latest_mtime_ns = 0
    total_size = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".csv", ".zip"}:
            continue
        stat = path.stat()
        count += 1
        latest_mtime_ns = max(latest_mtime_ns, stat.st_mtime_ns)
        total_size += stat.st_size
    return (count, latest_mtime_ns, total_size)


@lru_cache(maxsize=8)
def _load_bhavcopy_candles_cached(
    root: str,
    file_count: int,
    latest_mtime_ns: int,
    total_size: int,
) -> dict[tuple[str, str, date], BhavcopyCandle]:
    bhavcopy_root = Path(root)
    _ = (file_count, latest_mtime_ns, total_size)
    if not bhavcopy_root.exists():
        return {}
    candles: dict[tuple[str, str, date], BhavcopyCandle] = {}
    for path in bhavcopy_root.rglob("*"):
        if not path.is_file():
            continue
        exchange_hint = _exchange_from_path(path)
        try:
            if path.suffix.lower() == ".csv":
                candles.update(_read_csv_file(path, exchange_hint=exchange_hint))
            elif path.suffix.lower() == ".zip":
                candles.update(_read_zip_file(path, exchange_hint=exchange_hint))
        except Exception:
            logger.exception("Failed parsing bhavcopy file %s", path)
    return candles


def clear_bhavcopy_cache() -> None:
    _load_bhavcopy_candles_cached.cache_clear()


def load_bhavcopy_candles(root: str | None = None) -> dict[tuple[str, str, date], BhavcopyCandle]:
    bhavcopy_root = Path(root) if root else BHAVCOPY_DIR
    signature = _directory_signature(bhavcopy_root)
    return _load_bhavcopy_candles_cached(str(bhavcopy_root), *signature)


def find_bhavcopy_candle(
    *,
    symbol: str,
    exchange: str,
    trade_date: date,
    root: str | None = None,
) -> BhavcopyCandle | None:
    candles = load_bhavcopy_candles(root)
    clean_exchange = str(exchange or "").strip().upper()
    clean_symbol = _clean_symbol(symbol)
    return candles.get((clean_exchange, clean_symbol, trade_date))
