"""
Refresh precomputed analytics tables used by Explore (performance + rankings).

Run after price ingestion batches:

    python scripts/refresh_analytics.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.append(str(BACKEND))

from app.database import SessionLocal  # noqa: E402
from app.services.analytics_refresh_service import refresh_all_analytics  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    with SessionLocal() as db:
        result = refresh_all_analytics(db)
    logger.info("Analytics refresh complete: %s", result)


if __name__ == "__main__":
    main()
