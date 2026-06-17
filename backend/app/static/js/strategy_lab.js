(function () {
    const MIN_QUERY_LEN = 2;
    const SEARCH_DEBOUNCE_MS = 300;
    let searchTimer = null;
    let lastQuery = "";
    let searchAbort = null;
    let selectedStock = null;

    function log(...args) {
        console.log("[strategy_lab]", ...args);
    }

    function escapeHtml(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function syncStockHiddenInput() {
        const input = document.getElementById("strategy-lab-stock-id");
        if (input) input.value = selectedStock ? String(selectedStock.id) : "";
    }

    function setSearching(active) {
        const loader = document.getElementById("strategy-lab-search-loader");
        if (loader) loader.classList.toggle("is-visible", active);
    }

    function clearSearchResults() {
        const host = document.getElementById("strategy-lab-search-results");
        if (host) host.innerHTML = "";
        setSearching(false);
    }

    function buildSearchUrl(query) {
        const params = new URLSearchParams();
        params.set("query", query);
        params.set("limit", "12");
        params.set("instrument_type", "stock");
        const exchange = document.getElementById("strategy-lab-search-exchange")?.value || "";
        if (exchange) params.set("search_exchange", exchange);
        return `/web/partials/strategy-lab/instrument-search?${params.toString()}`;
    }

    async function fetchSearchResults(query) {
        if (searchAbort) searchAbort.abort();
        searchAbort = new AbortController();
        setSearching(true);
        try {
            const response = await fetch(buildSearchUrl(query), {
                headers: { "X-Requested-With": "fastapi-web" },
                signal: searchAbort.signal,
            });
            const host = document.getElementById("strategy-lab-search-results");
            if (!host) return;
            if (!response.ok) {
                host.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>Search failed (${response.status}).</p></div>`;
                return;
            }
            host.innerHTML = await response.text();
            log("search results", query, host.querySelectorAll(".strategy-lab-stock-pick").length);
            bindStockPick(host);
        } catch (error) {
            if (error.name === "AbortError") return;
            const host = document.getElementById("strategy-lab-search-results");
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
            fetchSearchResults(query);
        }, SEARCH_DEBOUNCE_MS);
    }

    async function refreshStockContext() {
        const stockId = document.getElementById("strategy-lab-stock-id")?.value;
        const portfolioId = document.getElementById("strategy-lab-portfolio-id")?.value;
        const host = document.getElementById("strategy-lab-stock-context");
        if (!host || !stockId) {
            if (host) {
                host.innerHTML = `<div class="empty-state"><strong>No stock selected</strong><p>Search and select a stock.</p></div>`;
            }
            return;
        }
        const params = new URLSearchParams({ stock_id: stockId });
        if (portfolioId) params.set("portfolio_id", portfolioId);
        const response = await fetch(`/web/partials/strategy-lab/stock-context?${params}`, {
            headers: { "X-Requested-With": "fastapi-web" },
        });
        if (response.ok) host.innerHTML = await response.text();
    }

    function bindStockPick(root) {
        const scope = root || document;
        scope.querySelectorAll(".strategy-lab-stock-pick").forEach((button) => {
            if (button.dataset.stockPickBound === "true") return;
            button.dataset.stockPickBound = "true";
            button.addEventListener("click", () => {
                const raw = button.getAttribute("data-stock-payload");
                if (!raw) return;
                try {
                    selectedStock = JSON.parse(raw);
                } catch (error) {
                    log("invalid stock payload", error);
                    return;
                }
                log("stock selected", selectedStock.symbol);
                syncStockHiddenInput();
                refreshStockContext();
                const input = document.getElementById("strategy-lab-instrument-search");
                if (input) input.value = "";
                lastQuery = "";
                clearSearchResults();
            });
        });
    }

    function collectParametersJson() {
        const jsonEditor = document.getElementById("strategy-lab-params-json");
        const advanced = document.getElementById("strategy-lab-advanced-toggle")?.checked;
        if (advanced && jsonEditor) {
            const hidden = document.getElementById("strategy-lab-params-json-hidden");
            if (hidden) hidden.value = jsonEditor.value.trim() || "{}";
            return;
        }
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
        const hidden = document.getElementById("strategy-lab-params-json-hidden");
        if (hidden) hidden.value = JSON.stringify(parameters);
        if (advanced && jsonEditor) jsonEditor.value = JSON.stringify(parameters, null, 2);
        log("params synced", Object.keys(parameters).length);
    }

    async function loadStrategyParams() {
        const strategyId = document.getElementById("strategy-lab-template-id")?.value;
        const advanced = document.getElementById("strategy-lab-advanced-toggle")?.checked;
        const panel = document.getElementById("strategy-lab-params-panel");
        const loader = document.getElementById("strategy-lab-params-loader");
        if (!strategyId || !panel) return;
        if (loader) loader.classList.add("is-visible");
        try {
            const response = await fetch(
                `/web/partials/strategy-lab/strategy-params?strategy_id=${encodeURIComponent(strategyId)}&advanced=${advanced ? "true" : "false"}`,
                { headers: { "X-Requested-With": "fastapi-web" } }
            );
            panel.innerHTML = await response.text();
            panel.querySelectorAll(".strategy-param-input").forEach((input) => {
                input.addEventListener("change", collectParametersJson);
                input.addEventListener("input", collectParametersJson);
            });
            const jsonEditor = document.getElementById("strategy-lab-params-json");
            if (jsonEditor) {
                jsonEditor.addEventListener("input", collectParametersJson);
            }
            collectParametersJson();
        } finally {
            if (loader) loader.classList.remove("is-visible");
        }
    }

    async function loadLazyPartial(host) {
        if (!host || !host.dataset.lazyUrl) return;
        const portfolioId = document.getElementById("strategy-lab-portfolio-id")?.value;
        const strategyId = document.getElementById("strategy-lab-user-strategy-id")?.value;
        let url = host.dataset.lazyUrl;
        const params = new URLSearchParams();
        if (portfolioId) params.set("portfolio_id", portfolioId);
        if (strategyId && host.id === "strategy-lab-user-strategies") {
            params.set("selected_user_strategy_id", strategyId);
        }
        if (params.toString()) url += `?${params.toString()}`;
        host.innerHTML = `<span class="loading-indicator is-visible">Loading...</span>`;
        const response = await fetch(url, { headers: { "X-Requested-With": "fastapi-web" } });
        host.innerHTML = await response.text();
        bindUserStrategyActions(host);
        if (window.bindHtmxLite) window.bindHtmxLite(host);
    }

    function bindUserStrategyActions(root) {
        root.querySelectorAll(".strategy-use-btn").forEach((button) => {
            if (button.dataset.useBound === "true") return;
            button.dataset.useBound = "true";
            button.addEventListener("click", () => {
                const id = button.getAttribute("data-user-strategy-id");
                const select = document.getElementById("strategy-lab-user-strategy-id");
                if (select && id) {
                    select.value = id;
                    log("user strategy selected", id);
                }
            });
        });
    }

    function bindActionButtons() {
        ["strategy-lab-create-btn", "strategy-lab-generate-btn"].forEach((id) => {
            const button = document.getElementById(id);
            if (!button) return;
            button.addEventListener("click", () => collectParametersJson());
        });
    }

    function bindHtmxHandlers() {
        document.body.addEventListener("htmx-lite:after-swap", (event) => {
            const target = event.detail?.target;
            if (!target) return;
            if (target.id === "strategy-lab-create-result") {
                const marker = target.querySelector("[data-refresh-user-strategies]");
                if (marker) {
                    const createdId = marker.getAttribute("data-created-strategy-id");
                    loadLazyPartial(document.getElementById("strategy-lab-user-strategies")).then(() => {
                        const select = document.getElementById("strategy-lab-user-strategy-id");
                        if (select && createdId) select.value = createdId;
                    });
                    loadLazyPartial(document.getElementById("strategy-lab-activity-log"));
                }
            }
            if (target.id === "strategy-lab-signal-preview") {
                loadLazyPartial(document.getElementById("strategy-lab-activity-log"));
            }
        });
    }

    function init() {
        if (!document.getElementById("strategy-lab-page")) return;

        const searchInput = document.getElementById("strategy-lab-instrument-search");
        if (searchInput) {
            searchInput.addEventListener("input", () => scheduleSearch(searchInput.value.trim()));
        }

        document.querySelectorAll("[data-strategy-lab-search-filter]").forEach((field) => {
            field.addEventListener("change", () => {
                lastQuery = "";
                const query = (searchInput?.value || "").trim();
                if (query.length >= MIN_QUERY_LEN) fetchSearchResults(query);
                else clearSearchResults();
            });
        });

        document.getElementById("strategy-lab-portfolio-id")?.addEventListener("change", () => {
            refreshStockContext();
            loadLazyPartial(document.getElementById("strategy-lab-user-strategies"));
        });

        document.getElementById("strategy-lab-template-id")?.addEventListener("change", loadStrategyParams);
        document.getElementById("strategy-lab-advanced-toggle")?.addEventListener("change", loadStrategyParams);
        document.getElementById("strategy-lab-reset-params")?.addEventListener("click", loadStrategyParams);
        document.getElementById("strategy-lab-refresh-strategies")?.addEventListener("click", () =>
            loadLazyPartial(document.getElementById("strategy-lab-user-strategies"))
        );
        document.getElementById("strategy-lab-refresh-activity")?.addEventListener("click", () =>
            loadLazyPartial(document.getElementById("strategy-lab-activity-log"))
        );

        bindActionButtons();
        bindHtmxHandlers();
        if (window.bindHtmxLite) {
            window.bindHtmxLite(document.getElementById("strategy-lab-page"));
        }

        loadStrategyParams();
        loadLazyPartial(document.getElementById("strategy-lab-user-strategies"));
        loadLazyPartial(document.getElementById("strategy-lab-activity-log"));
        log("initialized");
    }

    document.addEventListener("DOMContentLoaded", init);
})();
