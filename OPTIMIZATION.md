# API & System-Design Optimization Plan

A concrete, code-grounded plan for making the slow endpoints fast and the system
production-grade. Findings reference specific files so each item is actionable.

## TL;DR — the root cause of slow APIs

The codebase follows a consistent pattern: **"read from Postgres; on a cache/DB
miss, fall back to a *blocking* yfinance/requests call inside the request."**
That design is fine on the warm path (single indexed query, tens of ms) but
produces the slow requests you're seeing on **cold paths** and under
**concurrency**:

1. **Blocking external I/O on the request thread.** ~106 `yfinance`/`requests`
   call sites; the ones that can fire mid-request are the slow ones — e.g.
   `backtest_service._load_prices_for_range` does a **5-year** `sync_stock_prices`
   download when a stock has no cached prices (`backtest_service.py:78`), and
   `strategy_service` (`:219`) syncs on signal preview. `market_data_service`
   uses per-call timeouts up to 30s.
2. **No de-duplication of concurrent identical fetches ("thundering herd").**
   If five users backtest the same un-synced stock at once, five identical 5-year
   downloads run in parallel, each tying up a worker thread.
3. **Per-process, per-service in-memory caches.** At least 10 services keep their
   own `_CACHE` dict / `lru_cache`. These are lost on every restart and **not
   shared across workers**, so each uvicorn worker re-warms independently and a
   restart means a cold, slow first hit for every endpoint.
4. **185 of 204 routes are sync `def`**, so they run in FastAPI's bounded
   threadpool. A handful of slow blocking calls can saturate the pool and make
   *unrelated* endpoints feel slow.

The good news: `market_overview_service` already shows the right pattern
(fresh/stale/cold TTL cache, DB-first quotes, a <200 ms cold path, batched
queries, and a single-flight refresh lock). The work below generalizes that
pattern everywhere and removes the remaining blocking calls from request paths.

---

## Phase 1 — Stop blocking the request thread (biggest win)

**Principle:** read endpoints serve *only* from Postgres. All yfinance/requests
fetching happens in background sync jobs (you already do this for bhavcopy and
news). A read should never be able to trigger a 5-year download.

Concrete changes:

- **Backtest cold path** (`backtest_service.py:62-94`): if a stock has no price
  rows for the requested range, return `409 Conflict` / a "syncing…" response
  and enqueue a background sync, instead of doing the 5-year download inline.
  For an interactive backtest, prefer pre-syncing on stock selection.
- **Strategy preview** (`strategy_service.py:219`) and **paper-order placement**
  (`paper_trading_service.py:277`): same treatment — the 5-day sync on order
  placement is small, but it should still be guarded by single-flight (below).
- **Add a shared single-flight guard** so concurrent cold-fetches for the same
  symbol collapse into one. A tested utility now exists at
  `backend/app/utils/cache.py` (`single_flight()`), validated by
  `tests/test_cache_util.py`. Wiring sketch for the backtest path:

  ```python
  from app.utils.cache import single_flight

  _price_sync_sf = single_flight()

  def _ensure_prices_synced(stock_id: int) -> None:
      # Leader runs the sync + COMMIT; concurrent callers wait and then
      # re-query their own session. NOTE: the cold-sync must commit=True here
      # so waiters (different sessions) can see the rows.
      _price_sync_sf.do(
          ("price_sync_5y", stock_id),
          lambda: _sync_prices_committing(stock_id),
      )
  ```

  (Validate this with the full test suite on Python 3.11 before shipping — the
  commit semantics across sessions are the one subtlety.)

Expected impact: cold backtest/strategy requests drop from seconds (or a 30s
timeout) to a fast DB read or an immediate "syncing" response; one slow fetch can
no longer be triggered N times concurrently.

## Phase 2 — One shared cache, Redis-ready

