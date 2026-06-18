"""
Start or stop the paper trading app.

Default startup starts PostgreSQL + FastAPI web UI only (Streamlit is dormant).

Examples:
  py -3 scripts/run.py              # Docker: build + start web UI (foreground)
  py -3 scripts/run.py -d           # Docker: detached background
  py -3 run.py -d                   # Same from repo root
  py -3 scripts/run.py --with-streamlit -d   # Also start legacy Streamlit
  py -3 scripts/run.py --legacy -d           # Alias for --with-streamlit
  py -3 scripts/run.py stop         # Stop containers
  py -3 scripts/run.py status       # Show container status
  py -3 scripts/run.py logs         # Follow backend logs
  py -3 scripts/run.py logs --with-streamlit # Include Streamlit logs
  py -3 scripts/run.py check        # Verify Docker + Python setup
  py -3 scripts/run.py load-index-funds
  py -3 scripts/run.py ingest-index-funds --limit 3
  py -3 scripts/run.py index-funds  # Load CSV + ingest prices
  py -3 scripts/run.py load-index-memberships
  py -3 scripts/run.py download-bhavcopy --bhavcopy-years 3
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.yml"
DEFAULT_SERVICES = ("postgres", "backend")
WEB_UI_URL = "http://localhost:8000/web/explore"
OLLAMA_URL = "http://localhost:11434/api/tags"
DOCKER_DESKTOP_PATHS = [
    Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe"),
    Path(os.environ.get("ProgramFiles", "")) / "Docker" / "Docker" / "Docker Desktop.exe",
]


def _ollama_running() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA_URL, timeout=3) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def ensure_ollama_running() -> None:
    """Start Ollama in the background if it isn't already running."""
    if _ollama_running():
        print("[OK] Ollama is already running.")
        return

    if not shutil.which("ollama"):
        print("[--] Ollama not found — AI features (Stock Brief) will be disabled.")
        print("     Install from https://ollama.com and run: ollama pull qwen3:14b")
        return

    print("Starting Ollama...", end=" ", flush=True)
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(15):
        time.sleep(1)
        if _ollama_running():
            print("ready.")
            return
    print("still loading (may take a moment).")


