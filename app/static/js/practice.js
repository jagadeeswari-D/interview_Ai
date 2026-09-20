/* InterviewIQ — Smart Practice interactions (vanilla JS).
 *  - reveals sections on scroll (staggered, one-shot)
 *  - suggestion chips fill the topic input
 *  - answer character counter
 *  - loading state on submit buttons (no duplicate logic, no backend change)
 *  - results page: animates overall ring, dimension bars and score counters
 * All dynamic styles are applied through the CSSOM (CSP-safe) and read from
 * data-* attributes; inline style="..." is only a no-JS fallback.
 */
(function () {
  "use strict";

  var prefersReduced = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }
  function easeOutExpo(t) { return t === 1 ? 1 : 1 - Math.pow(2, -10 * t); }

  /* ---- Scroll reveal (matches the Dashboard behavior) ---- */
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

  /* ---- Suggestion chips fill the topic input ---- */
  function initSuggestions() {
    var input = document.getElementById("topic");
    if (!input) return;
    var chips = document.querySelectorAll("[data-suggest]");
    Array.prototype.forEach.call(chips, function (chip) {
      chip.addEventListener("click", function () {
        input.value = chip.getAttribute("data-suggest");
        input.focus();
      });
    });
  }

  /* ---- Answer character counter ---- */
  function initCharCount() {
    var form = document.querySelector("[data-answer-form]");
    if (!form) return;
    var field = form.querySelector("#answer");
    var counter = form.querySelector("[data-char-count]");
    if (!field || !counter) return;
    function update() {
      counter.textContent = field.value.length + " / " + field.maxLength;
    }
    field.addEventListener("input", update);
    update();
  }

  /* ---- Submit loading state (progressive enhancement only) ---- */
  function initSubmitGuard() {
    var forms = document.querySelectorAll("[data-submit-guard]");
    Array.prototype.forEach.call(forms, function (form) {
      var button = form.querySelector(
        'button[type="submit"][data-loading-label]'
      );
      if (!button) return;
      form.addEventListener("submit", function () {
        if (form.dataset.submitting === "1") return;
        form.dataset.submitting = "1";
        var label = button.querySelector(".sp-btn-label");
        button.classList.add("is-loading");
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        button.setAttribute("aria-disabled", "true");
        if (label) {
          label.textContent = button.getAttribute("data-loading-label") || label.textContent;
        }
      });
    });
  }

  /* ---- Results page animations ---- */
  function runCounter(el, target) {
    if (el.dataset.animated) return;
    el.dataset.animated = "1";
    var duration = 800;
    var start = null;
    function step(timestamp) {
      if (!start) start = timestamp;
      var progress = Math.min((timestamp - start) / duration, 1);
      el.textContent = String(Math.round(easeOutCubic(progress) * target));
      if (progress < 1) {
        window.requestAnimationFrame(step);
      }
    }
    window.requestAnimationFrame(step);
  }

  function runRing(ring, target) {
    var duration = 900;
    var start = null;
    function step(timestamp) {
      if (!start) start = timestamp;
      var progress = Math.min((timestamp - start) / duration, 1);
      ring.style.setProperty("--r", (easeOutExpo(progress) * target) + "%");
      if (progress < 1) {
        window.requestAnimationFrame(step);
      }
    }
    window.requestAnimationFrame(step);
  }

  function setFinalStates(ring, counters, dims) {
    if (ring) {
      var ringTarget = parseFloat(ring.getAttribute("data-score")) || 0;
      ring.style.setProperty("--r", ringTarget + "%");
    }
    Array.prototype.forEach.call(dims, function (li) {
      var fill = li.querySelector(".sp-dim-fill");
      var target = parseInt(li.getAttribute("data-bar"), 10) || 0;
      if (fill) fill.style.width = target + "%";
    });
  }

  function playAnimations(ring, counters, dims) {
    if (ring) {
      var ringTarget = parseFloat(ring.getAttribute("data-score")) || 0;
      ring.style.setProperty("--r", "0%");
      window.setTimeout(function () { runRing(ring, ringTarget); }, 120);
    }
    Array.prototype.forEach.call(counters, function (el) {
      runCounter(el, parseFloat(el.getAttribute("data-count")) || 0);
    });
    Array.prototype.forEach.call(dims, function (li) {
      var fill = li.querySelector(".sp-dim-fill");
      if (!fill) return;
      var target = parseInt(li.getAttribute("data-bar"), 10) || 0;
      fill.classList.add("no-anim");
      fill.style.width = "0%";
      void fill.offsetWidth;
      fill.classList.remove("no-anim");
      fill.style.width = target + "%";
    });
  }

  function initResult() {
    var ring = document.querySelector(".sp-ring");
    var counters = Array.prototype.slice.call(
      document.querySelectorAll(".sp-result [data-count]")
    );
    var dims = Array.prototype.slice.call(
      document.querySelectorAll(".sp-dim")
    );
    if (!ring && !counters.length && !dims.length) return;

    if (prefersReduced || !("IntersectionObserver" in window)) {
      setFinalStates(ring, counters, dims);
      return;
    }

    var done = false;
    function once(animate) {
      if (done) return;
      done = true;
      if (animate) playAnimations(ring, counters, dims);
    }

    var anchor = ring || dims[0] || counters[0];
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          observer.disconnect();
          once(true);
        }
      });
    }, { threshold: 0.2 });
    if (anchor) observer.observe(anchor);

    window.setTimeout(function () { once(true); }, 1400);
  }

  /* ---- Company context selector hint (Phase 10 / Stage 1) ---- */
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

  /* ---- Boot ---- */
  function boot() {
    initReveal();
    initSuggestions();
    initCharCount();
    initSubmitGuard();
    initResult();
    initCompanyContext();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();