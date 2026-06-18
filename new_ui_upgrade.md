# FastAPI Web UI Upgrade — Audit, Changelog, and Operations Guide

This document records the migration from the legacy **Streamlit** UI to the new **FastAPI + Jinja2 + HTML/CSS + HTMX-lite** web UI, including parity hotfixes and Phase 9 final cleanup.

Current state as of 2026-06-18: Streamlit has been decommissioned and removed from the runtime. The active application is the FastAPI web UI on `http://localhost:8000/web/explore`. Historical sections below may still describe the old migration/legacy-mode plan for audit context, but they are no longer the operating model.

---

## Table of contents

1. [Executive summary](#executive-summary)
2. [Architecture and constraints](#architecture-and-constraints)
3. [Default routes and navigation](#default-routes-and-navigation)
4. [Phase 9 audit (final state)](#phase-9-audit-final-state)
5. [Changelog by phase](#changelog-by-phase)
6. [Key files reference](#key-files-reference)
7. [Startup and operations](#startup-and-operations)
8. [Legacy Streamlit mode - historical](#legacy-streamlit-mode)
9. [Testing checklists](#testing-checklists)
10. [Known limitations](#known-limitations)

---

## Executive summary

| Item | Before | After |
|------|--------|-------|
| Default UI | Streamlit on port 8501 | FastAPI web on `http://localhost:8000/web/explore` |
| Default `python run.py` / `scripts/run.py` | Started backend + Streamlit (`frontend` service) | Starts **postgres + backend only** |
| Streamlit | Always running | **Dormant** — profile `legacy`, on-demand via sidebar or `--with-streamlit` |
| Root URL `GET /` | — | Redirects to `/web/explore` (not Streamlit) |
| Business logic | Python services | **Unchanged** — still in Python; JS is UI-only |
| Database schema | — | **Not modified** by this migration |
| Streamlit codebase | — | **Retained** under `frontend/` for legacy/debug |

---

## Architecture and constraints

### Stack (new web UI)

- **FastAPI** — HTTP routes, page shells, partial endpoints
- **Jinja2** — Server-rendered HTML (`backend/app/templates/`)
- **CSS** — `backend/app/static/css/app.css` (dark theme, shared tokens)
- **Minimal JS** — `backend/app/static/js/*.js` (partial fetch, debounce, Plotly, tabs)
- **HTMX-lite pattern** — `hx-get` / `hx-post` attributes handled by `app.js` (`bindHtmxLite`), not full HTMX library

### Hard rules (all phases)

- Keep current UI work in FastAPI/Jinja/static assets
- Do **not** change database schema for UI migration work
- Do **not** fake data or move trading/strategy/risk/AI business logic to JavaScript
- Do **not** add React / Next / Vue
- Calculations, screening, sync, and model calls stay in **Python services**

### Request flow (typical page)

1. `GET /web/<page>` — thin router in `web.py` builds shell context, renders `pages/<page>.html`
2. User action — JS or form posts to `GET|POST /web/partials/...`
3. Partial router validates input, calls service/helper, returns HTML fragment
4. `app.js` swaps fragment into target DOM; `charts.js` re-renders Plotly if needed

### Timing / observability

- Global: `app.timing` logs every HTTP request (`operation=http_request`)
- Page shells: `_log_page_route` in `web.py`
- Heavy partials: route-specific `logger.info` / `app.timing` in partial routers and services

---

## Default routes and navigation

### Page routes (confirmed)

| Route | Page template | Sidebar label | `active_page` key |
|-------|---------------|---------------|-------------------|
| `/web/explore` | `pages/explore.html` | Explore | `explore` |
| `/web/add-portfolio` | `pages/add_portfolio.html` | Add Portfolio | `add_portfolio` |
| `/web/paper-trading` | `pages/paper_trading.html` | Paper Trading | `paper_trading` |
| `/web/strategy-lab` | `pages/strategy_lab.html` | Strategy Lab | `strategy_lab` |
| `/web/backtesting` | `pages/backtesting.html` | Backtesting | `backtesting` |
| `/web/data` | `pages/data.html` | Data | `data` |
| `/web/index-fund` | `pages/index_fund.html` | Index Fund | `index_fund` |
| `/web/trends` | `pages/trends.html` | Trends | `trends` |
| `/web/risk` | `pages/risk.html` | Risk | `risk` |
| `/web/ai-think-tank` | `pages/ai_think_tank.html` | AI Think Tank | `ai_think_tank` |

### Root redirect

- `GET /` → **302** → `/web/explore` (`main.py` and `web.py`)
- Streamlit is **not** the default entry point

### Partial router prefixes

| Prefix | Module |
|--------|--------|
| `/web/partials` | `web_partials.py` (Explore movers, all stocks, sequential rankings, etc.) |
| `/web/partials/portfolio` | `web_portfolio_partials.py` |
| `/web/partials/paper-trading` | `web_paper_partials.py` |
| `/web/partials/backtesting` | `web_backtesting_partials.py` |
| `/web/partials/strategy-lab` | `web_strategy_lab_partials.py` |
| `/web/partials/trends` | `web_trends_partials.py` |
| `/web/partials/risk` | `web_risk_partials.py` |
| `/web/partials/data` | `web_data_partials.py` |
| `/web/partials/index-fund` | `web_index_fund_partials.py` |
| `/web/partials/ai-think-tank` | `web_ai_think_tank_partials.py` |
| `/web/partials/explore` | `web_explore_stock_partials.py` |
| `POST /web/legacy/start` | `web_legacy.py` |

### JavaScript files

| File | Role |
|------|------|
| `app.js` | HTMX-lite binding, partial fetch, Explore sync polling, sidebar, **Legacy Mode** button |
| `charts.js` | Plotly load/render/purge; finding detail charts on expand |
| `trading.js` | Add portfolio, paper trading, stock search picks |
| `backtesting.js` | Backtest forms, results, charts |
| `strategy_lab.js` | Strategy lab search, params, signal preview |
| `analytics.js` | Trends / analytics partials |
| `data_ops.js` | Data sync status and actions |
| `index_fund.js` | Index fund search chips, return plots POST |
| `ai_think_tank.js` | AI tabs, screener, mode-scoped form params |

### CSS

- Single primary stylesheet: `backend/app/static/css/app.css`
- Design tokens in `:root` (`--bg`, `--surface`, `--accent`, `--success`, `--danger`, `--warning`, plus `--panel` aliases)
- Page-specific sections: explore, stock detail, algo findings table, index fund, AI think tank, strategy playground, etc.

### Streamlit in FastAPI backend

- **Audit result:** No `import streamlit` under `backend/app/`
- Streamlit remains in `frontend/` and talks to FastAPI via HTTP only (unchanged architecture)

---

## Phase 9 audit (final state)

Audit performed at end of migration before documenting.

### Migrated features (functional areas)

| Area | Streamlit reference | Web status |
|------|---------------------|------------|
| Explore / market movers / stock search | `frontend/pages/1_Explore.py` | Migrated + stock detail deep links |
| Add Portfolio | Streamlit trading flows | Migrated + `?stock=` prefill |
| Paper Trading | Streamlit | Migrated |
| Backtesting | Streamlit | Migrated |
| Strategy Lab | Streamlit | Migrated |
| Trends | Streamlit | Migrated |
| Risk | Streamlit | Migrated |
| Data operations | Streamlit | Migrated |
| Index Fund | `frontend/pages/7_Index_Fund.py` | Migrated + return plot chip select |
| AI Think Tank | `frontend/pages/10_AI_Think_Tank.py` | Migrated + validation + NL screener |

### Duplicate / overlap notes

- Stock route keys: centralized in `web_explore_stock_helpers.py` (`stock_route_key`, `stock_detail_url`, `resolve_stock_by_route_key`)
- JSON safety: `app/utils/json_safe.py` (`to_json_safe`) for templates and AI raw debug blocks
- Algo findings: must use `PRICE_HISTORY_LIMIT` (10000), same as Streamlit API `limit` — not a low cap like 24

### Slow / heavy areas (performance observations)

Typical hotspots (lazy-load or POST-on-demand where implemented):

1. `POST /web/partials/backtesting/...` — backtest run
2. `POST /web/partials/data/...` — sync jobs
3. `POST /web/partials/ai-think-tank/run-analysis` — Ollama calls
4. `POST /web/partials/index-fund/return-plots` — Plotly payloads
5. `GET /web/explore?stock=...` — stock detail + full algo findings list

Page shells should avoid: sync on load, AI on load, full universe queries without limits.

### Broken / unfinished links (known)

- `SBIN.NS` route key may resolve via search fallback to a different ticker if DB mapping is ambiguous (logged as `resolved_search_fallback`)
- Legacy auto-start from **inside** Docker backend is disabled; use host command or sidebar fallback text
- Orphan container `paper_trading_frontend` may exist after rename to `streamlit` — run `docker compose down --remove-orphans`

---

## Changelog by phase

Phases are grouped by delivery order in the migration effort. Each entry lists **problem → solution → files**.

---

### Core migration (Explore through AI Think Tank)

**Goal:** Replace Streamlit pages with FastAPI page shells + partials while keeping Python business logic.

**Delivered:**

- Shared layout: `base.html`, `sidebar.html`, `topbar.html`, `app.css`, `app.js`
- Nine page routes under `/web/...` (see table above)
- Partial routers per feature area
- Plotly via `charts.js` + `data-plotly-json` payloads from Python
- Debug auth bypass for local Docker (`DEBUG_AUTH_BYPASS`)

**Representative files:**

- `backend/app/routers/web.py`
- `backend/app/routers/web_*_partials.py`
- `backend/app/services/web_*_helpers.py`
- `backend/app/templates/pages/*.html`
- `backend/app/templates/partials/*.html`

---

### Explore — stock detail and search links

**Problem:** Market mover links worked, but search result cards were not clickable; `/web/explore?stock=` was ignored.

**Solution:**

- `resolve_stock_by_route_key`, `build_stock_detail_context` in `web_explore_stock_helpers.py`
- Search partial: `mode=link` → anchor `/web/explore?stock={yahoo_symbol}`
- Stock detail partials: header, chart, algorithm findings, strategy playground

**Files:**

- `web_explore_stock_helpers.py`, `web.py`, `web_explore_stock_partials.py`
- `stock_search_results.html`, `stock_detail*.html`, `explore_top_movers.html`

---

### Stock detail UI hotfix

**Problem:** White Plotly charts; misaligned Strategy Playground; weak algorithm findings table.

**Solution:**

- `_stock_chart_dark_layout()` — `plotly_dark`, `#050812` backgrounds
- Strategy preview grid CSS (`.strategy-preview-grid`, `.mini-stat-card`)
- Algorithm findings table (`.algo-findings-table`)

**Files:**

- `web_explore_stock_helpers.py`, `app.css`, stock detail templates

---

### Add Portfolio — stock prefill

**Problem:** “Add to portfolio” from stock detail did not preselect the stock.

**Solution:**

- Link: `/web/add-portfolio?stock={route_key}`
- `resolve_stock_for_prefill`, `build_preselected_stock_view`
- Hidden `stock_id`, buy price, purchase date; `trading.js` updates from search cards

**Files:**

- `web_explore_stock_helpers.py`, `web.py`, `add_portfolio.html`, `holding_selected_stock.html`, `trading.js`

---

### Phase 7.1 — Index Fund return plots

**Problem:** Native `<select multiple>` lost selection on partial refresh; no search/chips.

**Solution:**

- Split GET shell vs POST results for return plots
- Chip multi-select + `GET /web/partials/index-fund/instrument-search`
- `POST /web/partials/index-fund/return-plots` with `form.getlist("instrument_ids")`
- Identifier: **`instrument_ids`** (DB index fund IDs)

**Files:**

- `web_index_fund_helpers.py`, `web_index_fund_partials.py`
- `index_fund_return_plots.html`, `index_fund_return_plots_results.html`, `index_fund_instrument_search_results.html`, `index_fund.js`, `app.css`

---

### Phase 8.1 — AI Think Tank validation hotfix

**Problem:** All analysis modes failed with Pydantic `int_parsing` on `backtest_id` when the form sent `backtest_id=""`. Raw JSON 422 appeared in the UI.

**Root cause:** FastAPI `Form(...)` with `backtest_id: int | None` parsed empty string **before** the handler. `ai_think_tank.js` submitted all form fields including empty backtest select.

**Solution:**

| Part | Change |
|------|--------|
| A | Mode-specific validation via `build_ai_analysis_request_from_form()` — blank → `None`, `optional_int()` |
| B | `none_if_blank`, `optional_int`, `optional_float` helpers |
| C | `formParams(form, activeMode)` skips blanks and inactive tab fields |
| D | `validate_run_request()` per mode; Backtest Interpreter requires `backtest_id` |
| E | `validation_error_view()` + styled `info_banner` in `ai_analysis_result.html` |
| F | Logs: `ai_think_tank.run_analysis`, `validation_failed` |
| G | `main.py` — web partial AI routes return HTML on 422 instead of JSON |

**Mode requirements (after fix):**

| Mode | Required | Must NOT require `backtest_id` |
|------|----------|-------------------------------|
| Signal Synthesizer | model, stock | yes |
| Backtest Interpreter | model, backtest_id | — |
| Pre-Trade Advisor | model, portfolio, stock, action, qty, price | yes |
| NL Screener | model, user prompt (≥3 chars) | yes |
| Portfolio Health | model, portfolio | yes |
| Journal Insights | model, portfolio | yes |

**Files:**

- `web_ai_think_tank_helpers.py`, `web_ai_think_tank_partials.py`, `ai_think_tank.js`, `ai_analysis_result.html`, `main.py`

---

### Phase 8.2 — AI Think Tank NL Screener hotfix

**Problem:** NL Screener failed with `Object of type datetime is not JSON serializable` in UI/raw JSON block.

**Root cause:** `list_stock_performance()` returns `latest_price_datetime` as Python `datetime`. Template `{{ result.raw | tojson }}` crashed. FastAPI stock detail also used `limit=24` for findings (separate issue fixed in algo findings phase).

**Solution:**

| Part | Change |
|------|--------|
| A | `app/utils/json_safe.py` — `to_json_safe()` recursive serializer |
| B | `web_nl_screener_service.py` — structured view model, clickable stocks |
| C | Deterministic rules first (`run_deterministic_nl_screener`) for prompts like “up 20% this year” |
| D | NL screener table with `/web/explore?stock={route_key}` links |
| E | Metric cards: matched count, return basis, filter |
| F | `raw_json` pre-serialized in `shape_analysis_view`; template uses `result.raw_json` |
| G | Styled error banners; Ollama-down message when model unreachable |

**Deterministic prompt patterns (minimum):**

- `moved up by X% this year` / `rose X% this year` / `up X% this year` → `min_change_1y_pct`, **1Y return** basis
- `Banking stocks with high volume` → sort by volume + sector keyword filter
- IT down year / up month → sector IT + 1Y negative + 1M positive

**Return basis note shown in UI:**

> Using stored 1Y return from daily candles (YTD is not computed separately).

**Files:**

- `json_safe.py`, `web_nl_screener_service.py`, `web_ai_think_tank_helpers.py`, `ai_analysis_result.html`, `app.css`

---

### Algorithm Findings parity hotfix

**Problem:** Stock detail showed only one finding (“Data Quality — Only 24 stored candles…”) while Streamlit showed full algorithm table (VWAP, MACD, GARCH, etc.).

**Root cause:** `build_stock_detail_context()` called `generate_stock_algo_findings(db, stock.id, limit=24)`. Service requires **≥ 80** rows (`MIN_SIGNAL_ROWS`); below that it returns a single data-quality row. Streamlit uses `limit=10000` via `/stocks/{id}/algo-findings`.

**Solution:**

- Change to `limit=PRICE_HISTORY_LIMIT` (10000) — same as Streamlit
- Logging: `stock_detail.algorithm_findings` with `findings_count` and status histogram
- Template already looped all findings; added warning banner when only Data Quality row
- `finding_chart_to_plotly()` + accordions with dark Plotly; `charts.js` renders on `<details>` open

**Short-history behavior:** Matches Streamlit — if &lt; 80 candles, only Data Quality row (plus warning banner on web).

**Files:**

- `web_explore_stock_helpers.py`, `stock_algorithm_findings.html`, `charts.js`, `app.css`

---

### Phase 9 — Final cleanup, legacy mode, startup

**Goals:** FastAPI default; Streamlit dormant; navigation cleanup; legacy launcher; docs and `run.py` behavior preserved.

#### 9A — Audit

Documented in [Phase 9 audit](#phase-9-audit-final-state) above.

#### 9B — Navigation cleanup

- Removed “Phase 8 web UI” / “Phase 3 web UI” labels
- Sidebar footer: **FastAPI web UI**
- **Enable Legacy Mode** button + caption in sidebar

#### 9C — Legacy Mode launcher

- `POST /web/legacy/start` → `partials/legacy_mode_result.html`
- `web_legacy_service.py`:
  - Health check `LEGACY_STREAMLIT_URL` (Docker default: `http://host.docker.internal:8501`)
  - Host-only auto-start: `docker compose --profile legacy up -d streamlit` when not in container
  - Docker backend: fallback message + command (no arbitrary shell)

**Config env vars (`backend` / `.env`):**

| Variable | Purpose |
|----------|---------|
| `LEGACY_STREAMLIT_URL` | URL to open/check (default `http://localhost:8501`) |
| `ENABLE_LEGACY_START` | Allow host Docker start (`false` in compose backend) |
| `LEGACY_COMPOSE_ROOT` | Compose project root for start command |
| `LEGACY_START_COMMAND` | Shown on failure; default `docker compose --profile legacy up -d streamlit` |

#### 9D — Docker cleanup

- Service renamed: `frontend` → **`streamlit`**
- Profile: **`legacy`** — not started by default
- Default compose services: **`postgres`**, **`backend`** only

#### 9E — UI consistency

- CSS token aliases: `--panel`, `--panel-2`, `--panel-3`
- Legacy sidebar block styles, command `<pre>` formatting

#### 9F — JavaScript

- `bindLegacyMode()` in `app.js` — POST partial, open tab on success

#### 9G — Backend

- `web_legacy.py` router registered in `main.py`

#### 9H–9J — Performance / labels / errors

- Documented hotspots; no schema changes
- Prior hotfixes ensure styled banners (AI, screener, validation) — not raw JSON on normal errors

#### 9K — Startup (`run.py`) — critical for local workflow

**User flow preserved:** Click Run on `run.py` or `python run.py` at repo root.

| Entry | Behavior |
|-------|----------|
| `run.py` (repo root) | Delegates to `scripts/run.py` |
| `run.ps1` / `run.bat` | Same delegation |
| Default `up` | `docker compose up [--build] [-d] postgres backend` |
| `--with-streamlit` / `--legacy` | Also starts `streamlit` with `--profile legacy` |
| Detached success | Prints `http://localhost:8000/web/explore`, optional browser open |
| `logs` | Backend only; add `--with-streamlit` for Streamlit logs |

**Files:**

- `run.py`, `scripts/run.py`, `docker-compose.yml`, `README.md`
- `web_legacy.py`, `web_legacy_service.py`, `legacy_mode_result.html`
- `sidebar.html`, `app.js`, `app.css`, `config.py`, `main.py`

---

## Key files reference

### Startup & Docker

| File | Purpose |
|------|---------|
| `run.py` | Root launcher → `scripts/run.py` |
| `scripts/run.py` | Docker Compose orchestration, health wait, URLs |
| `docker-compose.yml` | `postgres`, `backend`, `streamlit` (profile `legacy`) |
| `run.ps1` / `run.bat` | Windows wrappers |

### Web core

| File | Purpose |
|------|---------|
| `backend/app/main.py` | App factory, routers, `/` redirect, validation handler |
| `backend/app/routers/web.py` | Page shell routes |
| `backend/app/web_utils.py` | Jinja templates, filters (`inr`, `pct`, `stock_detail_url`, …) |
| `backend/app/templates/base.html` | Shell layout |
| `backend/app/templates/partials/sidebar.html` | Navigation + Legacy Mode |

### Feature helpers (representative)

| File | Purpose |
|------|---------|
| `web_explore_stock_helpers.py` | Stock detail, route keys, algo findings, charts |
| `web_index_fund_helpers.py` | Index fund plots, instrument search |
| `web_ai_think_tank_helpers.py` | AI modes, validation, shape views |
| `web_nl_screener_service.py` | Deterministic NL screener + view models |
| `algo_finding_service.py` | Full algorithm list (Streamlit parity) |

### Utilities

| File | Purpose |
|------|---------|
| `app/utils/json_safe.py` | JSON-safe serialization for templates/debug |

---

## Startup and operations

### Prerequisites

1. Docker Desktop running (Windows: “Engine running”)
2. `py -3 scripts/run.py check` — optional verification

### Default — new web UI only

```powershell
cd "C:\Users\Aman\Documents\New project\paper_trading_app"

# Foreground (Ctrl+C to stop)
python run.py

# Background (recommended)
python run.py -d

# Equivalent
py -3 scripts/run.py -d
.\run.ps1 -d
```

**Open:** http://localhost:8000/web/explore  

**Also:** http://localhost:8000/ redirects to Explore.

### Docker Compose (alternative)

```powershell
docker compose up -d --build postgres backend
```

### Stop / status / logs

```powershell
python run.py stop
python run.py status
python run.py logs
python run.py logs --with-streamlit   # include legacy Streamlit
```

### Rebuild after code changes

```powershell
docker compose up -d --build backend
# or
python run.py -d
```

### Clean up old Streamlit container name

If you still see `paper_trading_frontend` from before the rename:

```powershell
docker compose down --remove-orphans
python run.py -d
```

---

## Legacy Streamlit mode

Streamlit is **not** started by default. Use one of:

### 1. Sidebar (preferred when backend runs on host)

1. Open any `/web/...` page
2. Click **Enable Legacy Mode** (bottom of sidebar)
3. On success → new tab `http://localhost:8501`
4. On failure → banner with `docker compose --profile legacy up -d streamlit`

**Note:** When backend runs **inside Docker**, auto-start is disabled (`ENABLE_LEGACY_START=false`). Run the command on the host, then click the button again (or open :8501 directly).

### 2. CLI flag

```powershell
python run.py --with-streamlit -d
# alias: --legacy
```

### 3. Docker profile only

```powershell
docker compose --profile legacy up -d streamlit
```

Streamlit app path unchanged: `frontend/streamlit_app.py` → API at `http://backend:8000` inside compose network.

---

## Testing checklists

### Smoke — all pages load (200)

```
/web/explore
/web/add-portfolio
/web/paper-trading
/web/backtesting
/web/strategy-lab
/web/trends
/web/risk
/web/data
/web/index-fund
/web/ai-think-tank
```

### Functional (post-migration)

1. Explore search → stock detail (`?stock=SYMBOL.NS`)
2. Stock detail → full algorithm findings table (long history stock)
3. Add to portfolio deep link prefill
4. Paper order preview/submit
5. Backtesting single/multi stock
6. Strategy Lab signal preview
7. Trends filters preserve state
8. Risk portfolio metrics or empty state
9. Data sync status partial
10. Index Fund return plots chip multi-select
11. AI Think Tank — Signal Synthesizer / NL Screener without `backtest_id` error
12. NL Screener — clickable stocks, no datetime JSON error
13. Legacy Mode — not auto-started; manual start works or shows fallback command

### AI Think Tank — validation (Phase 8.1)

| Test | Expected |
|------|----------|
| Signal Synthesizer, no backtest | No `backtest_id` int parsing |
| NL Screener prompt | Reaches screener or clean Ollama-down banner |
| Backtest Interpreter, no backtest | “Select a backtest run before using Backtest Interpreter.” |
| Invalid `backtest_id=abc` | “must be a valid integer” (Backtest mode only) |
| UI errors | Styled banners, not raw JSON |

### NL Screener (Phase 8.2)

| Prompt | Expected |
|--------|----------|
| Which stock moved up by 20% this year | Deterministic 1Y filter, table + links |
| Find stocks whose price rose 20% this year | Same |
| Banking stocks with high volume | Volume sort + sector filter (count data-dependent) |

### Algorithm findings

| Stock type | Expected |
|------------|----------|
| Long history (e.g. HDFCBANK.NS) | ~15 rows (VWAP, MACD, GARCH, …) + accordions |
| &lt; 80 candles | Data Quality warning + single row (matches Streamlit) |

---

## Known limitations

1. **Ollama** — AI modes need local model server; may show “Ollama is not running” while `/api/tags` succeeds but `/api/chat` 404s for a specific model.
2. **Legacy start from Docker backend** — Cannot start Streamlit container from inside backend; use host CLI or sidebar instructions.
3. **NL screener** — Unsupported prompts fall back to LLM filter parsing (requires Ollama); deterministic rules cover common “% this year” / banking volume / IT patterns only.
4. **YTD return** — Screener uses **1Y** stored return; YTD not computed separately (explicit in UI note).
5. **Route key resolution** — Some symbols may resolve via `resolved_search_fallback`; prefer `yahoo_symbol` in DB.
6. **Orphan containers** — Old `paper_trading_frontend` name after compose rename; use `--remove-orphans`.
7. **Database schema** — Unchanged by UI migration; no Alembic changes documented here.
8. **Streamlit** — Unmaintained as primary UI; kept for debugging and comparison only.

---

## Document history

| Date | Scope |
|------|--------|
| 2026-05-30 | Initial `new_ui_upgrade.md` — consolidates migration, Phases 7.1, 8.1, 8.2, algorithm findings parity, Phase 9 audit and changelog |

---

*For day-to-day commands, see also `README.md` Quick start section (updated for web-first URLs).*
