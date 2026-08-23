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

    button.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      button.setAttribute("aria-label", "Switch to " + (next === "dark" ? "dark" : "light") + " theme");
      try {
        localStorage.setItem("interviewiq-theme", next);
      } catch (e) {
        /* ignore storage errors */
      }
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

  initSidebar();
  initThemeToggle();
  initAlerts();
})();