def _require_docker_cli() -> None:
    if not shutil.which("docker"):
        print("Error: docker is not installed or not on PATH.", file=sys.stderr)
        print("Install Docker Desktop: https://www.docker.com/products/docker-desktop/", file=sys.stderr)
        sys.exit(1)
    probe = subprocess.run(
        ["docker", "compose", "version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        print("Error: docker compose is not available.", file=sys.stderr)
        sys.exit(1)


def docker_daemon_running() -> bool:
    result = subprocess.run(
        ["docker", "info"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def try_start_docker_desktop() -> bool:
    for path in DOCKER_DESKTOP_PATHS:
        if path.exists():
            print(f"Starting Docker Desktop: {path}")
            subprocess.Popen(
                [str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
    return False


def require_docker_daemon(*, wait_seconds: int = 0) -> None:
    _require_docker_cli()
    if docker_daemon_running():
        return

    print("Error: Docker is installed but Docker Desktop is not running.", file=sys.stderr)
    print(file=sys.stderr)
    if sys.platform == "win32":
        started = try_start_docker_desktop()
        if started:
            print("Docker Desktop is starting. Wait until the whale icon shows 'Engine running'.", file=sys.stderr)
            if wait_seconds > 0:
                print(f"Waiting up to {wait_seconds} seconds...", file=sys.stderr)
                for _ in range(wait_seconds):
                    time.sleep(1)
                    if docker_daemon_running():
                        print("Docker is ready.", file=sys.stderr)
                        return
                print("Docker is still not ready. Try again in a minute.", file=sys.stderr)
        else:
            print("Start Docker Desktop manually from the Windows Start menu.", file=sys.stderr)
    else:
        print("Start the Docker daemon, then run this script again.", file=sys.stderr)
    print(file=sys.stderr)
    print("Then run:", file=sys.stderr)
    print(f'  py -3 scripts/run.py{" -d" if wait_seconds else ""}', file=sys.stderr)
    sys.exit(1)


def _compose(cmd: list[str], *, check: bool = True, profile_legacy: bool = False) -> int:
    require_docker_daemon()
    full = ["docker", "compose"]
    if profile_legacy:
        full.extend(["--profile", "legacy"])
    full.extend(cmd)
    print(f">>> {' '.join(full)}")
    print(f"    (from {ROOT})")
    result = subprocess.run(full, cwd=ROOT)
    if check and result.returncode != 0:
        sys.exit(result.returncode)
    return result.returncode


def _backend_health_url() -> str:
    return "http://localhost:8000/health"


def _backend_is_healthy() -> bool:
    try:
        with urllib.request.urlopen(_backend_health_url(), timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError):
        return False


def _wait_for_backend_health(*, timeout_seconds: int = 90) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _backend_is_healthy():
            return True
        time.sleep(2)
    return False


def _print_backend_startup_help() -> None:
    print("Error: Backend API is not responding on http://localhost:8000", file=sys.stderr)
    print(file=sys.stderr)
    print("The frontend shows 'API request failed' when this happens.", file=sys.stderr)
    print("Check backend logs:", file=sys.stderr)
    print("  py -3 scripts/run.py logs", file=sys.stderr)
    print("Or rebuild and restart:", file=sys.stderr)
    print("  py -3 scripts/run.py stop", file=sys.stderr)
    print("  py -3 scripts/run.py -d", file=sys.stderr)


def _ensure_constant_txt() -> None:
    """constant.txt is volume-mounted into the container; create it if absent."""
    path = ROOT / "constant.txt"
    if not path.exists():
        path.touch()


def run_docker(
    *,
    detached: bool,
    build: bool,
    wait_docker: int,
    with_streamlit: bool,
) -> None:
    if not COMPOSE_FILE.exists():
        print(f"Error: missing {COMPOSE_FILE}", file=sys.stderr)
        sys.exit(1)

    _ensure_constant_txt()
    require_docker_daemon(wait_seconds=wait_docker)
    ensure_ollama_running()

    cmd = ["up"]
    if build:
        cmd.append("--build")
    if detached:
        cmd.append("-d")
    if with_streamlit:
        cmd.extend([*DEFAULT_SERVICES, "streamlit"])
    else:
        cmd.extend(DEFAULT_SERVICES)

    _compose(cmd, profile_legacy=with_streamlit)
    if detached:
        print()
        print("Waiting for backend API...")
        if not _wait_for_backend_health():
            _print_backend_startup_help()
            sys.exit(1)
        print("Backend API is healthy.")
        print()
        print("App is running in the background.")
        _print_urls(with_streamlit=with_streamlit)
        _maybe_open_web_ui()


def stop() -> None:
    _compose(["down"])


def status() -> None:
    _compose(["ps"], check=False)
    print()
    if _backend_is_healthy():
        print("[OK] Backend API: http://localhost:8000/health")
    else:
        print("[FAIL] Backend API is not responding on http://localhost:8000")
        print("       Frontend will show 'API request failed' until backend is up.")
        print("       Run: py -3 scripts/run.py logs")


def logs(follow: bool, *, with_streamlit: bool) -> None:
    services = ["backend"]
    if with_streamlit:
        services.append("streamlit")
    cmd = ["logs", *services]
    if follow:
        cmd.append("-f")
    _compose(cmd, check=False, profile_legacy=with_streamlit)


def migrate() -> None:
    _compose(["run", "--rm", "backend", "alembic", "upgrade", "head"])


def load_index_funds(csv_path: str) -> None:
    migrate()
    _compose(
        [
            "run",
            "--rm",
            "backend",
            "python",
            "/app/scripts/load_index_funds.py",
            "--csv-path",
            csv_path,
        ]
    )


def load_index_memberships(
    *,
    source: str,
    csv_path: str,
    effective_date: str | None,
    deactivate_missing: bool,
) -> None:
    migrate()
    cmd = [
        "run",
        "--rm",
        "backend",
        "python",
        "/app/scripts/load_index_memberships.py",
        "--source",
        source,
        "--csv-path",
        csv_path,
    ]
    if effective_date:
        cmd.extend(["--effective-date", effective_date])
    if deactivate_missing:
        cmd.append("--deactivate-missing")
    _compose(cmd)


def ingest_index_funds(
    *,
    start_date: str,
    end_date: str | None,
    category: str | None,
    limit: int | None,
    offset: int,
    chunk_days: int,
    sleep_seconds: float,
    incremental: bool,
    quiet_progress: bool,
) -> None:
    migrate()
    cmd = [
        "run",
        "--rm",
        "backend",
        "python",
        "/app/scripts/ingest_index_funds.py",
        "--chunk-days",
        str(chunk_days),
        "--sleep-seconds",
        str(sleep_seconds),
        "--offset",
        str(offset),
    ]
    if incremental:
        cmd.append("--incremental")
    else:
        cmd.extend(["--start-date", start_date])
    if end_date:
        cmd.extend(["--end-date", end_date])
    if category:
        cmd.extend(["--category", category])
    if limit is not None:
        cmd.extend(["--limit", str(limit)])
    if quiet_progress:
        cmd.append("--quiet-progress")
    _compose(cmd)


def setup_index_funds(args: argparse.Namespace) -> None:
    load_index_funds(args.index_csv_path)
    ingest_index_funds(
        start_date=args.start_date,
        end_date=args.end_date,
        category=args.category,
        limit=args.limit,
        offset=args.offset,
        chunk_days=args.chunk_days,
        sleep_seconds=args.sleep_seconds,
        incremental=args.incremental,
        quiet_progress=args.quiet_progress,
    )


def download_bhavcopy(args: argparse.Namespace) -> None:
    cmd = [
        "run",
        "--rm",
        "backend",
        "python",
        "/app/scripts/download_bhavcopy.py",
        "--exchange",
        args.bhavcopy_exchange,
        "--years",
        str(args.bhavcopy_years),
        "--sleep-seconds",
        str(args.bhavcopy_sleep_seconds),
        "--timeout",
        str(args.bhavcopy_timeout),
    ]
    if args.bhavcopy_start_date:
        cmd.extend(["--start-date", args.bhavcopy_start_date])
    if args.bhavcopy_end_date:
        cmd.extend(["--end-date", args.bhavcopy_end_date])
    if args.dry_run:
        cmd.append("--dry-run")
    _compose(cmd)


def check_setup() -> None:
    print("=== Paper Trading App — setup check ===\n")
    ok = True

    python_cmd = shutil.which("python") or shutil.which("py")
    if python_cmd:
        print(f"[OK] Python launcher: {python_cmd}")
    else:
        print("[FAIL] Python not found. Install Python 3.11+ or use 'py -3'.")
        ok = False

    if shutil.which("docker"):
        print("[OK] docker CLI found")
    else:
        print("[FAIL] docker CLI not found")
        ok = False
        print("\nFix: Install Docker Desktop, then run: py -3 scripts/run.py")
        sys.exit(1)

    if docker_daemon_running():
        print("[OK] Docker daemon is running")
    else:
        print("[FAIL] Docker Desktop is not running")
        ok = False
        if sys.platform == "win32":
            print("     Fix: Open 'Docker Desktop' from Start menu and wait for 'Engine running'.")

    backend_venv = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
    if backend_venv.exists():
        print(f"[OK] Backend venv: {backend_venv}")
    else:
        print("[--] Backend venv not found (only needed for local dev without Docker)")

    print()
    if ok:
        print("Ready. Start the web UI with:")
        print("  py -3 run.py -d")
        print("  py -3 scripts/run.py -d")
        print("  .\\run.ps1 -d")
        print()
        print("Optional index fund setup:")
        print("  py -3 scripts/run.py index-funds --limit 3")
        print("  py -3 scripts/run.py index-funds")
        print("  py -3 scripts/run.py load-index-memberships")
        print()
        _print_urls()
    else:
        print("Fix the issues above, then run:")
        print("  py -3 scripts/run.py -d")
        sys.exit(1)


def _maybe_open_web_ui() -> None:
    try:
        import webbrowser

        webbrowser.open(WEB_UI_URL)
    except Exception:
        pass


def _print_urls(*, with_streamlit: bool = False) -> None:
    print("  Web UI (default):", WEB_UI_URL)
    print("  FastAPI root     : http://localhost:8000")
    print("  API docs         : http://localhost:8000/docs")
    if with_streamlit:
        print("  Legacy Streamlit : http://localhost:8501")
    else:
        print("  Legacy Streamlit : dormant (Enable Legacy Mode in sidebar, or run with --with-streamlit)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the paper trading app (Docker Compose).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="up",
        choices=[
            "up",
            "stop",
            "status",
            "logs",
            "check",
            "migrate",
            "load-index-funds",
            "load-index-memberships",
            "ingest-index-funds",
            "index-funds",
            "download-bhavcopy",
        ],
        help=(
            "up=start (default), stop, status, logs, check, migrate, "
            "load-index-funds, load-index-memberships, ingest-index-funds, "
            "index-funds, download-bhavcopy"
        ),
    )
    parser.add_argument(
        "-d",
        "--detach",
        action="store_true",
        help="Run containers in the background (only for 'up').",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip image rebuild on start.",
    )
    parser.add_argument(
        "--wait-docker",
        type=int,
        default=90,
        metavar="SEC",
        help="After launching Docker Desktop on Windows, wait up to SEC seconds (default: 90).",
    )
    parser.add_argument(
        "--index-csv-path",
        default="/app/data/indexes_commodities_prepared.csv",
        help="Container path to the index/commodity CSV for load-index-funds/index-funds.",
    )
    parser.add_argument(
        "--membership-source",
        choices=["online", "csv"],
        default="online",
        help="Source for load-index-memberships (default: online with CSV fallback).",
    )
    parser.add_argument(
        "--membership-csv-path",
        default="/app/data/index_constituents_sample.csv",
        help="Container path to fallback/local index constituent CSV.",
    )
    parser.add_argument(
        "--effective-date",
        help="Optional YYYY-MM-DD date for index constituent membership snapshot.",
    )
    parser.add_argument(
        "--deactivate-missing",
        action="store_true",
        help="Deactivate old memberships not present in online full snapshot.",
    )
    parser.add_argument(
        "--start-date",
        default="2010-01-01",
        help="Index price ingestion start date, YYYY-MM-DD (default: 2010-01-01).",
    )
    parser.add_argument(
        "--end-date",
        help="Index price ingestion end date, YYYY-MM-DD (default: previous business day).",
    )
    parser.add_argument(
        "--category",
        choices=["index", "commodity"],
        help="Limit index fund ingestion to one category.",
    )
    parser.add_argument("--limit", type=int, help="Limit index fund ingestion rows for testing.")
    parser.add_argument("--offset", type=int, default=0, help="Skip N index fund rows during ingestion.")
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=365,
        help="Index price ingestion chunk size in calendar days (default: 365).",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=1.0,
        help="Pause between Yahoo requests during index ingestion (default: 1.0).",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="For index ingestion, fetch from last stored candle + 1.",
    )
    parser.add_argument(
        "--quiet-progress",
        action="store_true",
        help="Hide per-chunk progress for index ingestion.",
    )
    parser.add_argument(
        "--with-streamlit",
        "--legacy",
        action="store_true",
        dest="with_streamlit",
        help="Also start legacy Streamlit UI (docker compose --profile legacy).",
    )
    parser.add_argument(
        "--bhavcopy-exchange",
        choices=["NSE", "BSE", "ALL"],
        default="ALL",
        help="Exchange for download-bhavcopy (default: ALL).",
    )
    parser.add_argument(
        "--bhavcopy-years",
        type=int,
        default=3,
        help="Lookback years for download-bhavcopy when no start date is supplied.",
    )
    parser.add_argument("--bhavcopy-start-date", help="YYYY-MM-DD start date for download-bhavcopy.")
    parser.add_argument("--bhavcopy-end-date", help="YYYY-MM-DD end date for download-bhavcopy.")
    parser.add_argument(
        "--bhavcopy-sleep-seconds",
        type=float,
        default=0.25,
        help="Pause between bhavcopy requests (default: 0.25).",
    )
    parser.add_argument(
        "--bhavcopy-timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds for bhavcopy downloads.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="For download-bhavcopy, print planned requests without downloading.",
    )
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.offset < 0:
        parser.error("--offset must be zero or greater")
    if args.chunk_days < 1:
        parser.error("--chunk-days must be at least 1")
    if args.sleep_seconds < 0:
        parser.error("--sleep-seconds cannot be negative")
    if args.bhavcopy_years < 1:
        parser.error("--bhavcopy-years must be at least 1")
    if args.bhavcopy_sleep_seconds < 0:
        parser.error("--bhavcopy-sleep-seconds cannot be negative")

    if args.command == "check":
        check_setup()
        return
    if args.command == "migrate":
        migrate()
        print("Migrations applied.")
        return
    if args.command == "load-index-funds":
        load_index_funds(args.index_csv_path)
        print("Index fund universe loaded.")
        return
    if args.command == "load-index-memberships":
        load_index_memberships(
            source=args.membership_source,
            csv_path=args.membership_csv_path,
            effective_date=args.effective_date,
            deactivate_missing=args.deactivate_missing,
        )
        print("Index memberships loaded.")
        return
    if args.command == "ingest-index-funds":
        ingest_index_funds(
            start_date=args.start_date,
            end_date=args.end_date,
            category=args.category,
            limit=args.limit,
            offset=args.offset,
            chunk_days=args.chunk_days,
            sleep_seconds=args.sleep_seconds,
            incremental=args.incremental,
            quiet_progress=args.quiet_progress,
        )
        print("Index fund price ingestion finished.")
        return
    if args.command == "index-funds":
        setup_index_funds(args)
        print("Index fund setup finished.")
        return
    if args.command == "download-bhavcopy":
        download_bhavcopy(args)
        print("Bhavcopy download finished.")
        return

    if args.command == "up":
        run_docker(
            detached=args.detach,
            build=not args.no_build,
            wait_docker=args.wait_docker if sys.platform == "win32" else 0,
            with_streamlit=args.with_streamlit,
        )
        if not args.detach:
            print()
            print("Press Ctrl+C to stop.")
            _print_urls(with_streamlit=args.with_streamlit)
            print()
            print("Open the web UI:", WEB_UI_URL)
        else:
            pass
    elif args.command == "stop":
        stop()
        print("Stopped.")
    elif args.command == "status":
        status()
    elif args.command == "logs":
        logs(follow=True, with_streamlit=args.with_streamlit)


if __name__ == "__main__":
    main()
