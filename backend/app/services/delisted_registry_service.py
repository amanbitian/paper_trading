from __future__ import annotations

from datetime import UTC, datetime
import logging
import os
from pathlib import Path
import threading

from sqlalchemy.orm import Session

from app.models.stock import Stock


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DELISTED_CONSTANT_FILE = Path(
    os.getenv("DELISTED_CONSTANT_FILE", str(PROJECT_ROOT / "constant.txt"))
)
_CONSTANT_FILE_LOCK = threading.Lock()

DELISTED_ERROR_MARKERS = (
    "possibly delisted",
    "no timezone found",
    "yftzmissingerror",
    "delisted",
)


def is_yfinance_delisted_error(message: str | None) -> bool:
    if not message:
        return False
    lowered = message.lower()
    return any(marker in lowered for marker in DELISTED_ERROR_MARKERS)


def _escape_record_value(value: object) -> str:
    return str(value or "").replace("\n", " ").replace("\r", " ").replace("|", "/").strip()


def _ensure_constant_file(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Yahoo Finance delisted/invalid ticker registry\n"
        "# yahoo_symbol|symbol|exchange|detected_at_utc|reason\n",
        encoding="utf-8",
    )


def append_delisted_constant(stock: Stock, reason: str | None, detected_at: datetime) -> None:
    path = DELISTED_CONSTANT_FILE
    try:
        with _CONSTANT_FILE_LOCK:
            _ensure_constant_file(path)
            existing_symbols = {
                line.split("|", 1)[0].strip().upper()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            }
            yahoo_symbol = stock.yahoo_symbol.upper()
            if yahoo_symbol in existing_symbols:
                return
            line = "|".join(
                [
                    _escape_record_value(yahoo_symbol),
                    _escape_record_value(stock.symbol),
                    _escape_record_value(stock.exchange),
                    detected_at.isoformat(),
                    _escape_record_value(reason),
                ]
            )
            with path.open("a", encoding="utf-8") as file:
                file.write(f"{line}\n")
    except Exception:
        logger.exception("Failed writing delisted ticker to %s", path)


def mark_stock_delisted(db: Session, *, stock_id: int, reason: str | None) -> Stock | None:
    """Record Yahoo's delisted-style failure without removing the stock from sync.

    Yahoo often reports "possibly delisted" for transient no-data windows. Keep
    the failure metadata for diagnosis, but leave the ticker in the active
    universe so the next incremental run can try it again.
    """
    stock = db.get(Stock, stock_id)
    if stock is None:
        return None

    detected_at = datetime.now(UTC)
    stock.is_delisted = False
    stock.is_active = True
    stock.delisted_reason = reason
    stock.delisted_detected_at = detected_at
    db.flush()
    logger.warning(
        "Recorded Yahoo delisted-style failure without deactivating yahoo_symbol=%s stock_id=%s reason=%s",
        stock.yahoo_symbol,
        stock.id,
        reason,
    )
    return stock
