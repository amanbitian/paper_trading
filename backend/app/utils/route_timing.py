"""In-memory per-route timing accumulator.

Populated by the HTTP middleware in main.py.  Survives only until container
restart — that's fine for a live monitoring panel.
"""
from __future__ import annotations

from threading import Lock
from typing import Any

_lock = Lock()
_stats: dict[str, list[float]] = {}   # route → list of duration_ms


def record(route: str, duration_ms: float) -> None:
    with _lock:
        if route not in _stats:
            _stats[route] = []
        _stats[route].append(duration_ms)


def get_all() -> list[dict[str, Any]]:
    with _lock:
        rows = []
        for route in sorted(_stats):
            samples = _stats[route]
            if not samples:
                continue
            rows.append(
                {
                    "route": route,
                    "calls": len(samples),
                    "avg_ms": round(sum(samples) / len(samples), 1),
                    "min_ms": round(min(samples), 1),
                    "max_ms": round(max(samples), 1),
                }
            )
        return rows
