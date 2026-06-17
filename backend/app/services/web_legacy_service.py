from __future__ import annotations

import logging
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent


def _in_docker() -> bool:
    return Path("/.dockerenv").exists()


def _compose_root() -> Path:
    override = (settings.legacy_compose_root or "").strip()
    if override:
        return Path(override)
    return PROJECT_ROOT


def legacy_streamlit_reachable(url: str | None = None, *, timeout: float = 2.0) -> bool:
    target = (url or settings.legacy_streamlit_url).rstrip("/")
    try:
        with urllib.request.urlopen(f"{target}/_stcore/health", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        try:
            with urllib.request.urlopen(target, timeout=timeout) as response:
                return response.status < 500
        except Exception:
            return False


def _docker_compose_start_streamlit() -> tuple[bool, str]:
    if not settings.enable_legacy_start:
        return False, "Legacy auto-start is disabled (ENABLE_LEGACY_START=false)."
    if _in_docker():
        return False, "Backend is running inside Docker and cannot start Streamlit on the host."
    if not shutil.which("docker"):
        return False, "Docker CLI is not available on PATH."

    compose_file = _compose_root() / "docker-compose.yml"
    if not compose_file.exists():
        return False, f"Missing compose file: {compose_file}"

    cmd = [
        "docker",
        "compose",
        "--profile",
        "legacy",
        "up",
        "-d",
        "streamlit",
    ]
    logger.info("legacy.start cmd=%s cwd=%s", " ".join(cmd), _compose_root())
    result = subprocess.run(
        cmd,
        cwd=_compose_root(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "docker compose failed").strip()
        logger.warning("legacy.start_failed detail=%s", detail[:500])
        return False, detail

    deadline = time.time() + 45
    while time.time() < deadline:
        if legacy_streamlit_reachable():
            return True, "Streamlit container started."
        time.sleep(2)
    return False, "Streamlit start command ran but the UI is not reachable yet."


def start_legacy_mode() -> dict[str, Any]:
    url = settings.legacy_streamlit_url.rstrip("/")
    command = settings.legacy_start_command

    if legacy_streamlit_reachable(url):
        logger.info("legacy.already_running url=%s", url)
        return {
            "ok": True,
            "url": url,
            "message": "Legacy Streamlit UI is already running.",
            "already_running": True,
            "command": None,
        }

    started, detail = _docker_compose_start_streamlit()
    if started and legacy_streamlit_reachable(url):
        logger.info("legacy.started url=%s", url)
        return {
            "ok": True,
            "url": url,
            "message": "Legacy Streamlit UI started.",
            "already_running": False,
            "command": command,
        }

    logger.warning("legacy.unavailable url=%s detail=%s", url, detail)
    return {
        "ok": False,
        "url": url,
        "message": "Legacy Streamlit is not running.",
        "detail": detail,
        "command": command,
        "already_running": False,
    }
