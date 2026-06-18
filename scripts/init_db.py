from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.append(str(BACKEND))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402


def main() -> None:
    alembic_cfg = Config(str(BACKEND / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(alembic_cfg, "head")
    print("Database migrations applied.")


if __name__ == "__main__":
    main()
