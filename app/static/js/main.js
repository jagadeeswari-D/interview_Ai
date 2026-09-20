/* InterviewIQ — shared UI behavior (Stage 1):
   - mobile sidebar open/close
   - light/dark theme toggle (persisted to localStorage)
   - dismissible flash alerts
   Loaded at end of <body>; all features degrade gracefully if absent. */
(function () {
  "use strict";

  function initSidebar() {
    var toggle = document.getElementById("sidebar-toggle");
    var overlay = document.getElementById("sidebar-overlay");
    if (!toggle || !overlay) return;

    function setOpen(open) {
      document.body.classList.toggle("sidebar-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
    }

    toggle.addEventListener("click", function () {
      setOpen(!document.body.classList.contains("sidebar-open"));
    });
    overlay.addEventListener("click", function () {
      setOpen(false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") setOpen(false);
    });
  }

  function initThemeToggle() {
    var button = document.getElementById("theme-toggle");
    if (!button) return;
    var root = document.documentElement;

    function target() {
      return root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    }

    function syncLabel() {
      button.setAttribute("aria-label", "Switch to " + target() + " theme");
    }

    syncLabel();
    button.addEventListener("click", function () {
      var next = target();
      root.setAttribute("data-theme", next);
      try {
        localStorage.setItem("interviewiq-theme", next);
      } catch (e) {
        /* ignore storage errors */
      }
      syncLabel();
    });
  }

  function initAlerts() {
    var dismissers = document.querySelectorAll("[data-dismiss-alert]");
    Array.prototype.forEach.call(dismissers, function (button) {
      button.addEventListener("click", function () {
        var alert = button.closest(".alert");
        if (alert) alert.remove();
      });
    });
  }

  /* Meter fills (e.g. score bars) are rendered with a data-fill percentage
     and applied here via the CSSOM, which is allowed by the strict CSP
     (style-src 'self' blocks inline style="" attributes). */
  function initMeterFills() {
    var fills = document.querySelectorAll("[data-fill]");
    Array.prototype.forEach.call(fills, function (el) {
      var value = parseInt(el.getAttribute("data-fill"), 10);
      if (!isNaN(value)) {
        el.style.width = Math.max(0, Math.min(100, value)) + "%";
      }
    });
  }

  initSidebar();
  initThemeToggle();
  initAlerts();
  initMeterFills();
})();
