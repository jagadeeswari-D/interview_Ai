/* InterviewIQ — Shared product page interactions (vanilla JS).
 * Used by Replay, Resume, Analytics, Reports, Profile and Settings pages.
 * Features:
 *  - Scroll reveal (.iq-reveal → .is-in) via IntersectionObserver
 *  - Animated counters ([data-count]) on viewport entry
 *  - Progress-bar / readiness-ring CSSOM animation from data-* attributes
 *  - All values applied via the CSSOM (no inline style="" — CSP-safe)
 *  - Respects prefers-reduced-motion: everything revealed up front, no animation
 * No functional, business or data logic lives here.
 */
(function () {
  "use strict";

  var prefersReduced = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- 1. Scroll reveal (one-shot) ---- */
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

  /* ---- 2. Animated counters ---- */
  function animateCounters() {
    var counters = document.querySelectorAll("[data-count]");
    if (!counters.length) return;
    if (prefersReduced) return;

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        observer.unobserve(el);
        var target = parseInt(el.getAttribute("data-count"), 10) || 0;
        var duration = 600;
        var start = null;
        function step(timestamp) {
          if (!start) start = timestamp;
          var progress = Math.min((timestamp - start) / duration, 1);
          var eased = 1 - Math.pow(1 - progress, 3);
          el.textContent = String(Math.round(eased * target));
          if (progress < 1) {
            window.requestAnimationFrame(step);
          }
        }
        window.requestAnimationFrame(step);
      });
    }, { threshold: 0.5 });
    counters.forEach(function (el) { observer.observe(el); });
  }

  /* ---- 3. Progress bars (skill fills, roadmap fills) ---- */
  function initProgressBars() {
    var fills = document.querySelectorAll(".iq-skill-fill, .iq-roadmap-fill, .meter-fill");
    if (!fills.length) return;

    function reveal(fill) {
      var parent = fill.closest("[data-bar]") || fill.closest("[data-w]");
      var value = (parent && (parent.getAttribute("data-bar") || parent.getAttribute("data-w"))) ||
                  fill.getAttribute("data-w") ||
                  fill.getAttribute("data-fill");
      var pct = parseInt(value, 10);
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
    }, { threshold: 0.4 });
    fills.forEach(function (fill) { observer.observe(fill); });
  }

  /* ---- 4. Readiness ring ---- */
  function initReadinessRing() {
    var rings = document.querySelectorAll(".iq-ring[data-score]");
    if (!rings.length) return;
    rings.forEach(function (ring) {
      var value = parseFloat(ring.getAttribute("data-score"));
      if (!isNaN(value)) {
        ring.style.setProperty(
          "--r",
          Math.max(0, Math.min(100, value)) + "%"
        );
      }
    });
  }

  /* ---- 5. Boot ---- */
  function boot() {
    initReveal();
    animateCounters();
    initProgressBars();
    initReadinessRing();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
