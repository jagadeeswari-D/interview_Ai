/* InterviewIQ — Today's Challenge interactions (vanilla JS).
 *  - scroll-reveal (matches Dashboard/Practice behavior)
 *  - answer character counter
 *  - loading state on the submit button (progressive enhancement only)
 * All dynamic styles go through the CSSOM (CSP-safe); no inline style="".
 */
(function () {
  "use strict";

  var prefersReduced = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- Scroll reveal (one-shot, staggered) ---- */
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

  /* ---- Submit loading state (progressively enhanced only) ---- */
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
        var label = button.querySelector(".ch-btn-label");
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

  /* ---- Boot ---- */
  function boot() {
    initReveal();
    initCharCount();
    initSubmitGuard();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();