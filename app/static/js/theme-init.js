/* Apply saved theme before first paint to avoid a flash of the wrong theme.
   Loaded synchronously in <head>. Respects the user's stored choice and
   falls back to the OS preference. */
(function () {
  "use strict";
  var root = document.documentElement;
  var theme = "light";
  try {
    var stored = localStorage.getItem("interviewiq-theme");
    if (stored === "dark" || stored === "light") {
      theme = stored;
    } else if (window.matchMedia("(prefers-color-scheme: dark)").matches) {
      theme = "dark";
    }
  } catch (e) {
    /* localStorage unavailable — keep default light theme. */
  }
  root.setAttribute("data-theme", theme);
})();
