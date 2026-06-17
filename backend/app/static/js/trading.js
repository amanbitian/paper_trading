(function () {
    function formatInr(amount) {
        const value = Number(amount);
        if (!Number.isFinite(value)) return "Rs 0.00";
        return `Rs ${value.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }

    function escapeHtml(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function updateHoldingEstimate() {
        const qty = document.querySelector("#add-holding-form [name='quantity']");
        const price = document.querySelector("#add-holding-form [name='buy_price']");
        const charges = document.querySelector("#add-holding-form [name='charges']");
        const target = document.querySelector("#holding-estimate");
        if (!target || !qty || !price) return;
        const total =
            (Number(qty.value) || 0) * (Number(price.value) || 0) + (Number(charges?.value) || 0);
        target.textContent = `Estimated amount: ${formatInr(total)}`;
    }

    function bindEstimateFields() {
        document.querySelectorAll("[data-estimate-field]").forEach((field) => {
            if (field.dataset.estimateBound === "true") return;
            field.dataset.estimateBound = "true";
            field.addEventListener("input", updateHoldingEstimate);
            field.addEventListener("change", updateHoldingEstimate);
        });
        updateHoldingEstimate();
    }

    function renderHoldingSelectedPanel(data) {
        const host = document.getElementById("holding-selected-stock");
        if (!host || !data) return;

        const sectorParts = [data.sector, data.industry].filter(Boolean);
        const priceLine =
            data.latest_close != null
                ? `<p class="holding-selected-price">Latest close: ${escapeHtml(formatInr(data.latest_close))}</p>`
                : "";
        const dateLine = data.latest_date
            ? `<p class="holding-selected-date muted">As of: ${escapeHtml(data.latest_date)}</p>`
            : "";
        const sectorLine = sectorParts.length
            ? `<p class="holding-selected-sector muted">${escapeHtml(sectorParts.join(" · "))}</p>`
            : "";
        const badgeClass = data.has_prices ? "success" : "warning";
        const badgeLabel = data.status_label || (data.has_prices ? "Has prices" : "Missing prices");

        host.outerHTML = `
            <article class="holding-selected-stock-panel" id="holding-selected-stock">
                <p class="holding-selected-eyebrow">Selected stock</p>
                <h3 class="holding-selected-name">${escapeHtml(data.company_name || data.symbol)}</h3>
                <p class="holding-selected-tickers">${escapeHtml(data.symbol)} · ${escapeHtml(data.exchange)} · ${escapeHtml(data.yahoo_ticker || data.yahoo_symbol || "")}</p>
                ${priceLine}
                ${dateLine}
                ${sectorLine}
                <span class="status-badge ${badgeClass}"><span class="info-dot"></span>${escapeHtml(badgeLabel)}</span>
            </article>`;
    }

    function clearHoldingSelectedPanel() {
        const host = document.getElementById("holding-selected-stock");
        if (!host) return;
        host.outerHTML = `
            <div id="holding-selected-stock" class="holding-selected-stock-panel is-empty muted">
                <strong>No stock selected</strong>
                <p>Search above or open from a stock detail page.</p>
            </div>`;
    }

    function applyHoldingStockData(data, options = {}) {
        if (!data || !data.stock_id) return;

        const holdingHidden = document.getElementById("holding-stock-id");
        const symbolHidden = document.getElementById("holding-stock-symbol-hidden");
        const exchangeHidden = document.getElementById("holding-stock-exchange-hidden");
        const searchInput = document.getElementById("holding-stock-search-input");
        const buyPrice = document.getElementById("holding-buy-price");
        const purchaseDate = document.getElementById("holding-purchase-date");

        if (holdingHidden) holdingHidden.value = String(data.stock_id);
        if (symbolHidden) symbolHidden.value = data.symbol || "";
        if (exchangeHidden) exchangeHidden.value = data.exchange || "";
        if (searchInput && options.updateSearch !== false) {
            searchInput.value = data.search_label || `${data.company_name || data.symbol} · ${data.symbol} · ${data.exchange}`;
        }

        renderHoldingSelectedPanel(data);

        if (buyPrice) {
            if (data.latest_close != null && Number.isFinite(Number(data.latest_close))) {
                buyPrice.value = Number(data.latest_close).toFixed(2);
            } else if (options.clearPriceIfMissing) {
                buyPrice.value = "";
            }
        }
        if (purchaseDate && data.purchase_date_default) {
            purchaseDate.value = String(data.purchase_date_default).slice(0, 10);
        } else if (purchaseDate && data.latest_date) {
            purchaseDate.value = String(data.latest_date).slice(0, 10);
        }

        updateHoldingEstimate();
    }

    function stockDataFromCard(card) {
        const latestPrice = card.getAttribute("data-latest-price");
        const latestDate = card.getAttribute("data-latest-date");
        return {
            stock_id: Number(card.getAttribute("data-stock-id")),
            company_name: card.getAttribute("data-stock-label"),
            symbol: card.getAttribute("data-stock-symbol"),
            exchange: card.getAttribute("data-stock-exchange"),
            yahoo_ticker: card.getAttribute("data-stock-yahoo"),
            sector: card.getAttribute("data-stock-sector") || "",
            industry: card.getAttribute("data-stock-industry") || "",
            latest_close: latestPrice ? Number(latestPrice) : null,
            latest_date: latestDate || null,
            has_prices: Boolean(latestPrice),
            status_label: latestPrice ? "Has prices" : "Missing prices",
            status_tone: latestPrice ? "success" : "warning",
            search_label: `${card.getAttribute("data-stock-label")} · ${card.getAttribute("data-stock-symbol")} · ${card.getAttribute("data-stock-exchange")}`,
            purchase_date_default: latestDate || null,
        };
    }

    function selectStock(card, context) {
        const stockId = card.getAttribute("data-stock-id");
        const label = card.getAttribute("data-stock-label");
        const symbol = card.getAttribute("data-stock-symbol");
        if (!stockId) return;

        const holdingHidden = document.querySelector("#holding-stock-id");
        const paperHidden = document.querySelector("#paper-form-stock-id");
        const paperChip = document.querySelector("#paper-selected-stock");

        if (context === "holding" || !context) {
            applyHoldingStockData(stockDataFromCard(card), { updateSearch: true, clearPriceIfMissing: true });
            if (!document.getElementById("holding-selected-stock")) {
                if (holdingHidden) holdingHidden.value = stockId;
            }
        }
        if (context === "paper" || !context) {
            if (paperHidden) paperHidden.value = stockId;
            const paperStockField = document.querySelector("#paper-stock-id");
            if (paperStockField) paperStockField.value = stockId;
            if (paperChip) {
                paperChip.textContent = `${label} (${symbol})`;
                paperChip.classList.remove("muted");
            }
            refreshOrderPreview();
        }

        card.closest(".search-results")?.querySelectorAll(".search-result-card").forEach((item) => {
            item.classList.toggle("is-selected", item === card);
        });
    }

    function bindStockSelection(root) {
        root.querySelectorAll(".search-result-card.is-selectable").forEach((card) => {
            if (card.dataset.stockSelectBound === "true") return;
            card.dataset.stockSelectBound = "true";
            const handler = () => {
                const inPaper = Boolean(card.closest("#paper-stock-search-results"));
                selectStock(card, inPaper ? "paper" : "holding");
            };
            card.addEventListener("click", handler);
            card.querySelector(".stock-pick-button")?.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                handler();
            });
        });
    }

    async function fetchPartial(url, targetSelector, swapMode = "outerHTML") {
        const target = document.querySelector(targetSelector);
        if (!target || !url) return;
        try {
            const response = await fetch(url, { headers: { "X-Requested-With": "fastapi-web" } });
            const html = await response.text();
            if (swapMode.includes("outerHTML")) {
                target.outerHTML = html;
            } else {
                target.innerHTML = html;
            }
            window.bindHtmxLite?.(document);
            bindStockSelection(document);
        } catch (error) {
            console.error("Partial fetch failed", url, error);
        }
    }

    function buildPreviewUrl() {
        const form = document.querySelector("#paper-order-form");
        const portfolioId = document.querySelector("#paper-form-portfolio-id")?.value;
        const stockId = document.querySelector("#paper-form-stock-id")?.value;
        if (!form || !portfolioId) return null;
        const data = new URLSearchParams();
        data.set("portfolio_id", portfolioId);
        if (stockId) data.set("stock_id", stockId);
        form.querySelectorAll("input, select, textarea").forEach((field) => {
            if (!field.name || field.disabled) return;
            if ((field.type === "radio" || field.type === "checkbox") && !field.checked) return;
            data.set(field.name, field.value);
        });
        return `/web/partials/paper-trading/order-preview?${data.toString()}`;
    }

    async function refreshHoldings() {
        const select = document.querySelector("#add-holding-form [name='portfolio_id']");
        if (!select || !select.value) return;
        const url = `/web/partials/portfolio/holdings?portfolio_id=${encodeURIComponent(select.value)}`;
        const wrap = document.querySelector("#portfolio-holdings-wrap");
        if (wrap) wrap.setAttribute("hx-get", url);
        await fetchPartial(url, "#portfolio-holdings");
    }

    async function refreshOrderPreview() {
        const url = buildPreviewUrl();
        if (!url) return;
        await fetchPartial(url, "#order-preview");
    }

    function syncPaperPortfolioHidden() {
        const select = document.querySelector("#paper-portfolio-id");
        const hidden = document.querySelector("#paper-form-portfolio-id");
        if (select && hidden) hidden.value = select.value || "";
    }

    async function refreshPaperSections() {
        syncPaperPortfolioHidden();
        const portfolioId = document.querySelector("#paper-form-portfolio-id")?.value;
        if (!portfolioId) return;
        await Promise.all([
            fetchPartial(
                `/web/partials/paper-trading/open-positions?portfolio_id=${encodeURIComponent(portfolioId)}`,
                "#open-positions"
            ),
            fetchPartial(
                `/web/partials/paper-trading/order-history?portfolio_id=${encodeURIComponent(portfolioId)}`,
                "#order-history"
            ),
            refreshOrderPreview(),
        ]);
    }

    function bindSideToggle() {
        const options = document.querySelectorAll(".side-option");
        options.forEach((option) => {
            const input = option.querySelector("input[type='radio']");
            if (!input || input.dataset.sideBound === "true") return;
            input.dataset.sideBound = "true";
            input.addEventListener("change", () => {
                options.forEach((item) => item.classList.remove("is-active"));
                if (input.checked) option.classList.add("is-active");
                refreshOrderPreview();
            });
        });
    }

    function bindOrderTypeFields() {
        const orderType = document.querySelector("#paper-order-type");
        if (!orderType || orderType.dataset.orderTypeBound === "true") return;
        orderType.dataset.orderTypeBound = "true";
        const updateVisibility = () => {
            const value = orderType.value;
            document.querySelectorAll(".limit-only").forEach((el) => {
                el.hidden = value !== "LIMIT";
            });
            document.querySelectorAll(".stop-only").forEach((el) => {
                el.hidden = value !== "STOP_LOSS";
            });
            refreshOrderPreview();
        };
        orderType.addEventListener("change", updateVisibility);
        updateVisibility();
    }

    function bindPreviewFields() {
        document.querySelectorAll("[data-preview-field]").forEach((field) => {
            if (field.dataset.previewBound === "true") return;
            field.dataset.previewBound = "true";
            const handler = () => refreshOrderPreview();
            field.addEventListener("input", handler);
            field.addEventListener("change", handler);
        });
    }

    function bindPortfolioSelectors() {
        const holdingSelect = document.querySelector("#add-holding-form [name='portfolio_id']");
        if (holdingSelect && holdingSelect.dataset.holdingsBound !== "true") {
            holdingSelect.dataset.holdingsBound = "true";
            holdingSelect.addEventListener("change", refreshHoldings);
        }
        const paperSelect = document.querySelector("#paper-portfolio-id");
        if (paperSelect && paperSelect.dataset.paperBound !== "true") {
            paperSelect.dataset.paperBound = "true";
            paperSelect.addEventListener("change", refreshPaperSections);
        }
    }

    function initHoldingPrefill() {
        const prefillEl = document.getElementById("holding-prefill-json");
        if (!prefillEl) return;
        try {
            const data = JSON.parse(prefillEl.textContent || "{}");
            applyHoldingStockData(data, { updateSearch: false });
        } catch (error) {
            console.error("[trading] holding prefill parse failed", error);
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        bindEstimateFields();
        initHoldingPrefill();
        bindStockSelection(document);
        bindPortfolioSelectors();
        bindSideToggle();
        bindOrderTypeFields();
        bindPreviewFields();
        syncPaperPortfolioHidden();
        if (document.querySelector("#paper-order-form")) {
            refreshOrderPreview();
        }
    });

    document.body.addEventListener("portfolio-created", () => {
        window.location.reload();
    });

    document.body.addEventListener("holding-added", () => {
        refreshHoldings();
    });

    document.body.addEventListener("paper-order-submitted", () => {
        refreshPaperSections();
    });

    document.body.addEventListener("paper-preview-refresh", () => {
        refreshOrderPreview();
    });

    document.body.addEventListener("holdings-refresh", () => {
        refreshHoldings();
    });

    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            mutation.addedNodes.forEach((node) => {
                if (!(node instanceof HTMLElement)) return;
                bindStockSelection(node);
                if (window.bindHtmxLite) window.bindHtmxLite(node);
            });
        });
    });
    observer.observe(document.body, { childList: true, subtree: true });
})();