- **Consolidate the scattered `_CACHE` dicts** onto `app.utils.cache.TTLCache`
  (fresh + stale-while-revalidate windows, thread-safe, monotonic-clock TTLs).
  This removes duplicated cache logic across `market_overview_service`,
  `market_movers_service`, `fundamentals_service`, `news_service`,
  `stock_brief_service`, `market_sync_service`, etc.
- **Introduce Redis** behind the same `get`/`set`/`get_with_state` surface so the
  swap is mechanical. Benefits: cache survives restarts, is shared across
  workers, and supports the LLM cache and movers cache too. Keep the in-process
  `TTLCache` as an L1 in front of Redis (L2) for hot keys.
- **Add HTTP caching to read endpoints:** `Cache-Control` + `ETag` on
  `/web/...` GET partials and JSON reads. You already cache `/static/*`
  (`main.py` timing middleware); extend the same idea to data reads so the
  browser/CDN can skip round-trips entirely.

## Phase 3 — Precompute expensive aggregates

You already use this pattern well (`stock_fundamentals_latest`,
`analytics_refresh_service`, `market_movers` cache). Extend it:

- **A denormalized "stock snapshot" table** (latest close, 1D/1W/1M/1Y returns,
  volume, key indicators), refreshed by the sync job after each bhavcopy load.
  Then Explore / Trends / Movers / NL-screener read one indexed table instead of
  recomputing across `stock_prices`.
- **Push Python-side sorting into SQL.** `market_trends_service` builds result
  sets and sorts/slices in Python; move ranking to `ORDER BY ... LIMIT` against
  the snapshot table (and a covering index) so the DB returns only the rows
  needed.
- **Profile the real top-N slow routes first.** You already record per-route
  timings in memory (the Data dashboard / `utils/route_timing.py`). Persist that
  to a table or add an APM, then optimize by measured cost rather than guesswork.
  Use `EXPLAIN (ANALYZE, BUFFERS)` on the hot queries.

## Phase 4 — Move background work out of the web process

`main.py`'s lifespan starts daemon `threading.Thread`s for news sync, cache
warming, the price index build, and the strategy-explainer refresh. These
compete with request handlers for CPU/GIL, die on restart, and don't scale past
one process. Move them to a real scheduler/worker:

- Lightweight: **APScheduler** in a separate process.
- Scalable: **Celery/RQ + beat** with Redis as broker (you'll already have Redis
  from Phase 2).

This frees the request path and makes the jobs observable, retryable, and
restart-safe.

## Phase 5 — Connections & scale

- `database.py` uses `pool_size=20, max_overflow=40` (up to 60 connections per
  process). With multiple workers this can exceed Postgres limits — front the DB
  with **PgBouncer** (transaction pooling) before scaling workers.
- Add **pagination** to list endpoints that can return large sets (explore,
  trends, search) rather than capping with large `limit` defaults.
- Consider converting pure DB-read endpoints to `async def` with the async SQLA
  driver *only after* Phases 1–2; mixing blocking calls into async routes is
  worse than the current all-sync threadpool model, so do this deliberately.

---

## Suggested order of execution

1. **Phase 1** (single-flight guard + remove inline 5y download from backtest) —
   largest, most immediate latency win; utility is already built and tested.
2. **Phase 2** Redis + shared `TTLCache` — fixes cold-after-restart slowness and
   multi-worker duplication.
3. **Phase 3** snapshot table + SQL-side ranking — speeds up the heaviest read
   pages.
4. **Phase 4 / 5** workers + PgBouncer — needed when you scale past one process.

## What's already done in this pass

- `backend/app/utils/cache.py` — `TTLCache` (fresh/stale TTL) and `single_flight`
  (concurrent-call de-duplication), the reusable foundation for Phases 1–2.
- `tests/test_cache_util.py` — 10 passing unit tests covering TTL classification,
  eviction, single-flight dedup, exception propagation, and per-key isolation.

> Note: the existing test suite uses `from datetime import UTC` (Python 3.11+).
> Run it with Python 3.11 (the project's `.venv`); the new cache tests are
> 3.10-compatible and pass standalone.
