(function () {
    const LAZY_SECTIONS = [
        { host: "#data-overview-panel", loader: "#data-overview-loader", swap: "innerHTML" },
        { host: "#data-sync-panel", loader: "#data-sync-loader", swap: "outerHTML" },
        { host: "#data-freshness-panel", loader: "#data-freshness-loader", swap: "innerHTML" },
        { host: "#data-quality-panel", loader: "#data-quality-loader", swap: "innerHTML" },
        { host: "#data-ingestion-panel", loader: "#data-ingestion-loader", swap: "innerHTML" },
        { host: "#data-fundamentals-panel", loader: "#data-fundamentals-loader", swap: "innerHTML" },
        { host: "#data-runs-panel", loader: "#data-runs-loader", swap: "innerHTML" },
        { host: "#data-failed-panel", loader: "#data-failed-loader", swap: "innerHTML" },
        { host: "#data-stale-panel", loader: "#data-stale-loader", swap: "innerHTML" },
        { host: "#data-db-panel", loader: "#data-db-loader", swap: "innerHTML" },
        { host: "#data-latency-panel", loader: "#data-latency-loader", swap: "innerHTML" },
    ];

    const POST_SYNC_REFRESH = [
        "#data-overview-panel",
        "#data-freshness-panel",
        "#data-quality-panel",
        "#data-ingestion-panel",
        "#data-fundamentals-panel",
        "#data-runs-panel",
        "#data-failed-panel",
        "#data-stale-panel",
        "#data-db-panel",
    ];

    let dataSyncPollTimer = null;
    let dataSyncWasRunning = false;

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

    async function swapHost(host, html, swapMode) {
        if (!host) return null;
        if (swapMode === "outerHTML") {
            host.outerHTML = html;
            return document.querySelector(`#${host.id}`) || document.querySelector("#data-sync-status");
        }
        host.innerHTML = html;
        return host;
    }

    async function loadLazyHost(host) {
        if (!host || !host.dataset.lazyUrl || host.dataset.loaded === "true") {
            return host;
        }
        const swapMode = host.dataset.lazySwap || "innerHTML";
        const loader = host.closest(".data-ops-section")?.querySelector(".loading-indicator");
        if (loader) loader.classList.add("is-visible");
        try {
            const { response, html } = await fetchPartial(host.dataset.lazyUrl, "GET");
            if (!response.ok) {
                host.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>Failed to load (${response.status}).</p></div>`;
                return host;
            }
            const swapped = await swapHost(host, html, swapMode);
            host.dataset.loaded = "true";
            if (window.bindHtmxLite) window.bindHtmxLite(document);
            maybeStartDataSyncPolling();
            return swapped || host;
        } catch (error) {
            host.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>${error.message}</p></div>`;
            return host;
        } finally {
            if (loader) loader.classList.remove("is-visible");
        }
    }

    async function reloadHostSelector(selector) {
        const host = document.querySelector(selector);
        if (!host || !host.dataset.lazyUrl) return;
        host.dataset.loaded = "false";
        await loadLazyHost(host);
    }

    function buildStaleUrl() {
        const form = document.getElementById("data-stale-filter-form");
        const params = new URLSearchParams();
        params.set("limit", "100");
        if (form) {
            const minLag = form.querySelector('[name="min_lag"]')?.value || "1";
            const exchange = form.querySelector('[name="exchange"]')?.value || "";
            params.set("min_lag", minLag);
            if (exchange) params.set("exchange", exchange);
        }
        return `/web/partials/data/stale-symbols?${params.toString()}`;
    }

    async function reloadStalePanel() {
        const host = document.querySelector("#data-stale-panel");
        if (!host) return;
        host.dataset.lazyUrl = buildStaleUrl();
        host.dataset.loaded = "false";
        showLoader("#data-stale-loader", true);
        await loadLazyHost(host);
        showLoader("#data-stale-loader", false);
    }

    async function reloadSyncStatus() {
        const panel = document.querySelector("#data-sync-status");
        if (panel) {
            try {
                const { response, html } = await fetchPartial("/web/partials/data/sync-status", "GET");
                if (response.ok) {
                    panel.outerHTML = html;
                    if (window.bindHtmxLite) window.bindHtmxLite(document);
                    maybeStartDataSyncPolling();
                }
            } catch (error) {
                console.error("[data_ops] sync refresh failed", error);
            }
            return;
        }
        await reloadHostSelector("#data-sync-panel");
    }

    async function refreshAfterSync() {
        const jobs = POST_SYNC_REFRESH.map((selector) => reloadHostSelector(selector));
        await Promise.all(jobs);
        await reloadSyncStatus();
    }

    function stopDataSyncPolling() {
        if (dataSyncPollTimer) {
            window.clearInterval(dataSyncPollTimer);
            dataSyncPollTimer = null;
        }
    }

    async function pollDataSyncStatus() {
        const panel = document.querySelector("#data-sync-status");
        if (!panel) {
            stopDataSyncPolling();
            return;
        }
        try {
            const { response, html } = await fetchPartial("/web/partials/data/sync-status", "GET");
            if (!response.ok) return;
            const wasRunning = panel.dataset.syncRunning === "true";
            panel.outerHTML = html;
            if (window.bindHtmxLite) window.bindHtmxLite(document);
            const updated = document.querySelector("#data-sync-status");
            const isRunning = updated && updated.dataset.syncRunning === "true";
            if (wasRunning && !isRunning) {
                await refreshAfterSync();
            }
            if (!isRunning) stopDataSyncPolling();
        } catch (error) {
            console.error("[data_ops] sync poll failed", error);
        }
    }

    function maybeStartDataSyncPolling() {
        const panel = document.querySelector("#data-sync-status");
        if (!panel) {
            stopDataSyncPolling();
            return;
        }
        const isRunning = panel.dataset.syncRunning === "true";
        if (isRunning) {
            dataSyncWasRunning = true;
            if (!dataSyncPollTimer) {
                dataSyncPollTimer = window.setInterval(pollDataSyncStatus, 5000);
            }
            return;
        }
        if (dataSyncWasRunning) {
            dataSyncWasRunning = false;
            refreshAfterSync();
        }
        stopDataSyncPolling();
    }

    async function loadAllSections() {
        const page = document.getElementById("data-ops-page");
        if (!page) return;
        for (const section of LAZY_SECTIONS) {
            const host = document.querySelector(section.host);
            if (host) await loadLazyHost(host);
        }
    }

    function bindStaleFilters() {
        const form = document.getElementById("data-stale-filter-form");
        if (!form || form.dataset.bound === "true") return;
        form.dataset.bound = "true";
        form.querySelectorAll("[data-data-stale-filter]").forEach((field) => {
            field.addEventListener("change", reloadStalePanel);
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        if (!document.getElementById("data-ops-page")) return;
        bindStaleFilters();
        loadAllSections();
    });

    document.body.addEventListener("market-sync-started", function () {
        if (!document.getElementById("data-ops-page")) return;
        dataSyncWasRunning = true;
        maybeStartDataSyncPolling();
    });

    document.body.addEventListener("market-sync-completed", function () {
        if (!document.getElementById("data-ops-page")) return;
        refreshAfterSync();
    });

    document.body.addEventListener("data-quality-optimized", function () {
        if (!document.getElementById("data-ops-page")) return;
        refreshAfterSync();
    });

    document.body.addEventListener("htmx-lite:after-swap", function (event) {
        const target = event.detail?.target;
        if (!target || !document.getElementById("data-ops-page")) return;
        if (target.id === "data-sync-status" || target.closest?.("#data-sync-status")) {
            maybeStartDataSyncPolling();
        }
    });
})();
