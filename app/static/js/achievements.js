/* InterviewIQ — Achievements gallery (vanilla JS).
 * - reveals sections on scroll (once)
 * - animates locked achievement progress bars into view
 * Values are read from data-* attributes (not inline styles, which the
 * strict CSP style-src 'self' blocks) and applied via the CSSOM, which is
 * CSP-safe. Mirrors the dashboard.js skill-bar pattern.
 */
(function () {
  "use strict";

  var prefersReduced = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- Scroll reveal (one-shot) ---- */
  function initReveal() {
    var items = document.querySelectorAll(".iq-reveal");
    if (!items.length) return;
    if (!("IntersectionObserver" in window) || prefersReduced) {
      items.forEach(function (el) { el.classList.add("is-in"); });
      return;
    }
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-in");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });
    items.forEach(function (el) { observer.observe(el); });
  }

  /* ---- Locked achievement progress bars ---- */
  function initTrackFills() {
    var fills = document.querySelectorAll(".ach-track-fill");
    if (!fills.length) return;
    function reveal(fill) {
      var pct = parseInt(fill.getAttribute("data-w") || "0", 10);
      if (!isNaN(pct)) {
        fill.style.width = Math.max(0, Math.min(100, pct)) + "%";
      }
    }
    if (!("IntersectionObserver" in window) || prefersReduced) {
      fills.forEach(reveal);
      return;
    }
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          reveal(entry.target);
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.3, rootMargin: "0px 0px -40px 0px" });
    fills.forEach(function (el) { observer.observe(el); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initReveal();
    initTrackFills();
  });
})();