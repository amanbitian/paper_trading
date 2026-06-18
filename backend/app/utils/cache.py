"""Shared in-process caching primitives.

This module consolidates the ad-hoc ``_CACHE`` dicts and per-service refresh
locks that grew up across ``app/services`` into two small, well-tested building
blocks:

- :class:`TTLCache` — a thread-safe key/value cache with a *fresh* and a
  *stale* window. Fresh entries are served directly; stale entries can be
  served immediately while a caller refreshes them in the background
  (stale-while-revalidate); expired entries are dropped.
- :func:`single_flight` — deduplicates concurrent identical expensive calls
  (the "thundering herd" problem). When N requests miss the cache for the same
  key at the same time, only one does the slow work (e.g. a yfinance download);
  the rest wait for and share that single result.

Design notes
------------
- Timekeeping uses :func:`time.monotonic`, which is immune to wall-clock jumps
  and avoids any ``datetime`` timezone concerns — appropriate for TTLs.
- The implementation is intentionally backend-agnostic. The public surface
  (``get`` / ``set`` / ``get_with_state``) maps cleanly onto Redis later
  (GET/SETEX), so migrating to a shared cache for multi-worker deployments is a
  drop-in swap rather than a rewrite. See ``OPTIMIZATION.md``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Generic, Hashable, Optional, Tuple, TypeVar


T = TypeVar("T")


class EntryState(str, Enum):
    """Freshness of a cache lookup."""

    MISS = "miss"      # not present (or fully expired)
    FRESH = "fresh"    # within the fresh window — serve directly
    STALE = "stale"    # within the stale window — serve, but refresh soon


@dataclass(frozen=True)
class _Entry(Generic[T]):
    value: T
    stored_at: float  # time.monotonic() seconds


class TTLCache(Generic[T]):
    """Thread-safe TTL cache with a fresh window and an optional stale window.

    Parameters
    ----------
    fresh_ttl:
        Seconds an entry is considered fresh and served without any refresh.
    stale_ttl:
        Seconds beyond *fresh_ttl* an entry may still be served while a refresh
        is triggered. Set to ``0`` to disable the stale window (entries become
        a MISS the moment they stop being fresh).
    """

    def __init__(self, fresh_ttl: float, stale_ttl: float = 0.0) -> None:
        if fresh_ttl < 0 or stale_ttl < 0:
            raise ValueError("TTL values must be non-negative")
        self._fresh_ttl = float(fresh_ttl)
        self._stale_ttl = float(stale_ttl)
        self._store: dict[Hashable, _Entry[T]] = {}
        self._lock = threading.RLock()

    def _classify(self, entry: _Entry[T], now: float) -> EntryState:
        age = now - entry.stored_at
        if age <= self._fresh_ttl:
            return EntryState.FRESH
        if age <= self._fresh_ttl + self._stale_ttl:
            return EntryState.STALE
        return EntryState.MISS

    def get_with_state(self, key: Hashable) -> Tuple[EntryState, Optional[T]]:
        """Return ``(state, value)``. Value is ``None`` only on MISS."""
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return EntryState.MISS, None
            state = self._classify(entry, now)
            if state is EntryState.MISS:
                # Fully expired — evict so the dict doesn't grow unbounded.
                self._store.pop(key, None)
                return EntryState.MISS, None
            return state, entry.value

    def get(self, key: Hashable) -> Optional[T]:
        """Return the value if fresh or stale, else ``None``."""
        _, value = self.get_with_state(key)
        return value

    def set(self, key: Hashable, value: T) -> None:
        with self._lock:
            self._store[key] = _Entry(value=value, stored_at=time.monotonic())

    def invalidate(self, key: Hashable) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


class _SingleFlight:
    """Coordinates so only one thread computes a given key at a time.

    Other threads that ask for the same key while it is in flight block on a
    per-key event and then receive the same result (or the same exception).
    """

    @dataclass
    class _Call:
        event: threading.Event
        value: Any = None
        error: Optional[BaseException] = None

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_flight: dict[Hashable, "_SingleFlight._Call"] = {}

    def do(self, key: Hashable, fn: Callable[[], T]) -> T:
        with self._lock:
            existing = self._in_flight.get(key)
            if existing is not None:
                leader = False
                call = existing
            else:
                leader = True
                call = self._Call(event=threading.Event())
                self._in_flight[key] = call

        if not leader:
            call.event.wait()
            if call.error is not None:
                raise call.error
            return call.value  # type: ignore[return-value]

        try:
            call.value = fn()
            return call.value
        except BaseException as exc:  # propagate to all waiters
            call.error = exc
            raise
        finally:
            with self._lock:
                self._in_flight.pop(key, None)
            call.event.set()


def single_flight() -> _SingleFlight:
    """Return a new single-flight coordinator.

    Usage::

        _prices_sf = single_flight()

        def load_prices(stock_id: int) -> pd.DataFrame:
            return _prices_sf.do(stock_id, lambda: _expensive_download(stock_id))
    """
    return _SingleFlight()


__all__ = ["TTLCache", "EntryState", "single_flight"]
