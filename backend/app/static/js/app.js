(function () {
    let syncPollTimer = null;
    let syncWasRunning = false;

    function closestFormData(element) {
        const data = new URLSearchParams();
        const includeSelector = element.getAttribute("hx-include");
        let roots = [];
        if (includeSelector) {
            roots = Array.from(document.querySelectorAll(includeSelector));
        } else if (element.tagName === "FORM") {
            roots = [element];
        }
        roots.forEach((root) => {
            const fields = root.matches("input, select, textarea")
                ? [root]
                : root.querySelectorAll("input, select, textarea");
            fields.forEach((field) => {
                if (!field.name || field.disabled) return;
                if ((field.type === "checkbox" || field.type === "radio") && !field.checked) return;
                data.append(field.name, field.value);
            });
        });
        if (
            element.name &&
            element.value &&
            !((element.type === "checkbox" || element.type === "radio") && !element.checked)
        ) {
            data.append(element.name, element.value);
        }
        return data;
    }

    function showIndicator(selector, visible) {
        if (!selector) return;
        const indicator = document.querySelector(selector);
        if (indicator) indicator.classList.toggle("is-visible", visible);
    }

    function dispatchHxTrigger(response) {
        const trigger = response.headers.get("HX-Trigger");
        if (!trigger) return;
        trigger.split(",").forEach((name) => {
            const eventName = name.trim();
            if (!eventName) return;
            document.body.dispatchEvent(new CustomEvent(eventName, { bubbles: true }));
        });
    }

    function stopSyncPolling() {
        if (syncPollTimer) {
            window.clearInterval(syncPollTimer);
            syncPollTimer = null;
        }
    }

    async function swapHtml(target, html, swapMode) {
        if (!target) return null;
        if (swapMode.includes("outerHTML")) {
            target.outerHTML = html;
            return document.querySelector(`#${target.id}`) || target.parentElement;
        }
        target.innerHTML = html;
        return target;
    }

    async function fetchPartial(url, method, data) {
        const options = {
            method,
            headers: { "X-Requested-With": "fastapi-web" },
        };
        if (method === "POST") {
            options.headers["Content-Type"] = "application/x-www-form-urlencoded";
            options.body = data;
        }
        const response = await fetch(url, options);
        const html = await response.text();
        return { response, html };
    }

    function buildMoversRefreshUrl() {
        const form = document.querySelector("#movers-filter-form");
        if (!form) {
            return "/web/partials/explore/top-movers?bucket=gainers&sort=trend";
        }
        const params = new URLSearchParams();
        form.querySelectorAll("input, select").forEach((field) => {
            if (field.name) params.set(field.name, field.value);
        });
        return `/web/partials/explore/top-movers?${params.toString()}`;
    }

    function buildAllStocksRefreshUrl() {
        const form = document.querySelector("#all-stocks-filter-form");
        if (!form) {
            return "/web/partials/explore/all-stocks";
        }
        const params = new URLSearchParams();
        form.querySelectorAll("input, select").forEach((field) => {
            if (field.name) params.set(field.name, field.value);
        });
        return `/web/partials/explore/all-stocks?${params.toString()}`;
    }

    function buildSequentialRefreshUrl() {
        const active = document.querySelector("#sequential-rankings-panel .tab-button.is-active");
        const side = active && active.textContent.toLowerCase().includes("sell") ? "sell" : "buy";
        return `/web/partials/explore/sequential-rankings?side=${side}`;
    }

    async function refreshExploreSections() {
        const jobs = [
            { selector: "#index-cards", url: "/web/partials/explore/index-cards", swap: "outerHTML" },
        ];
        if (document.querySelector("#market-movers-panel")) {
            jobs.push({
                selector: "#market-movers-panel",
                url: buildMoversRefreshUrl(),
                swap: "outerHTML",
            });
        } else if (document.querySelector("#all-stocks-panel")) {
            jobs.push({
                selector: "#all-stocks-panel",
                url: buildAllStocksRefreshUrl(),
                swap: "outerHTML",
            });
        } else if (document.querySelector("#sequential-rankings-panel")) {
            jobs.push({
                selector: "#sequential-rankings-panel",
                url: buildSequentialRefreshUrl(),
                swap: "outerHTML",
            });
        }

        const swappedRoots = [];
        for (const job of jobs) {
            const target = document.querySelector(job.selector);
            if (!target) continue;
            try {
                const { response, html } = await fetchPartial(job.url, "GET");
                if (!response.ok) continue;
                await swapHtml(target, html, job.swap);
                const refreshed = document.querySelector(job.selector) || target.parentElement;
                if (refreshed) swappedRoots.push(refreshed);
            } catch (error) {
                console.error("Explore refresh failed", job.selector, error);
            }
        }
        // Bind only the newly inserted subtrees; fall back to full doc if nothing swapped.
        if (swappedRoots.length > 0) {
            swappedRoots.forEach((root) => bindHtmxLite(root));
        } else {
            bindHtmxLite(document);
        }
        maybeStartSyncPolling();
    }

    async function pollSyncStatus() {
        const panel = document.querySelector("#sync-status");
        if (!panel) {
            stopSyncPolling();
            return;
        }
        try {
            const { response, html } = await fetchPartial("/web/partials/explore/sync-status", "GET");
            if (!response.ok) return;
            const wasRunning = panel.dataset.syncRunning === "true";
            await swapHtml(panel, html, "outerHTML");
            const updated = document.querySelector("#sync-status");
            bindHtmxLite(updated || panel.parentElement || document);
            const isRunning = updated && updated.dataset.syncRunning === "true";
            if (wasRunning && !isRunning) {
                document.body.dispatchEvent(
                    new CustomEvent("market-sync-completed", { bubbles: true })
                );
                await refreshExploreSections();
            }
            if (!isRunning) {
                stopSyncPolling();
            }
        } catch (error) {
            console.error("Sync status poll failed", error);
        }
    }

    function maybeStartSyncPolling() {
        const panel = document.querySelector("#sync-status");
        if (!panel) {
            stopSyncPolling();
            return;
        }
        const isRunning = panel.dataset.syncRunning === "true";
        if (isRunning) {
            syncWasRunning = true;
            if (!syncPollTimer) {
                syncPollTimer = window.setInterval(pollSyncStatus, 5000);
            }
            return;
        }
        if (syncWasRunning) {
            syncWasRunning = false;
            refreshExploreSections();
        }
        stopSyncPolling();
    }

    async function requestFragment(element) {
        const getUrl = element.getAttribute("hx-get");
        const postUrl = element.getAttribute("hx-post");
        const targetSelector = element.getAttribute("hx-target");
        const swapMode = element.getAttribute("hx-swap") || "innerHTML";
        const indicatorSelector = element.getAttribute("hx-indicator");
        const target = targetSelector ? document.querySelector(targetSelector) : element;
        if (!target || (!getUrl && !postUrl)) return;

        const method = postUrl ? "POST" : "GET";
        const data = closestFormData(element);
        let url = getUrl || postUrl;

        showIndicator(indicatorSelector, true);
        element.classList.add("htmx-request");
        target.classList.add("htmx-request");
        try {
            if (method === "GET" && data.toString()) {
                const separator = url.includes("?") ? "&" : "?";
                url = `${url}${separator}${data.toString()}`;
            }
            const { response, html } = await fetchPartial(
                url,
                method,
                method === "POST" ? data : null
            );
            if (!response.ok) {
                if (target.id === "sync-status" && html) {
                    await swapHtml(target, html, swapMode);
                    bindHtmxLite(document);
                    maybeStartSyncPolling();
                } else {
                    target.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>Request failed (${response.status}).</p></div>`;
                }
                return;
            }
            const swapped = await swapHtml(target, html, swapMode);
            const swappedTarget =
                (targetSelector ? document.querySelector(targetSelector) : null) || swapped || target;
            bindHtmxLite(swappedTarget || document);
            dispatchHxTrigger(response);
            document.body.dispatchEvent(
                new CustomEvent("htmx-lite:after-swap", {
                    bubbles: true,
                    detail: { target: swappedTarget },
                })
            );
            maybeStartSyncPolling();
            if (response.headers.get("HX-Trigger") === "market-sync-started") {
                syncWasRunning = true;
            }
        } catch (error) {
            if (target.id === "sync-status") {
                target.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>${error.message}</p></div>`;
            } else {
                target.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>${error.message}</p></div>`;
            }
        } finally {
            showIndicator(indicatorSelector, false);
            element.classList.remove("htmx-request");
            const refreshedTarget = targetSelector
                ? document.querySelector(targetSelector)
                : target;
            if (refreshedTarget) refreshedTarget.classList.remove("htmx-request");
            if (method === "POST" && targetSelector === "#backtesting-results") {
                document.body.dispatchEvent(
                    new CustomEvent("backtesting:run-finished", { bubbles: true })
                );
            }
        }
    }

    function setActiveTab(element) {
        const group = element.getAttribute("data-tab-group");
        if (group) {
            document.querySelectorAll(`[data-tab-group="${group}"]`).forEach((tab) => {
                tab.classList.toggle("is-active", tab === element);
            });
            return;
        }
        const row = element.closest(".tab-row, .toolbar-row, .explore-main-tabs");
        if (!row) return;
        const selector = element.classList.contains("chip")
            ? ".chip"
            : element.classList.contains("main-tab-button")
              ? ".main-tab-button"
              : ".tab-button";
        row.querySelectorAll(selector).forEach((tab) => {
            tab.classList.toggle("is-active", tab === element);
        });
    }

    function debounce(fn, wait) {
        let timer;
        return function (...args) {
            window.clearTimeout(timer);
            timer = window.setTimeout(() => fn.apply(this, args), wait);
        };
    }

    function bindElement(element) {
        if (element.dataset.htmxLiteBound === "true") return;
        element.dataset.htmxLiteBound = "true";

        if (element.tagName === "FORM") {
            element.addEventListener("submit", function (event) {
                event.preventDefault();
                requestFragment(element);
            });
        }

        const trigger = element.getAttribute("hx-trigger") || "click";
        const delayMatch = trigger.match(/delay:(\d+)ms/);
        const delay = delayMatch ? Number(delayMatch[1]) : 0;
        const handler = delay
            ? debounce(() => requestFragment(element), delay)
            : () => requestFragment(element);

        if (trigger.includes("keyup")) {
            element.addEventListener("keyup", handler);
        }
        if (trigger.includes("changed") || trigger.includes("change")) {
            element.addEventListener("change", handler);
        }

        const usesClick =
            element.tagName === "BUTTON" ||
            (element.tagName === "A" && element.hasAttribute("hx-get")) ||
            (element.tagName === "INPUT" && element.type === "button");

        if (
            usesClick ||
            (!trigger.includes("keyup") &&
                !trigger.includes("change") &&
                !trigger.includes("changed") &&
                element.tagName !== "FORM")
        ) {
            element.addEventListener("click", function (event) {
                if (element.tagName === "BUTTON" || element.tagName === "A") {
                    event.preventDefault();
                }
                if (
                    element.classList.contains("tab-button") ||
                    element.classList.contains("main-tab-button") ||
                    element.classList.contains("chip")
                ) {
                    setActiveTab(element);
                }
                if (element.tagName !== "FORM") {
                    handler();
                }
            });
        }
    }

    function bindHtmxLite(root) {
        root.querySelectorAll("[hx-get], [hx-post]").forEach(bindElement);
    }

    function bindSidebar() {
        const button = document.querySelector("[data-sidebar-toggle]");
        const sidebar = document.querySelector("#sidebar");
        const backdrop = document.querySelector("#sidebar-backdrop");
        if (!button || !sidebar || button.dataset.sidebarBound === "true") return;
        button.dataset.sidebarBound = "true";
        const toggle = () => {
            sidebar.classList.toggle("is-open");
            backdrop && backdrop.classList.toggle("is-open");
        };
        button.addEventListener("click", toggle);
        backdrop && backdrop.addEventListener("click", toggle);
    }

    function bindLegacyMode() {
        const button = document.getElementById("legacy-mode-btn");
        const host = document.getElementById("legacy-mode-result");
        const loader = document.getElementById("legacy-mode-loader");
        if (!button || button.dataset.legacyBound === "true") return;
        button.dataset.legacyBound = "true";
        button.addEventListener("click", async () => {
            if (loader) loader.classList.add("is-visible");
            button.disabled = true;
            try {
                const { response, html } = await fetchPartial(
                    "/web/legacy/start",
                    "POST",
                    new URLSearchParams()
                );
                if (host) {
                    host.innerHTML = html;
                    bindHtmxLite(host);
                }
                const openUrl = host?.querySelector("[data-legacy-url]")?.getAttribute("data-legacy-url");
                if (response.ok && openUrl) {
                    window.open(openUrl, "_blank", "noopener,noreferrer");
                }
            } catch (error) {
                if (host) {
                    host.innerHTML = `<div class="info-banner danger"><span class="info-dot"></span><p>${error.message}</p></div>`;
                }
            } finally {
                if (loader) loader.classList.remove("is-visible");
                button.disabled = false;
            }
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        bindSidebar();
        bindLegacyMode();
        bindHtmxLite(document);
        maybeStartSyncPolling();
    });

    document.body.addEventListener("market-sync-completed", refreshExploreSections);
    document.body.addEventListener("market-sync-started", function () {
        syncWasRunning = true;
        maybeStartSyncPolling();
    });

    window.bindHtmxLite = bindHtmxLite;
    window.refreshExploreSections = refreshExploreSections;
})();
