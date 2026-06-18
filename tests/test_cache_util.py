"""Tests for the shared caching primitives in ``app.utils.cache``.

Covers TTL fresh/stale/expiry classification and the single-flight
deduplication of concurrent identical calls (the thundering-herd guard).
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR / "backend"))

from app.utils.cache import EntryState, TTLCache, single_flight  # noqa: E402


class TTLCacheTests(unittest.TestCase):
    def test_fresh_then_stale_then_expired(self) -> None:
        cache: TTLCache[str] = TTLCache(fresh_ttl=0.05, stale_ttl=0.05)
        cache.set("k", "v")

        state, value = cache.get_with_state("k")
        self.assertEqual(state, EntryState.FRESH)
        self.assertEqual(value, "v")

        time.sleep(0.07)  # past fresh, within stale
        state, value = cache.get_with_state("k")
        self.assertEqual(state, EntryState.STALE)
        self.assertEqual(value, "v")

        time.sleep(0.06)  # past stale -> miss
        state, value = cache.get_with_state("k")
        self.assertEqual(state, EntryState.MISS)
        self.assertIsNone(value)

    def test_miss_on_absent_key(self) -> None:
        cache: TTLCache[int] = TTLCache(fresh_ttl=10)
        self.assertEqual(cache.get_with_state("nope"), (EntryState.MISS, None))
        self.assertIsNone(cache.get("nope"))

    def test_no_stale_window(self) -> None:
        cache: TTLCache[str] = TTLCache(fresh_ttl=0.02, stale_ttl=0.0)
        cache.set("k", "v")
        time.sleep(0.04)
        self.assertEqual(cache.get("k"), None)

    def test_invalidate_and_clear_and_len(self) -> None:
        cache: TTLCache[int] = TTLCache(fresh_ttl=10)
        cache.set("a", 1)
        cache.set("b", 2)
        self.assertEqual(len(cache), 2)
        cache.invalidate("a")
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), 2)
        cache.clear()
        self.assertEqual(len(cache), 0)

    def test_expired_entry_is_evicted(self) -> None:
        cache: TTLCache[str] = TTLCache(fresh_ttl=0.01, stale_ttl=0.0)
        cache.set("k", "v")
        time.sleep(0.03)
        cache.get("k")  # triggers eviction
        self.assertEqual(len(cache), 0)

    def test_rejects_negative_ttl(self) -> None:
        with self.assertRaises(ValueError):
            TTLCache(fresh_ttl=-1)


class SingleFlightTests(unittest.TestCase):
    def test_dedupes_concurrent_calls(self) -> None:
        sf = single_flight()
        calls = {"n": 0}
        calls_lock = threading.Lock()
        start = threading.Event()

        def slow() -> int:
            with calls_lock:
                calls["n"] += 1
            time.sleep(0.1)
            return 42

        results: list[int] = []
        results_lock = threading.Lock()

        def worker() -> None:
            start.wait()
            r = sf.do("key", slow)
            with results_lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        start.set()  # release all at once
        for t in threads:
            t.join()

        self.assertEqual(results, [42] * 20)   # everyone got the result
        self.assertEqual(calls["n"], 1)         # but slow() ran exactly once

    def test_different_keys_run_independently(self) -> None:
        sf = single_flight()
        ran: list[str] = []
        ran_lock = threading.Lock()

        def make(k: str):
            def fn() -> str:
                with ran_lock:
                    ran.append(k)
                return k.upper()
            return fn

        self.assertEqual(sf.do("a", make("a")), "A")
        self.assertEqual(sf.do("b", make("b")), "B")
        self.assertEqual(sorted(ran), ["a", "b"])

    def test_exception_propagates_to_all_waiters(self) -> None:
        sf = single_flight()
        start = threading.Event()
        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def boom() -> int:
            time.sleep(0.05)
            raise RuntimeError("kaboom")

        def worker() -> None:
            start.wait()
            try:
                sf.do("k", boom)
            except BaseException as exc:  # noqa: BLE001
                with errors_lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 5)
        self.assertTrue(all(isinstance(e, RuntimeError) for e in errors))

    def test_subsequent_call_after_completion_reruns(self) -> None:
        sf = single_flight()
        count = {"n": 0}

        def fn() -> int:
            count["n"] += 1
            return count["n"]

        self.assertEqual(sf.do("k", fn), 1)
        self.assertEqual(sf.do("k", fn), 2)  # not cached — single-flight only dedupes *concurrent* work


if __name__ == "__main__":
    unittest.main()
