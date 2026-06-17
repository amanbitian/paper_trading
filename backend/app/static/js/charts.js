(function () {
    const MAX_PLOTLY_WAIT_MS = 8000;

    function waitForPlotly() {
        return new Promise((resolve) => {
            if (typeof window.Plotly !== "undefined") {
                resolve(true);
                return;
            }
            const started = Date.now();
            const timer = window.setInterval(() => {
                if (typeof window.Plotly !== "undefined") {
                    window.clearInterval(timer);
                    resolve(true);
                    return;
                }
                if (Date.now() - started >= MAX_PLOTLY_WAIT_MS) {
                    window.clearInterval(timer);
                    resolve(false);
                }
            }, 80);
        });
    }

    function showChartError(target, message) {
        if (!target) return;
        target.innerHTML = `<div class="info-banner danger chart-error-banner"><span class="info-dot"></span><p>${message}</p></div>`;
    }

    function readPayload(el) {
        const raw = (el.textContent || el.dataset.plotlyJson || "").trim();
        if (!raw) return null;
        try {
            return JSON.parse(raw);
        } catch (error) {
            console.error("[charts] Invalid Plotly JSON", error);
            return null;
        }
    }

    async function renderPayloadEl(el) {
        const targetId = el.dataset.target;
        if (!targetId) return;
        const target = document.getElementById(targetId);
        if (!target) return;

        const payload = readPayload(el);
        if (!payload || !payload.data || !payload.data.length) {
            showChartError(target, "Chart data is missing or empty.");
            return;
        }

        const plotlyReady = await waitForPlotly();
        if (!plotlyReady) {
            console.error("[charts] Plotly is not loaded. Include plotly.js before charts.js.");
            showChartError(target, "Plotly failed to load. Refresh the page.");
            return;
        }

        try {
            window.Plotly.purge(target);
            await window.Plotly.newPlot(target, payload.data, payload.layout || {}, {
                responsive: true,
                displayModeBar: false,
            });
        } catch (error) {
            console.error("[charts] Failed to render Plotly chart", targetId, error);
            showChartError(target, "Failed to render chart. See console for details.");
        }
    }

    async function renderPlotlyCharts(root) {
        const scope = root || document;
        const payloads = scope.querySelectorAll("[data-plotly-json]");
        for (const el of payloads) {
            await renderPayloadEl(el);
        }

        // Legacy hosts: JSON in a sibling script (full page render only).
        scope.querySelectorAll("[data-plotly-chart]").forEach((host) => {
            const chartId = host.getAttribute("data-plotly-chart");
            const script = chartId ? document.getElementById(`${chartId}-data`) : null;
            if (!script) return;
            const payload = readPayload(script);
            if (!payload || !payload.data) return;
            waitForPlotly().then((ready) => {
                if (!ready) {
                    showChartError(host, "Plotly failed to load.");
                    return;
                }
                window.Plotly.purge(host);
                window.Plotly.newPlot(host, payload.data, payload.layout || {}, {
                    responsive: true,
                    displayModeBar: false,
                });
            });
        });
    }

    function purgeChartsIn(root) {
        const scope = root || document;
        if (typeof window.Plotly === "undefined") return;
        scope.querySelectorAll(".chart-plot, [data-plotly-chart]").forEach((target) => {
            try {
                window.Plotly.purge(target);
            } catch (error) {
                /* ignore */
            }
        });
    }

    function onAfterSwap(event) {
        const target = event.detail && event.detail.target ? event.detail.target : document;
        renderPlotlyCharts(target);
    }

    function bindFindingDetailCharts(root) {
        const scope = root || document;
        scope.querySelectorAll(".stock-finding-detail, .algo-detail").forEach((detailsEl) => {
            if (detailsEl.dataset.plotlyToggleBound === "true") return;
            detailsEl.dataset.plotlyToggleBound = "true";
            detailsEl.addEventListener("toggle", () => {
                if (detailsEl.open) renderPlotlyCharts(detailsEl);
            });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        renderPlotlyCharts(document);
        bindFindingDetailCharts(document);
    });

    document.body.addEventListener("htmx-lite:after-swap", onAfterSwap);

    function resizePlotlyCharts(root) {
        const scope = root || document;
        if (typeof window.Plotly === "undefined") return;
        scope.querySelectorAll(".chart-plot").forEach((target) => {
            try {
                if (target.data && target.data.length) {
                    window.Plotly.Plots.resize(target);
                }
            } catch (error) {
                /* ignore */
            }
        });
    }

    window.renderPlotlyCharts = renderPlotlyCharts;
    window.purgePlotlyChartsIn = purgeChartsIn;
    window.resizePlotlyCharts = resizePlotlyCharts;
})();
