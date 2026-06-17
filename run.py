#!/usr/bin/env python3
"""Start the paper trading app (delegates to scripts/run.py)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "scripts" / "run.py"

if __name__ == "__main__":
    raise SystemExit(subprocess.call([sys.executable, str(SCRIPT), *sys.argv[1:]]))
