/* Apply saved theme before first paint to avoid a flash of the wrong theme.
   Loaded synchronously in <head>. Respects the user's stored choice and
   falls back to the OS preference. */
(function () {
  "use strict";
  var root = document.documentElement;
  var theme = "dark";
  try {
    var stored = localStorage.getItem("interviewiq-theme");
    if (stored === "dark" || stored === "light") {
      theme = stored;
    } else if (window.matchMedia("(prefers-color-scheme: light)").matches) {
      theme = "light";
    }
  } catch (e) {
    /* localStorage unavailable — keep default dark theme. */
  }
  root.setAttribute("data-theme", theme);
})();
