/* InterviewIQ — History search & filter controls (vanilla JS, CSP-safe).
 * Small progressive enhancement for the /history filter form:
 *  - Selects and date inputs auto-submit the form on change (filtering is
 *    server-side, so there are NO requests per keystroke — only explicit
 *    user actions); the search box keeps submitting on Enter or the
 *    "Apply" button exactly like a normal GET form.
 * Without JS the form falls back to plain GET submission, so nothing breaks.
 * No data or business logic lives here.
 */
(function () {
  "use strict";

  function boot() {
    var form = document.querySelector(".iq-history-controls");
    if (!form) return;
    if (!form.submit) return;

    var autos = form.querySelectorAll("[data-autosubmit]");
    for (var i = 0; i < autos.length; i++) {
      autos[i].addEventListener("change", function () {
        form.submit();
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();