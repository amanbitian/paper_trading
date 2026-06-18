(function () {
    function showLoader(id, visible) {
        const el = document.getElementById(id);
        if (el) el.classList.toggle("is-visible", visible);
    }

    async function fetchHtml(url) {
        const response = await fetch(url, {
            headers: { "X-Requested-With": "fastapi-web" },
        });
        const html = await response.text();
        return { response, html };
    }

    function getTrendsForm() {
        return document.getElementById("trends-filter-form");
    }

    function trendsFormParams() {
        const form = getTrendsForm();
        if (!form) return new URLSearchParams();
        const params = new URLSearchParams();
        const periodInput = document.getElementById("trends-period-input");
        if (periodInput && periodInput.value) {
            params.set("period", periodInput.value);
        }
        new FormData(form).forEach((value, key) => {
            if (key === "rows_auto_toggle") return;
            if (key === "period") return;
            params.set(key, value);
        });
        const rowsModeInput = document.getElementById("trends-rows-mode");
        params.set("rows_mode", rowsModeInput ? rowsModeInput.value || "auto" : "auto");
        return params;
    }

    function selectedNiftyOption() {
        const select = document.querySelector('#trends-filter-form select[name="nifty_index"]');
        if (!select) return null;
        return select.options[select.selectedIndex];
    }

    function autoRowsFromNifty() {
        const option = selectedNiftyOption();
        if (!option) return 100;
        const count = Number(option.dataset.count || 0);
        if (count > 0) return count;
        const match = (option.textContent || "").match(/\((\d+)\)/);
        if (match) return Number(match[1]);
        return 100;
    }

    function setRowsMode(mode) {
        const rowsModeInput = document.getElementById("trends-rows-mode");
        const autoToggle = document.getElementById("trends-rows-auto-toggle");
        const rowsInput = document.getElementById("trends-rows-input");
        const isAuto = mode === "auto";
        if (rowsModeInput) rowsModeInput.value = isAuto ? "auto" : "manual";
        if (autoToggle) autoToggle.checked = isAuto;
        if (rowsInput) {
            rowsInput.dataset.autoRows = isAuto ? "true" : "false";
            rowsInput.classList.toggle("is-auto-rows", isAuto);
        }
    }

    function applyAutoRowsToInput() {
        const rowsInput = document.getElementById("trends-rows-input");
        if (!rowsInput) return;
        const autoValue = autoRowsFromNifty();
        const max = Number(rowsInput.max || 5000);
        rowsInput.value = String(Math.max(50, Math.min(autoValue, max)));
    }

    async function refreshPartial(path, targetSelector, params) {
        const target = document.querySelector(targetSelector);
        if (!target) return;
        const url = `${path}?${params.toString()}`;
        const charts = path.includes("treemap");
        if (charts && window.purgePlotlyChartsIn) {
            window.purgePlotlyChartsIn(target);
        }
        const { response, html } = await fetchHtml(url);
        if (response.ok) {
            target.innerHTML = html;
            document.body.dispatchEvent(
                new CustomEvent("htmx-lite:after-swap", {
                    bubbles: true,
                    detail: { target },
                })
            );
        }
    }

    async function refreshTrends() {
        const params = trendsFormParams();
        const jobs = [
            { loader: "trends-summary-loader", path: "/web/partials/trends/summary", target: "#trends-summary-panel" },
            { loader: "trends-treemap-loader", path: "/web/partials/trends/treemap", target: "#trends-treemap-panel" },
            { loader: "trends-table-loader", path: "/web/partials/trends/table", target: "#trends-table-panel" },
        ];
        await Promise.all(
            jobs.map(async (job) => {
                showLoader(job.loader, true);
                try {
                    await refreshPartial(job.path, job.target, params);
                } finally {
                    showLoader(job.loader, false);
                }
            })
        );
    }

    window.refreshTrends = refreshTrends;

    async function reloadTrendsFilters() {
        const panel = document.getElementById("trends-filters-panel");
        const form = getTrendsForm();
        if (!panel || !form) return;
        const params = trendsFormParams();
        const { response, html } = await fetchHtml(`/web/partials/trends/filters?${params.toString()}`);
        if (response.ok) {
            panel.innerHTML = html;
            bindTrendsFilters();
        }
    }

    async function loadTrendsFilters() {
        const panel = document.getElementById("trends-filters-panel");
        const page = document.getElementById("trends-page");
        if (!panel) return;
        const period = page ? page.getAttribute("data-default-period") || "daily" : "daily";
        const periodInput = document.getElementById("trends-period-input");
        if (periodInput) periodInput.value = period;
        showLoader("trends-filters-loader", true);
        try {
            const params = new URLSearchParams({
                period,
                index_universe: "stocks",
                industry: "All industries",
                nifty_index: "All indices",
                rows_mode: "auto",
                sort_by: "size",
            });
            const { response, html } = await fetchHtml(`/web/partials/trends/filters?${params.toString()}`);
            if (response.ok) {
                panel.innerHTML = html;
                bindTrendsFilters();
            }
        } finally {
            showLoader("trends-filters-loader", false);
        }
    }

    function bindTrendsPeriod() {
        document.querySelectorAll("[data-trends-period]").forEach((button) => {
            if (button.dataset.bound === "true") return;
            button.dataset.bound = "true";
            button.addEventListener("click", async () => {
                document.querySelectorAll("[data-trends-period]").forEach((chip) => {
                    chip.classList.toggle("is-active", chip === button);
                });
                const periodInput = document.getElementById("trends-period-input");
                if (periodInput) periodInput.value = button.getAttribute("data-period") || "daily";
                await refreshTrends();
            });
        });
    }

    function bindTrendsFilters() {
        const form = getTrendsForm();
        if (!form) return;

        const niftySelect = form.querySelector('select[name="nifty_index"]');
        const universeSelect = form.querySelector('select[name="index_universe"]');
        const rowsInput = document.getElementById("trends-rows-input");
        const autoToggle = document.getElementById("trends-rows-auto-toggle");

        if (autoToggle && autoToggle.dataset.bound !== "true") {
            autoToggle.dataset.bound = "true";
            autoToggle.addEventListener("change", async () => {
                if (autoToggle.checked) {
                    setRowsMode("auto");
                    applyAutoRowsToInput();
                } else {
                    setRowsMode("manual");
                }
                await refreshTrends();
            });
        }

        if (rowsInput && rowsInput.dataset.bound !== "true") {
            rowsInput.dataset.bound = "true";
            rowsInput.addEventListener("change", async () => {
                setRowsMode("manual");
                await refreshTrends();
            });
            rowsInput.addEventListener("input", () => {
                setRowsMode("manual");
            });
        }

        if (niftySelect && niftySelect.dataset.bound !== "true") {
            niftySelect.dataset.bound = "true";
            niftySelect.addEventListener("change", async () => {
                const rowsModeInput = document.getElementById("trends-rows-mode");
                const isAuto = !rowsModeInput || rowsModeInput.value === "auto";
                if (isAuto) {
                    setRowsMode("auto");
                    applyAutoRowsToInput();
                }
                await reloadTrendsFilters();
                await refreshTrends();
            });
        }

        if (universeSelect && universeSelect.dataset.bound !== "true") {
            universeSelect.dataset.bound = "true";
            universeSelect.addEventListener("change", async () => {
                await reloadTrendsFilters();
                const rowsModeInput = document.getElementById("trends-rows-mode");
                if (!rowsModeInput || rowsModeInput.value === "auto") {
                    applyAutoRowsToInput();
                }
                await refreshTrends();
            });
        }

        form.querySelectorAll("[data-trends-filter]").forEach((field) => {
            if (field.dataset.bound === "true") return;
            if (
                field === niftySelect ||
                field === universeSelect ||
                field === rowsInput ||
                field === autoToggle
            ) {
                return;
            }
            field.dataset.bound = "true";
            field.addEventListener("change", () => refreshTrends());
        });
    }

    function riskQueryString() {
        const form = document.querySelector("#risk-controls-form");
        const params = new URLSearchParams();
        if (!form) return "";
        form.querySelectorAll("input, select").forEach((field) => {
            if (!field.name || field.disabled) return;
            params.set(field.name, field.value);
        });
        return params.toString();
    }

    async function refreshRiskSections() {
        const qs = riskQueryString();
        const portfolioId = document.querySelector("#risk-portfolio-id");
        if (!portfolioId || !portfolioId.value) return;

        const jobs = [
            { loader: "risk-metrics-loader", panel: "#risk-metrics-panel", url: `/web/partials/risk/metrics?${qs}` },
            {
                loader: "risk-allocation-loader",
                panel: "#risk-allocation-panel",
                url: `/web/partials/risk/allocation?${qs}`,
                charts: true,
            },
            {
                loader: "risk-drawdown-loader",
                panel: "#risk-drawdown-panel",
                url: `/web/partials/risk/drawdown?${qs}`,
                charts: true,
            },
            {
                loader: "risk-concentration-loader",
                panel: "#risk-concentration-panel",
                url: `/web/partials/risk/concentration?${qs}`,
            },
        ];

        await Promise.all(
            jobs.map(async (job) => {
                const target = document.querySelector(job.panel);
                if (!target) return;
                showLoader(job.loader, true);
                try {
                    if (job.charts && window.purgePlotlyChartsIn) {
                        window.purgePlotlyChartsIn(target);
                    }
                    const { response, html } = await fetchHtml(job.url);
                    if (response.ok) {
                        target.innerHTML = html;
                        document.body.dispatchEvent(
                            new CustomEvent("htmx-lite:after-swap", {
                                bubbles: true,
                                detail: { target },
                            })
                        );
                    }
                } finally {
                    showLoader(job.loader, false);
                }
            })
        );
    }

    function bindRiskControls() {
        const form = document.querySelector("#risk-controls-form");
        if (!form) return;
        form.querySelectorAll("[data-risk-control]").forEach((field) => {
            if (field.dataset.bound === "true") return;
            field.dataset.bound = "true";
            field.addEventListener("change", () => refreshRiskSections());
        });
        if (window.bindHtmxLite) window.bindHtmxLite(form);
    }

    async function initTrendsPage() {
        if (!document.getElementById("trends-page")) return;
        bindTrendsPeriod();
        await loadTrendsFilters();
        await refreshTrends();
    }

    async function initRiskPage() {
        if (!document.getElementById("risk-page")) return;
        bindRiskControls();
        await refreshRiskSections();
    }

    document.addEventListener("DOMContentLoaded", function () {
        initTrendsPage();
        initRiskPage();
    });

    document.body.addEventListener("risk-refreshed", function () {
        refreshRiskSections();
    });
})();
