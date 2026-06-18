"""In-memory per-route timing accumulator for the Data page.

Populated by the HTTP middleware in main.py. Survives only until container
restart, which is enough for a live performance panel.
"""
from __future__ import annotations

from collections import deque
from threading import Lock
from typing import Any

_lock = Lock()
_MAX_SAMPLES_PER_ROUTE = 500
_SLOW_CALL_MS = 1000.0
_stats: dict[str, dict[str, Any]] = {}


def _new_bucket() -> dict[str, Any]:
    return {
        "samples": deque(maxlen=_MAX_SAMPLES_PER_ROUTE),
        "calls": 0,
        "total_ms": 0.0,
        "min_ms": None,
        "max_ms": None,
        "last_ms": None,
        "slow_calls": 0,
    }


def _percentile(samples: list[float], percentile: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def record(route: str, duration_ms: float) -> None:
    duration = float(duration_ms)
    with _lock:
        bucket = _stats.setdefault(route, _new_bucket())
        bucket["samples"].append(duration)
        bucket["calls"] += 1
        bucket["total_ms"] += duration
        bucket["min_ms"] = duration if bucket["min_ms"] is None else min(bucket["min_ms"], duration)
        bucket["max_ms"] = duration if bucket["max_ms"] is None else max(bucket["max_ms"], duration)
        bucket["last_ms"] = duration
        if duration >= _SLOW_CALL_MS:
            bucket["slow_calls"] += 1


def get_all(*, limit: int | None = None) -> list[dict[str, Any]]:
    with _lock:
        rows = []
        for route, bucket in _stats.items():
            samples = list(bucket["samples"])
            if not samples:
                continue
            calls = int(bucket["calls"] or 0)
            avg_ms = (float(bucket["total_ms"] or 0.0) / calls) if calls else 0.0
            p95_ms = _percentile(samples, 0.95)
            rows.append(
                {
                    "route": route,
                    "calls": calls,
                    "sample_count": len(samples),
                    "avg_ms": round(avg_ms, 1),
                    "p95_ms": round(p95_ms, 1) if p95_ms is not None else None,
                    "min_ms": round(float(bucket["min_ms"]), 1) if bucket["min_ms"] is not None else None,
                    "max_ms": round(float(bucket["max_ms"]), 1) if bucket["max_ms"] is not None else None,
                    "last_ms": round(float(bucket["last_ms"]), 1) if bucket["last_ms"] is not None else None,
                    "slow_calls": int(bucket["slow_calls"] or 0),
                }
            )
        rows.sort(key=lambda row: (row["p95_ms"] or row["avg_ms"], row["max_ms"] or 0), reverse=True)
        return rows[:limit] if limit else rows
