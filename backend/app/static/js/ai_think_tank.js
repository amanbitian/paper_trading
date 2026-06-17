(function () {
    const SEARCH_DEBOUNCE_MS = 300;
    const MIN_QUERY_LEN = 2;
    let searchTimer = null;
    let searchAbort = null;
    let selectedStock = null;
    const loadedTabs = new Set();

    function showLoader(id, visible) {
        const el = document.getElementById(id);
        if (el) el.classList.toggle("is-visible", visible);
    }

    async function fetchHtml(url, method, body) {
        const options = {
            method: method || "GET",
            headers: { "X-Requested-With": "fastapi-web" },
        };
        if (method === "POST") {
            options.headers["Content-Type"] = "application/x-www-form-urlencoded";
            options.body = body;
        }
        const response = await fetch(url, options);
        return { response, html: await response.text() };
    }

    function isBlank(value) {
        return value == null || String(value).trim() === "";
    }

    function formParams(form, activeMode) {
        const params = new URLSearchParams();
        if (!form) return params;
        form.querySelectorAll("input, select, textarea").forEach((field) => {
            if (!field.name || field.disabled) return;
            if ((field.type === "checkbox" || field.type === "radio") && !field.checked) return;
            const panel = field.closest("[data-ai-panel]");
            if (panel && activeMode && panel.getAttribute("data-ai-panel") !== activeMode) {
                return;
            }
            if (isBlank(field.value)) return;
            params.append(field.name, field.value);
        });
        const modelSelect = document.getElementById("ai-model-select");
        if (modelSelect && modelSelect.name && !isBlank(modelSelect.value)) {
            params.set("model", modelSelect.value);
        }
        const portfolioSelect = document.getElementById("ai-portfolio-select");
        if (portfolioSelect && !isBlank(portfolioSelect.value)) {
            params.set("portfolio_id", portfolioSelect.value);
        }
        const stockId = document.getElementById("ai-stock-id");
        if (stockId && !isBlank(stockId.value)) {
            params.set("stock_id", stockId.value);
        }
        const symbol = document.getElementById("ai-symbol");
        if (symbol && !isBlank(symbol.value)) {
            params.set("symbol", symbol.value);
        }
        return params;
    }

    function buildSearchUrl(query) {
        const params = new URLSearchParams();
        params.set("query", query);
        params.set("limit", "12");
        const exchange = document.getElementById("ai-search-exchange")?.value || "";
        if (exchange) params.set("search_exchange", exchange);
        return `/web/partials/ai-think-tank/instrument-search?${params.toString()}`;
    }

    function syncHiddenStock(stock) {
        selectedStock = stock;
        const idInput = document.getElementById("ai-stock-id");
        const symInput = document.getElementById("ai-symbol");
        if (idInput) idInput.value = stock ? String(stock.id) : "";
        if (symInput) symInput.value = stock ? stock.symbol : "";
    }

    async function loadStockContext() {
        const panel = document.getElementById("ai-stock-context-panel");
        if (!panel) return;
        const stockId = document.getElementById("ai-stock-id")?.value;
        if (!stockId) {
            panel.innerHTML = `<div class="empty-state"><strong>No stock selected</strong><p>Search and select a stock.</p></div>`;
            refreshPromptPreview();
            return;
        }
        const { response, html } = await fetchHtml(
            `/web/partials/ai-think-tank/stock-context?stock_id=${encodeURIComponent(stockId)}`,
            "GET"
        );
        if (response.ok) panel.innerHTML = html;
        refreshPromptPreview();
    }

    async function loadPortfolioContext() {
        const panel = document.getElementById("ai-portfolio-context-panel");
        const portfolioId = document.getElementById("ai-portfolio-select")?.value;
        if (!panel) return;
        if (!portfolioId) return;
        const { response, html } = await fetchHtml(
            `/web/partials/ai-think-tank/portfolio-context?portfolio_id=${encodeURIComponent(portfolioId)}`,
            "GET"
        );
        if (response.ok) panel.innerHTML = html;
        refreshPromptPreview();
    }

    async function refreshPromptPreview() {
        const panel = document.getElementById("ai-prompt-preview-panel");
        if (!panel) return;
        const mode = document.getElementById("ai-analysis-mode")?.value || "signal_synthesizer";
        const params = formParams(document.getElementById("ai-think-tank-form"), mode);
        params.set("mode", mode);
        const model = document.getElementById("ai-model-select")?.value;
        if (model) params.set("model", model);
        const { response, html } = await fetchHtml(
            `/web/partials/ai-think-tank/prompt-preview?${params.toString()}`,
            "GET"
        );
        if (response.ok) panel.innerHTML = html;
    }

    async function loadModelStatus() {
        const panel = document.getElementById("ai-model-status-panel");
        const model = document.getElementById("ai-model-select")?.value;
        if (!panel) return;
        showLoader("ai-model-status-loader", true);
        try {
            const url = model
                ? `/web/partials/ai-think-tank/model-status?model=${encodeURIComponent(model)}`
                : "/web/partials/ai-think-tank/model-status";
            const { response, html } = await fetchHtml(url, "GET");
            if (response.ok) {
                panel.innerHTML = html;
                populateModelSelect();
            }
        } finally {
            showLoader("ai-model-status-loader", false);
        }
    }

    function populateModelSelect() {
        const select = document.getElementById("ai-model-select");
        const datalist = document.getElementById("ai-model-options");
        if (!select || !datalist) return;
        const options = Array.from(datalist.querySelectorAll("option")).map((o) => o.value);
        if (!options.length) return;
        const current = select.value;
        select.innerHTML = "";
        options.forEach((name) => {
            const opt = document.createElement("option");
            opt.value = name;
            opt.textContent = name;
            if (name === current) opt.selected = true;
            select.appendChild(opt);
        });
        if (!select.value && options[0]) select.value = options[0];
    }

    function bindStockPick(root) {
        root.querySelectorAll(".strategy-lab-stock-pick").forEach((button) => {
            if (button.dataset.aiBound === "true") return;
            button.dataset.aiBound = "true";
            button.addEventListener("click", () => {
                try {
                    const payload = JSON.parse(button.getAttribute("data-stock-payload") || "{}");
                    syncHiddenStock(payload);
                    document.getElementById("ai-search-results").innerHTML = "";
                    const search = document.getElementById("ai-stock-search");
                    if (search) search.value = payload.symbol || "";
                    loadStockContext();
                } catch (error) {
                    console.error("[ai_think_tank] stock pick failed", error);
                }
            });
        });
    }

    async function runSearch(query) {
        const host = document.getElementById("ai-search-results");
        if (!host) return;
        if (searchAbort) searchAbort.abort();
        if (!query || query.length < MIN_QUERY_LEN) {
            host.innerHTML = "";
            showLoader("ai-search-loader", false);
            return;
        }
        searchAbort = new AbortController();
        showLoader("ai-search-loader", true);
        try {
            const response = await fetch(buildSearchUrl(query), {
                headers: { "X-Requested-With": "fastapi-web" },
                signal: searchAbort.signal,
            });
            host.innerHTML = await response.text();
            bindStockPick(host);
        } catch (error) {
            if (error.name !== "AbortError") console.error("[ai_think_tank] search failed", error);
        } finally {
            showLoader("ai-search-loader", false);
            searchAbort = null;
        }
    }

    function activateTab(tabId) {
        document.querySelectorAll("[data-ai-tab]").forEach((btn) => {
            const active = btn.getAttribute("data-ai-tab") === tabId;
            btn.classList.toggle("is-active", active);
            btn.setAttribute("aria-selected", active ? "true" : "false");
        });
        document.querySelectorAll("[data-ai-panel]").forEach((panel) => {
            const active = panel.getAttribute("data-ai-panel") === tabId;
            panel.classList.toggle("is-active", active);
            panel.hidden = !active;
        });
        const modeInput = document.getElementById("ai-analysis-mode");
        if (modeInput && tabId !== "activity_log") modeInput.value = tabId;
        refreshPromptPreview();

        if (tabId === "activity_log" && !loadedTabs.has("activity_log")) {
            loadedTabs.add("activity_log");
            loadActivityLog();
        }
    }

    async function loadActivityLog() {
        const panel = document.getElementById("ai-activity-panel");
        if (!panel) return;
        showLoader("ai-activity-loader", true);
        try {
            const { response, html } = await fetchHtml("/web/partials/ai-think-tank/activity-log?limit=80", "GET");
            if (response.ok) panel.innerHTML = html;
        } finally {
            showLoader("ai-activity-loader", false);
        }
    }

    async function runAnalysis(mode) {
        const form = document.getElementById("ai-think-tank-form");
        const resultHost = document.getElementById("ai-analysis-result");
        if (!form || !resultHost) return;
        const params = formParams(form, mode);
        params.set("mode", mode);
        showLoader("ai-analysis-loader", true);
        resultHost.innerHTML = "";
        try {
            const { response, html } = await fetchHtml("/web/partials/ai-think-tank/run-analysis", "POST", params);
            resultHost.innerHTML = html;
            if (window.bindHtmxLite) window.bindHtmxLite(resultHost);
            const details = document.getElementById("ai-evidence-details");
            if (details) details.open = true;
            refreshPromptPreview();
        } catch (error) {
            resultHost.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>${error.message}</p></div>`;
        } finally {
            showLoader("ai-analysis-loader", false);
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        const page = document.getElementById("ai-think-tank-page");
        if (!page) return;

        loadModelStatus();

        const search = document.getElementById("ai-stock-search");
        if (search) {
            search.addEventListener("keyup", () => {
                clearTimeout(searchTimer);
                const q = (search.value || "").trim();
                if (!q) {
                    document.getElementById("ai-search-results").innerHTML = "";
                    return;
                }
                if (q.length < MIN_QUERY_LEN) return;
                searchTimer = setTimeout(() => runSearch(q), SEARCH_DEBOUNCE_MS);
            });
        }

        document.getElementById("ai-portfolio-select")?.addEventListener("change", loadPortfolioContext);
        document.getElementById("ai-model-select")?.addEventListener("change", loadModelStatus);

        document.querySelectorAll("[data-ai-tab]").forEach((btn) => {
            btn.addEventListener("click", () => activateTab(btn.getAttribute("data-ai-tab")));
        });

        document.querySelectorAll(".ai-run-button").forEach((btn) => {
            btn.addEventListener("click", () => runAnalysis(btn.getAttribute("data-ai-mode")));
        });

        document.getElementById("ai-refresh-activity")?.addEventListener("click", loadActivityLog);

        loadPortfolioContext();
        refreshPromptPreview();
    });
})();
