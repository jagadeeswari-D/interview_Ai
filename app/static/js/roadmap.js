/* InterviewIQ — Learning Roadmap interactions (vanilla JS).
 * - reveals hero / progress / journey on scroll (once)
 * - animates the journey path draw across the phase cards
 * - animates the progress counter smoothly
 * - triggers meter glow after fill
 * Values are applied via the CSSOM (style-src 'self' blocks inline style="").
 * No products, data, or business logic is touched here — styling only.
 * Respects prefers-reduced-motion: everything is revealed up front, no
 * animation, functionality fully intact.
 */
(function () {
  "use strict";

  var prefersReduced = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- Scroll reveal (one-shot) ---- */
  function initReveal() {
    var items = document.querySelectorAll(".rm-reveal");
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

  /* ---- Animate progress counter smoothly ---- */
  function animateProgressCounter() {
    var countEl = document.querySelector(".rm-progress-count");
    if (!countEl) return;
    if (prefersReduced) return;

    var text = countEl.textContent || "";
    var match = text.match(/^(\d+)/);
    if (!match) return;
    var target = parseInt(match[1], 10);
    if (isNaN(target) || target === 0) return;

    /* Read the denominator from the existing span */
    var denomSpan = countEl.querySelector(".score-denominator");
    var denomText = denomSpan ? denomSpan.textContent : "";

    /* Build an animated wrapper; the denominator span stays static */
    var wrapper = document.createElement("span");
    wrapper.className = "rm-count-animated";
    wrapper.textContent = "0";

    /* Replace text node but keep the span */
    countEl.innerHTML = "";
    countEl.appendChild(wrapper);
    if (denomSpan) countEl.appendChild(denomSpan);

    var duration = 800;
    var start = null;

    function step(timestamp) {
      if (!start) start = timestamp;
      var progress = Math.min((timestamp - start) / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3);
      wrapper.textContent = String(Math.round(eased * target));
      if (progress < 1) {
        window.requestAnimationFrame(step);
      }
    }
    window.requestAnimationFrame(step);
  }

  /* ---- Trigger meter glow after the bar fills ---- */
  function initMeterGlow() {
    var fills = document.querySelectorAll(".meter-fill[data-fill]");
    if (!fills.length) return;
    if (prefersReduced) return;

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          var fill = entry.target;
          observer.unobserve(fill);
          /* Delay glow start until after the CSS fill transition completes */
          window.setTimeout(function () {
            fill.classList.add("meter-animating");
          }, 1200);
        }
      });
    }, { threshold: 0.4 });
    fills.forEach(function (fill) { observer.observe(fill); });
  }

  /* ---- Company context selector hint (shared _company_select.html) ---- */
  function initCompanyContext() {
    var select = document.querySelector("[data-company-select]");
    var note = document.querySelector("[data-company-context]");
    if (!select || !note) return;
    function update() {
      var option = select.options[select.selectedIndex];
      var text = option ? option.getAttribute("data-company-short") : "";
      note.textContent = text || "";
    }
    select.addEventListener("change", update);
    update();
  }

  function boot() {
    initReveal();
    animateProgressCounter();
    initMeterGlow();
    initCompanyContext();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
