(function () {
    const SEARCH_DEBOUNCE_MS = 280;
    const PLOTS_MIN_SEARCH_LEN = 1;
    const SEARCH_DEBOUNCE_MS_UNIVERSE = 300;
    const MIN_SEARCH_LEN = 2;
    let searchTimer = null;
    let syncPollTimer = null;
    let syncWasRunning = false;
    const loadedTabs = new Set();

    function showLoader(selector, visible) {
        const el = document.querySelector(selector);
        if (el) el.classList.toggle("is-visible", visible);
    }

    async function fetchPartial(url, method, body) {
        const options = {
            method: method || "GET",
            headers: { "X-Requested-With": "fastapi-web" },
        };
        if (method === "POST") {
            options.headers["Content-Type"] = "application/x-www-form-urlencoded";
            options.body = body || "";
        }
        const response = await fetch(url, options);
        return { response, html: await response.text() };
    }

    function buildParamsFromForm(form) {
        const params = new URLSearchParams();
        if (!form) return params;
        const fields = form.querySelectorAll("input, select, textarea");
        fields.forEach((field) => {
            if (!field.name || field.disabled) return;
            if (field.type === "checkbox" && !field.checked) return;
            if (field.type === "radio" && !field.checked) return;
            if (field.multiple) {
                Array.from(field.selectedOptions).forEach((option) => {
                    if (option.value) params.append(field.name, option.value);
                });
                return;
            }
            params.append(field.name, field.value);
        });
        return params;
    }

    async function loadUrlIntoPanel(panel, url, loaderSelector) {
        if (!panel || !url) return;
        showLoader(loaderSelector, true);
        try {
            const { response, html } = await fetchPartial(url, "GET");
            if (!response.ok) {
                panel.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>Failed to load (${response.status}).</p></div>`;
                return;
            }
            panel.innerHTML = html;
            if (window.bindHtmxLite) window.bindHtmxLite(document);
            bindPanelForms(panel);
            if (panel.querySelector("#index-fund-plots-shell") && window.IndexFundPlots) {
                window.IndexFundPlots.init(panel);
            }
            if (window.renderPlotlyCharts) window.renderPlotlyCharts(panel);
            maybeStartIndexSyncPolling();
        } catch (error) {
            panel.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>${error.message}</p></div>`;
        } finally {
            showLoader(loaderSelector, false);
        }
    }

    async function submitForm(form, panelSelector, loaderSelector) {
        const action = form.getAttribute("hx-get");
        if (!action || !form) return;
        const panel = document.querySelector(panelSelector);
        const params = buildParamsFromForm(form);
        const separator = action.includes("?") ? "&" : "?";
        const url = `${action}${separator}${params.toString()}`;
        await loadUrlIntoPanel(panel, url, loaderSelector);
    }

    function bindPanelForms(root) {
        const scope = root || document;
        const universeForm = scope.querySelector("#index-fund-universe-form") || document.querySelector("#index-fund-universe-form");
        if (universeForm && universeForm.dataset.bound !== "true") {
            universeForm.dataset.bound = "true";
            universeForm.querySelectorAll("[data-index-fund-filter]").forEach((field) => {
                field.addEventListener("change", () =>
                    submitForm(universeForm, "#index-fund-universe-panel", "#index-fund-universe-loader")
                );
            });
            const search = universeForm.querySelector("[data-index-fund-search]");
            if (search) {
                search.addEventListener("keyup", () => {
                    clearTimeout(searchTimer);
                    const value = (search.value || "").trim();
                    if (value.length > 0 && value.length < MIN_SEARCH_LEN) return;
                    searchTimer = window.setTimeout(
                        () => submitForm(universeForm, "#index-fund-universe-panel", "#index-fund-universe-loader"),
                        SEARCH_DEBOUNCE_MS_UNIVERSE
                    );
                });
            }
        }

        const historyForm = scope.querySelector("#index-fund-history-form") || document.querySelector("#index-fund-history-form");
        if (historyForm && historyForm.dataset.bound !== "true") {
            historyForm.dataset.bound = "true";
            historyForm.querySelectorAll("[data-index-fund-history-filter]").forEach((field) => {
                field.addEventListener("change", () => {
                    const instrumentId = historyForm.querySelector('[name="index_fund_id"]')?.value;
                    if (!instrumentId) return;
                    submitForm(historyForm, "#index-fund-history-panel", "#index-fund-history-loader");
                });
            });
        }
    }

    async function loadLazyHost(host, loaderSelector) {
        if (!host || !host.dataset.lazyUrl || host.dataset.loaded === "true") return;
        const swap = host.dataset.lazySwap || "innerHTML";
        showLoader(loaderSelector, true);
        try {
            const { response, html } = await fetchPartial(host.dataset.lazyUrl, "GET");
            if (!response.ok) {
                host.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>Failed to load (${response.status}).</p></div>`;
                return;
            }
            if (swap === "outerHTML") {
                host.outerHTML = html;
            } else {
                host.innerHTML = html;
            }
            host.dataset.loaded = "true";
            if (window.bindHtmxLite) window.bindHtmxLite(document);
            maybeStartIndexSyncPolling();
        } catch (error) {
            host.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>${error.message}</p></div>`;
        } finally {
            showLoader(loaderSelector, false);
        }
    }

    function activateTab(tabName) {
        document.querySelectorAll("[data-index-fund-tab]").forEach((button) => {
            const active = button.getAttribute("data-index-fund-tab") === tabName;
            button.classList.toggle("is-active", active);
            button.setAttribute("aria-selected", active ? "true" : "false");
        });
        document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
            const active = panel.getAttribute("data-tab-panel") === tabName;
            panel.classList.toggle("is-active", active);
            panel.hidden = !active;
        });
        if (loadedTabs.has(tabName)) return;
        loadedTabs.add(tabName);
        if (tabName === "universe") {
            const panel = document.getElementById("index-fund-universe-panel");
            const section = document.getElementById("index-fund-tab-universe");
            const url = section?.getAttribute("data-tab-url");
            loadUrlIntoPanel(panel, url, "#index-fund-universe-loader");
        } else if (tabName === "plots") {
            const panel = document.getElementById("index-fund-plots-panel");
            const section = document.getElementById("index-fund-tab-plots");
            const url = section?.getAttribute("data-tab-url");
            loadUrlIntoPanel(panel, url, "#index-fund-plots-loader");
        } else if (tabName === "history") {
            const historyPanel = document.getElementById("index-fund-history-panel");
            const historyUrl = historyPanel?.getAttribute("data-tab-url");
            loadUrlIntoPanel(historyPanel, historyUrl, "#index-fund-history-loader");
            const strategyPanel = document.getElementById("index-fund-strategy-panel");
            const strategyUrl = strategyPanel?.getAttribute("data-tab-url");
            loadUrlIntoPanel(strategyPanel, strategyUrl, "#index-fund-strategy-loader");
        }
    }

    function stopIndexSyncPolling() {
        if (syncPollTimer) {
            window.clearInterval(syncPollTimer);
            syncPollTimer = null;
        }
    }

    async function pollIndexSyncStatus() {
        const panel = document.querySelector("#index-fund-sync-status");
        if (!panel) {
            stopIndexSyncPolling();
            return;
        }
        try {
            const { response, html } = await fetchPartial("/web/partials/index-fund/sync-status", "GET");
            if (!response.ok) return;
            const wasRunning = panel.dataset.syncRunning === "true";
            panel.outerHTML = html;
            if (window.bindHtmxLite) window.bindHtmxLite(document);
            const updated = document.querySelector("#index-fund-sync-status");
            const isRunning = updated && updated.dataset.syncRunning === "true";
            if (wasRunning && !isRunning) {
                document.body.dispatchEvent(
                    new CustomEvent("index-fund-sync-completed", { bubbles: true })
                );
            }
            if (!isRunning) stopIndexSyncPolling();
        } catch (error) {
            console.error("[index_fund] sync poll failed", error);
        }
    }

    function maybeStartIndexSyncPolling() {
        const panel = document.querySelector("#index-fund-sync-status");
        if (!panel) {
            stopIndexSyncPolling();
            return;
        }
        const isRunning = panel.dataset.syncRunning === "true";
        if (isRunning) {
            syncWasRunning = true;
            if (!syncPollTimer) syncPollTimer = window.setInterval(pollIndexSyncStatus, 5000);
            return;
        }
        if (syncWasRunning) {
            syncWasRunning = false;
            refreshAfterSync();
        }
        stopIndexSyncPolling();
    }

    async function refreshAfterSync() {
        const summaryHost = document.getElementById("index-fund-summary-panel");
        if (summaryHost) {
            summaryHost.dataset.loaded = "false";
            await loadLazyHost(summaryHost, "#index-fund-summary-loader");
        }
        if (loadedTabs.has("universe")) {
            const form = document.getElementById("index-fund-universe-form");
            if (form) await submitForm(form, "#index-fund-universe-panel", "#index-fund-universe-loader");
        }
        if (loadedTabs.has("plots") && window.IndexFundPlots) {
            await window.IndexFundPlots.generatePlot();
        }
        await reloadSyncStatus();
    }

    async function reloadSyncStatus() {
        const panel = document.querySelector("#index-fund-sync-status");
        if (panel) {
            const { response, html } = await fetchPartial("/web/partials/index-fund/sync-status", "GET");
            if (response.ok) {
                panel.outerHTML = html;
                if (window.bindHtmxLite) window.bindHtmxLite(document);
                maybeStartIndexSyncPolling();
            }
            return;
        }
        const host = document.getElementById("index-fund-sync-panel");
        if (host) {
            host.dataset.loaded = "false";
            await loadLazyHost(host, "#index-fund-sync-loader");
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        const page = document.getElementById("index-fund-page");
        if (!page) return;

        loadLazyHost(
            document.getElementById("index-fund-summary-panel"),
            "#index-fund-summary-loader"
        );
        loadLazyHost(document.getElementById("index-fund-sync-panel"), "#index-fund-sync-loader");

        activateTab("universe");

        document.querySelectorAll("[data-index-fund-tab]").forEach((button) => {
            button.addEventListener("click", () => {
                activateTab(button.getAttribute("data-index-fund-tab"));
            });
        });
    });

    document.body.addEventListener("index-fund-sync-started", function () {
        syncWasRunning = true;
        maybeStartIndexSyncPolling();
    });

    document.body.addEventListener("index-fund-sync-completed", refreshAfterSync);

    document.body.addEventListener("htmx-lite:after-swap", function (event) {
        const target = event.detail?.target;
        if (!target || !document.getElementById("index-fund-page")) return;
        if (target.id === "index-fund-sync-status") {
            maybeStartIndexSyncPolling();
            const trigger = event.detail?.hxTrigger;
            if (trigger === "index-fund-sync-started") {
                syncWasRunning = true;
                maybeStartIndexSyncPolling();
            }
        }
        bindPanelForms(target);
        if (target.querySelector("#index-fund-plots-shell") && window.IndexFundPlots) {
            window.IndexFundPlots.init(target);
        }
        if (window.renderPlotlyCharts) window.renderPlotlyCharts(target);
    });

    const IndexFundPlots = (function () {
        const selected = new Map();
        let plotsSearchTimer = null;
        let plotsLastQuery = "";
        let plotsSearchAbort = null;
        let plotsGenerateAbort = null;

        function escapeHtml(value) {
            return String(value ?? "")
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;");
        }

        function parsePayload(raw) {
            if (!raw) return null;
            try {
                return JSON.parse(raw);
            } catch (error) {
                console.error("[index_fund] invalid instrument payload", error);
                return null;
            }
        }

        function formatPrice(item) {
            const value = Number(item?.latest_price);
            if (!Number.isFinite(value)) return "";
            return value.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }

        function syncHiddenInputs() {
            const host = document.getElementById("index-fund-plots-hidden-inputs");
            if (!host) return;
            host.innerHTML = Array.from(selected.values())
                .map((item) => `<input type="hidden" name="instrument_ids" value="${escapeHtml(item.id)}">`)
                .join("");
        }

        function updateCount() {
            const countEl = document.getElementById("index-fund-plots-count");
            const clearBtn = document.getElementById("index-fund-plots-clear");
            const count = selected.size;
            if (countEl) {
                countEl.textContent = count === 1 ? "1 selected" : `${count} selected`;
            }
            if (clearBtn) clearBtn.disabled = count === 0;
        }

        function renderChips() {
            const host = document.getElementById("index-fund-plots-chips");
            if (!host) return;
            if (!selected.size) {
                host.innerHTML =
                    '<p class="index-fund-plots-chips-empty muted">No instruments selected yet. Search above to add indexes or commodities.</p>';
                syncHiddenInputs();
                updateCount();
                refreshSearchResultStates();
                return;
            }
            host.innerHTML = Array.from(selected.values())
                .map(
                    (item) => `
                <span class="index-fund-plots-chip" data-instrument-id="${escapeHtml(item.id)}">
                    <span class="index-fund-plots-chip-label">${escapeHtml(item.symbol)} [${escapeHtml(item.yahoo_symbol)}]</span>
                    <button type="button" class="index-fund-plots-chip-remove" data-remove-instrument="${escapeHtml(item.id)}" aria-label="Remove ${escapeHtml(item.symbol)}">×</button>
                </span>`
                )
                .join("");
            host.querySelectorAll("[data-remove-instrument]").forEach((button) => {
                button.addEventListener("click", () => {
                    const id = Number(button.getAttribute("data-remove-instrument"));
                    if (Number.isFinite(id)) selected.delete(id);
                    renderChips();
                });
            });
            syncHiddenInputs();
            updateCount();
            refreshSearchResultStates();
        }

        function addInstrument(item) {
            const id = Number(item?.id);
            if (!Number.isFinite(id)) return false;
            if (selected.has(id)) return false;
            selected.set(id, item);
            renderChips();
            return true;
        }

        function clearAll() {
            selected.clear();
            renderChips();
        }

        function loadInitialSelection(shell) {
            selected.clear();
            const raw = shell?.getAttribute("data-initial-selection");
            const initial = parsePayload(raw) || [];
            initial.forEach((item) => {
                if (item && item.id != null) selected.set(Number(item.id), item);
            });
            renderChips();
        }

        function setSearchLoading(active) {
            const loader = document.getElementById("index-fund-plots-search-loader");
            if (loader) loader.classList.toggle("is-visible", active);
        }

        function clearSearchResults() {
            const host = document.getElementById("index-fund-plots-search-results");
            if (host) host.innerHTML = "";
            setSearchLoading(false);
        }

        function buildSearchUrl(query) {
            const params = new URLSearchParams();
            params.set("query", query);
            params.set("limit", "20");
            return `/web/partials/index-fund/instrument-search?${params.toString()}`;
        }

        async function fetchSearchResults(query) {
            if (plotsSearchAbort) plotsSearchAbort.abort();
            plotsSearchAbort = new AbortController();
            setSearchLoading(true);
            try {
                const response = await fetch(buildSearchUrl(query), {
                    headers: { "X-Requested-With": "fastapi-web" },
                    signal: plotsSearchAbort.signal,
                });
                const host = document.getElementById("index-fund-plots-search-results");
                if (!host) return;
                if (!response.ok) {
                    host.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>Search failed (${response.status}).</p></div>`;
                    return;
                }
                host.innerHTML = await response.text();
                bindSearchResultClicks(host);
            } catch (error) {
                if (error.name === "AbortError") return;
                const host = document.getElementById("index-fund-plots-search-results");
                if (host) {
                    host.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>${escapeHtml(error.message)}</p></div>`;
                }
            } finally {
                setSearchLoading(false);
                plotsSearchAbort = null;
            }
        }

        function scheduleSearch(query) {
            clearTimeout(plotsSearchTimer);
            if (query.length < PLOTS_MIN_SEARCH_LEN) {
                plotsLastQuery = "";
                clearSearchResults();
                return;
            }
            if (query === plotsLastQuery) return;
            plotsSearchTimer = window.setTimeout(() => {
                plotsLastQuery = query;
                fetchSearchResults(query);
            }, SEARCH_DEBOUNCE_MS);
        }

        function refreshSearchResultStates() {
            const selectedIds = new Set(selected.keys());
            document.querySelectorAll("#index-fund-plots-search-results .index-fund-search-result").forEach((button) => {
                const payload = parsePayload(button.getAttribute("data-instrument-payload"));
                if (!payload) return;
                const added = selectedIds.has(Number(payload.id));
                button.classList.toggle("is-added", added);
                button.disabled = added;
                const action = button.querySelector(".index-fund-search-result-action");
                if (action) action.textContent = added ? "Added" : "Add";
            });
        }

        function bindSearchResultClicks(root) {
            const scope = root || document;
            scope.querySelectorAll(".index-fund-search-result").forEach((button) => {
                if (button.dataset.plotsSearchBound === "true") return;
                button.dataset.plotsSearchBound = "true";
                button.addEventListener("click", () => {
                    const payload = parsePayload(button.getAttribute("data-instrument-payload"));
                    if (!payload) return;
                    if (addInstrument(payload)) {
                        const input = document.getElementById("index-fund-plots-search");
                        if (input) input.value = "";
                        plotsLastQuery = "";
                        clearSearchResults();
                    }
                });
            });
            refreshSearchResultStates();
        }

        function buildPlotFormBody() {
            const body = new URLSearchParams();
            const form = document.getElementById("index-fund-plots-form");
            if (!form) return body;
            Array.from(selected.values()).forEach((item) => {
                body.append("instrument_ids", String(item.id));
            });
            const period = form.querySelector('[name="period"]');
            const start = form.querySelector('[name="start_date"]');
            const end = form.querySelector('[name="end_date"]');
            const normalize = form.querySelector('[name="normalize_indexed"]');
            const compare = form.querySelector('[name="compare_nifty"]');
            if (period?.value) body.set("period", period.value);
            if (start?.value) body.set("start_date", start.value);
            if (end?.value) body.set("end_date", end.value);
            if (normalize?.checked) body.set("normalize_indexed", "true");
            if (compare?.checked) body.set("compare_nifty", "true");
            return body;
        }

        async function generatePlot() {
            const resultsHost = document.getElementById("index-fund-plots-results");
            if (!resultsHost) return;
            if (plotsGenerateAbort) plotsGenerateAbort.abort();
            plotsGenerateAbort = new AbortController();
            showLoader("#index-fund-plots-results-loader", true);
            try {
                if (window.purgePlotlyChartsIn) window.purgePlotlyChartsIn(resultsHost);
                const response = await fetch("/web/partials/index-fund/return-plots", {
                    method: "POST",
                    headers: {
                        "X-Requested-With": "fastapi-web",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    body: buildPlotFormBody().toString(),
                    signal: plotsGenerateAbort.signal,
                });
                const html = await response.text();
                if (!response.ok) {
                    resultsHost.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>Failed to generate plots (${response.status}).</p></div>`;
                    return;
                }
                resultsHost.innerHTML = html;
                if (window.renderPlotlyCharts) await window.renderPlotlyCharts(resultsHost);
            } catch (error) {
                if (error.name === "AbortError") return;
                resultsHost.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>${escapeHtml(error.message)}</p></div>`;
            } finally {
                showLoader("#index-fund-plots-results-loader", false);
                plotsGenerateAbort = null;
            }
        }

        function bindControls(shell) {
            if (!shell || shell.dataset.plotsControlsBound === "true") return;
            shell.dataset.plotsControlsBound = "true";

            const searchInput = document.getElementById("index-fund-plots-search");
            if (searchInput) {
                searchInput.addEventListener("input", () => scheduleSearch(searchInput.value.trim()));
            }

            const clearBtn = document.getElementById("index-fund-plots-clear");
            if (clearBtn) clearBtn.addEventListener("click", clearAll);

            const generateBtn = document.getElementById("index-fund-plots-generate");
            if (generateBtn) generateBtn.addEventListener("click", generatePlot);
        }

        function init(root) {
            const scope = root || document;
            const shell = scope.querySelector("#index-fund-plots-shell") || document.getElementById("index-fund-plots-shell");
            if (!shell) return;
            loadInitialSelection(shell);
            bindControls(shell);
        }

        return { init, generatePlot, clearAll };
    })();

    window.IndexFundPlots = IndexFundPlots;
})();
