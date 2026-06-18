(function () {
    const basket = [];
    const MIN_QUERY_LEN = 2;
    const SEARCH_DEBOUNCE_MS = 300;
    let searchTimer = null;
    let lastQuery = "";
    let searchAbort = null;

    function log(...args) {
        console.log("[backtesting]", ...args);
    }

    function instrumentKey(item) {
        return `${item.instrument_type}:${item.exchange || ""}:${item.symbol}:${item.id}`;
    }

    function formatInr(amount) {
        const value = Number(amount);
        if (!Number.isFinite(value)) return null;
        return `Rs ${value.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }

    function escapeHtml(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function syncBasketHiddenInput() {
        const jsonInput = document.getElementById("backtest-basket-json");
        if (jsonInput) jsonInput.value = JSON.stringify(basket);

        const host = document.getElementById("backtest-basket-hidden-fields");
        if (host) {
            host.innerHTML = basket
                .map(
                    (item) => `
                <input type="hidden" name="basket_instrument_ids" value="${escapeHtml(item.id)}">
                <input type="hidden" name="basket_symbols" value="${escapeHtml(item.symbol)}">
                <input type="hidden" name="basket_exchanges" value="${escapeHtml(item.exchange || "")}">
                <input type="hidden" name="basket_instrument_types" value="${escapeHtml(item.instrument_type || "stock")}">`
                )
                .join("");
        }

        const countBadge = document.getElementById("backtest-basket-count");
        if (countBadge) {
            const label = basket.length === 1 ? "instrument" : "instruments";
            countBadge.textContent = `${basket.length} ${label}`;
        }

        log("basket sync", {
            count: basket.length,
            hiddenFieldCount: host ? host.querySelectorAll('input[name="basket_symbols"]').length : 0,
        });
    }

    function renderBasketChips() {
        const host = document.getElementById("backtest-basket-chips");
        if (!host) return;
        if (!basket.length) {
            host.innerHTML = `<div class="empty-state basket-empty"><strong>No instruments added yet</strong><p>Search above, then add one or more instruments to the basket.</p></div>`;
            syncBasketHiddenInput();
            return;
        }

        host.innerHTML = basket
            .map((item) => {
                const metaParts = [];
                if (item.exchange) metaParts.push(item.exchange);
                if (item.sector) metaParts.push(item.sector);
                else if (item.industry) metaParts.push(item.industry);
                const priceLabel = formatInr(item.latest_price);
                return `
            <article class="basket-card" data-basket-key="${escapeHtml(instrumentKey(item))}">
                <div class="basket-card-body">
                    <div class="basket-symbol">${escapeHtml(item.symbol)}</div>
                    <div class="basket-company">${escapeHtml(item.company_name || item.symbol)}</div>
                    <div class="basket-meta">${escapeHtml(metaParts.join(" · ") || item.yahoo_symbol || "")}</div>
                    ${priceLabel ? `<div class="basket-price">Latest: ${escapeHtml(priceLabel)}</div>` : ""}
                </div>
                <button type="button" class="basket-remove-btn ghost-button" data-remove-basket="${escapeHtml(instrumentKey(item))}">Remove</button>
            </article>`;
            })
            .join("");

        host.querySelectorAll("[data-remove-basket]").forEach((button) => {
            button.addEventListener("click", () => {
                const key = button.getAttribute("data-remove-basket");
                const index = basket.findIndex((item) => instrumentKey(item) === key);
                if (index >= 0) {
                    basket.splice(index, 1);
                    log("basket item removed", key);
                }
                renderBasketChips();
            });
        });
        syncBasketHiddenInput();
    }

    function addToBasket(item) {
        const key = instrumentKey(item);
        if (basket.some((entry) => instrumentKey(entry) === key)) {
            log("duplicate skipped", key);
            return false;
        }
        basket.push(item);
        log("basket item added", item.symbol, item.exchange);
        renderBasketChips();
        return true;
    }

    function setSearching(active) {
        const loader = document.getElementById("backtest-search-loader");
        if (loader) loader.classList.toggle("is-visible", active);
    }

    function clearSearchResults() {
        const host = document.getElementById("backtesting-search-results");
        if (host) host.innerHTML = "";
        setSearching(false);
    }

    function buildSearchUrl(query) {
        const params = new URLSearchParams();
        params.set("query", query);
        params.set("limit", "12");
        const instrumentType = document.getElementById("backtest-instrument-type")?.value || "stock";
        params.set("instrument_type", instrumentType);
        if (instrumentType === "index_fund") {
            const category = document.getElementById("backtest-search-category")?.value || "";
            if (category) params.set("search_category", category);
        } else {
            const exchange = document.getElementById("backtest-search-exchange")?.value || "";
            const indexCode = document.getElementById("backtest-search-index")?.value || "";
            if (exchange) params.set("search_exchange", exchange);
            if (indexCode) params.set("search_index_code", indexCode);
        }
        return `/web/partials/backtesting/instrument-search?${params.toString()}`;
    }

    async function fetchBacktestSearchResults(query) {
        if (searchAbort) searchAbort.abort();
        searchAbort = new AbortController();
        const url = buildSearchUrl(query);
        log("search request", query);
        setSearching(true);
        try {
            const response = await fetch(url, {
                headers: { "X-Requested-With": "fastapi-web" },
                signal: searchAbort.signal,
            });
            const host = document.getElementById("backtesting-search-results");
            if (!host) return;
            if (!response.ok) {
                host.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>Search failed (${response.status}).</p></div>`;
                return;
            }
            host.innerHTML = await response.text();
            const count = host.querySelectorAll(".search-result-item").length;
            log("search results", { query, count });
            bindSearchResultClicks(host);
        } catch (error) {
            if (error.name === "AbortError") {
                log("search cancelled", query);
                return;
            }
            const host = document.getElementById("backtesting-search-results");
            if (host) {
                host.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>${escapeHtml(error.message)}</p></div>`;
            }
        } finally {
            setSearching(false);
            searchAbort = null;
        }
    }

    function scheduleSearch(query) {
        clearTimeout(searchTimer);
        if (query.length < MIN_QUERY_LEN) {
            lastQuery = "";
            clearSearchResults();
            return;
        }
        if (query === lastQuery) return;
        searchTimer = window.setTimeout(() => {
            lastQuery = query;
            fetchBacktestSearchResults(query);
        }, SEARCH_DEBOUNCE_MS);
    }

    function bindSearchInput() {
        const input = document.getElementById("backtest-instrument-search");
        if (!input) return;
        input.addEventListener("input", () => {
            scheduleSearch(input.value.trim());
        });
    }

    function bindSearchFilters() {
        document.querySelectorAll("[data-backtest-search-filter], #backtest-instrument-type").forEach((field) => {
            field.addEventListener("change", () => {
                const input = document.getElementById("backtest-instrument-search");
                const query = (input?.value || "").trim();
                lastQuery = "";
                if (query.length >= MIN_QUERY_LEN) {
                    fetchBacktestSearchResults(query);
                } else {
                    clearSearchResults();
                }
            });
        });
    }

    function parseBasketPayload(button) {
        const raw = button.getAttribute("data-basket-payload");
        if (!raw) return null;
        try {
            return JSON.parse(raw);
        } catch (error) {
            log("invalid basket payload", error);
            return null;
        }
    }

    function refreshSearchResultStates() {
        const basketKeys = new Set(basket.map(instrumentKey));
        document.querySelectorAll("#backtesting-search-results .search-result-item").forEach((button) => {
            const payload = parseBasketPayload(button);
            if (!payload) return;
            const added = basketKeys.has(instrumentKey(payload));
            button.classList.toggle("is-added", added);
            button.disabled = added;
            const action = button.querySelector(".search-result-action");
            if (action) action.textContent = added ? "Added" : "Add to basket";
        });
    }

    function bindSearchResultClicks(root) {
        const scope = root || document;
        scope.querySelectorAll(".search-result-item").forEach((button) => {
            if (button.dataset.basketBound === "true") return;
            button.dataset.basketBound = "true";
            button.addEventListener("click", () => {
                const payload = parseBasketPayload(button);
                if (!payload) return;
                if (addToBasket(payload)) {
                    const input = document.getElementById("backtest-instrument-search");
                    if (input) input.value = "";
                    lastQuery = "";
                    clearSearchResults();
                }
            });
        });
        refreshSearchResultStates();
    }

    function bindUniverseToggle() {
        const select = document.getElementById("backtest-instrument-type");
        if (!select) return;
        const sync = () => {
            const isIndex = select.value === "index_fund";
            document.querySelectorAll(".stock-only").forEach((el) => el.classList.toggle("hidden", isIndex));
            document.querySelectorAll(".index-only").forEach((el) => el.classList.toggle("hidden", !isIndex));
            clearSearchResults();
            lastQuery = "";
        };
        select.addEventListener("change", sync);
        sync();
    }

    function collectParametersJson() {
        const parameters = {};
        document.querySelectorAll(".strategy-param-input").forEach((input) => {
            const name = input.getAttribute("data-param-name");
            if (!name) return;
            const kind = input.getAttribute("data-param-kind");
            if (kind === "bool") {
                parameters[name] = input.checked;
            } else if (kind === "int") {
                parameters[name] = parseInt(input.value, 10);
            } else if (kind === "float") {
                parameters[name] = parseFloat(input.value);
            } else {
                parameters[name] = input.value;
            }
        });
        const hidden = document.getElementById("backtest-parameters-json");
        if (hidden) hidden.value = JSON.stringify(parameters);
        return parameters;
    }

    async function loadStrategyParams() {
        const strategyId = document.getElementById("backtest-strategy-id")?.value;
        const advanced = document.getElementById("backtest-advanced-toggle")?.checked;
        const panel = document.getElementById("backtest-strategy-params-panel");
        const loader = document.getElementById("strategy-params-loader");
        if (!strategyId || !panel) return;
        if (loader) loader.classList.add("is-visible");
        try {
            const response = await fetch(
                `/web/partials/backtesting/strategy-params?strategy_id=${encodeURIComponent(strategyId)}&advanced=${advanced ? "true" : "false"}`,
                { headers: { "X-Requested-With": "fastapi-web" } }
            );
            panel.innerHTML = await response.text();
            panel.querySelectorAll(".strategy-param-input").forEach((input) => {
                input.addEventListener("change", collectParametersJson);
                input.addEventListener("input", collectParametersJson);
            });
            collectParametersJson();
        } finally {
            if (loader) loader.classList.remove("is-visible");
        }
    }

    function setRunLoading(active) {
        const button = document.getElementById("backtest-run-button");
        const loader = document.getElementById("backtest-run-loader");
        if (button) {
            button.disabled = active;
            button.textContent = active ? "Running backtest..." : "Run Backtest";
        }
        if (loader) loader.classList.toggle("is-visible", active);
    }

    function bindResultTabs(scope) {
        const tabs = scope.querySelectorAll("[data-result-tab]");
        const panels = scope.querySelectorAll(".result-tab-panel");
        if (!tabs.length) return;

        tabs.forEach((tab) => {
            if (tab.dataset.tabBound === "true") return;
            tab.dataset.tabBound = "true";
            tab.addEventListener("click", async () => {
                const name = tab.getAttribute("data-result-tab");
                tabs.forEach((item) => item.classList.toggle("is-active", item === tab));
                panels.forEach((panel) =>
                    panel.classList.toggle("is-active", panel.getAttribute("data-panel") === name)
                );
                if (name === "trades") {
                    const runId = scope.querySelector(".backtest-results-panel")?.dataset.runId;
                    const host = runId ? scope.querySelector(`#backtest-trades-panel-${runId}`) : null;
                    if (host && host.dataset.loaded !== "true" && host.dataset.lazyUrl) {
                        host.innerHTML = `<span class="loading-indicator is-visible">Loading trades...</span>`;
                        const response = await fetch(host.dataset.lazyUrl, {
                            headers: { "X-Requested-With": "fastapi-web" },
                        });
                        host.innerHTML = await response.text();
                        host.dataset.loaded = "true";
                    }
                }
                if (name === "equity" || name === "drawdown") {
                    if (window.renderPlotlyCharts) await window.renderPlotlyCharts(scope);
                    if (window.resizePlotlyCharts) window.resizePlotlyCharts(scope);
                }
            });
        });
    }

    function showResultPanel(root, runId) {
        const panels = root.querySelectorAll(".backtest-result-panel-wrap");
        panels.forEach((panel) => {
            const active = panel.dataset.resultPanel === String(runId);
            panel.classList.toggle("is-active", active);
            panel.hidden = !active;
        });
        const chips = root.querySelectorAll(".instrument-result-chip");
        chips.forEach((chip) => {
            const active = chip.dataset.resultRunId === String(runId);
            chip.classList.toggle("is-active", active);
            chip.setAttribute("aria-selected", active ? "true" : "false");
        });
        const activePanel = root.querySelector(`.backtest-result-panel-wrap[data-result-panel="${runId}"]`);
        if (activePanel) {
            bindResultTabs(activePanel);
            if (window.renderPlotlyCharts) window.renderPlotlyCharts(activePanel);
            if (window.resizePlotlyCharts) window.resizePlotlyCharts(activePanel);
            log("result panel switched", runId);
        }
    }

    function bindInstrumentSelector(root) {
        const chips = root.querySelectorAll(".instrument-result-chip");
        if (!chips.length) {
            const panel = root.querySelector(".backtest-result-panel-wrap, .backtest-results-panel");
            if (panel) {
                const scope = panel.closest(".backtest-result-panel-wrap") || panel;
                bindResultTabs(scope);
                if (window.renderPlotlyCharts) window.renderPlotlyCharts(scope);
            }
            return;
        }
        chips.forEach((chip) => {
            if (chip.dataset.selectorBound === "true") return;
            chip.dataset.selectorBound = "true";
            chip.addEventListener("click", () => {
                showResultPanel(root, chip.dataset.resultRunId);
            });
        });
        const first = root.querySelector(".instrument-result-chip.is-active") || chips[0];
        if (first) showResultPanel(root, first.dataset.resultRunId);
    }

    function bindForm() {
        const form = document.getElementById("backtesting-form");
        if (!form) return;
        form.addEventListener("submit", () => {
            syncBasketHiddenInput();
            collectParametersJson();
            setRunLoading(true);
            log("form submit", { basketCount: basket.length });
        });
        document.body.addEventListener("htmx-lite:after-swap", (event) => {
            const target = event.detail?.target;
            if (!target) return;
            if (target.id === "backtesting-results") {
                bindInstrumentSelector(target);
            }
        });
        document.body.addEventListener("backtesting:run-finished", () => {
            setRunLoading(false);
        });
        if (window.bindHtmxLite) window.bindHtmxLite(form);
    }

    function init() {
        if (!document.getElementById("backtesting-page")) return;
        renderBasketChips();
        bindUniverseToggle();
        bindSearchInput();
        bindSearchFilters();
        bindForm();

        document.getElementById("backtest-clear-basket")?.addEventListener("click", () => {
            basket.length = 0;
            log("basket cleared");
            renderBasketChips();
        });

        document.getElementById("backtest-strategy-id")?.addEventListener("change", loadStrategyParams);
        document.getElementById("backtest-advanced-toggle")?.addEventListener("change", loadStrategyParams);

        loadStrategyParams();
        log("initialized");
    }

    document.addEventListener("DOMContentLoaded", init);
})();
