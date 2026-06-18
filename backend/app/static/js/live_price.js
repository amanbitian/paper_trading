/*
 * Live-price overlay poller.
 *
 * Decouples the stock ticker from the heavy stock-detail snapshot: any element
 * with [data-live-price-url] is refreshed on page load and then every
 * data-poll-seconds (default 30s) by fetching a small HTML partial and swapping
 * its innerHTML. Works with the app's custom mini-htmx by re-scanning on the
 * "htmx-lite:after-swap" event, so it also activates when the stock detail is
 * injected via a partial swap. The last good value is kept on transient errors.
 */
(function () {
    "use strict";

    var POLL_MIN_SECONDS = 5;

    async function fetchPrice(el) {
        var url = el.getAttribute("data-live-price-url");
        if (!url || !document.contains(el)) return;
        try {
            var resp = await fetch(url, {
                headers: { "X-Requested-With": "fastapi-web" },
                credentials: "same-origin",
            });
            if (!resp.ok) return; // keep the last good value
            var html = await resp.text();
            if (document.contains(el)) el.innerHTML = html;
        } catch (err) {
            /* network blip — keep the last good value */
        }
    }

    function init(root) {
        var scope = root && root.querySelectorAll ? root : document;
        scope.querySelectorAll("[data-live-price-url]").forEach(function (el) {
            if (el.dataset.livePriceBound === "true") return;
            el.dataset.livePriceBound = "true";

            var seconds = parseInt(el.getAttribute("data-poll-seconds") || "30", 10);
            if (isNaN(seconds) || seconds < POLL_MIN_SECONDS) seconds = 30;

            fetchPrice(el); // immediate refresh for the freshest value
            var timer = window.setInterval(function () {
                if (!document.contains(el)) {
                    window.clearInterval(timer);
                    return;
                }
                fetchPrice(el);
            }, seconds * 1000);
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        init(document);
    });

    // The custom mini-htmx in app.js dispatches this after every fragment swap.
    document.addEventListener("htmx-lite:after-swap", function () {
        init(document);
    });
})();
