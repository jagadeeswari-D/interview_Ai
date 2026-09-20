/* InterviewIQ — Settings page interactions (vanilla JS).
 * - section tab switching (animated panel reveal)
 * - theme radios apply the existing localStorage theme system immediately
 * - save buttons show "Saving…" then a small inline "Saved ✓" toast
 * - no browser alert(), no fake delete actions
 */
(function () {
  "use strict";

  function qs(selector, root) {
    return (root || document).querySelector(selector);
  }

  function qsa(selector, root) {
    return Array.prototype.slice.call(
      (root || document).querySelectorAll(selector)
    );
  }

  /* ---- Section tabs ---- */
  function initTabs() {
    var tabs = qsa(".iq-settings-tab");
    var panels = qsa(".iq-settings-panel");
    if (!tabs.length || !panels.length) return;

    var allPanelNames = panels.map(function (p) {
      return p.getAttribute("data-panel");
    });

    function show(section) {
      var found = allPanelNames.indexOf(section) >= 0 ? section : "profile";
      tabs.forEach(function (tab) {
        tab.classList.toggle("is-active", tab.getAttribute("data-section") === found);
      });
      panels.forEach(function (panel) {
        var active = panel.getAttribute("data-panel") === found;
        panel.classList.toggle("is-active", active);
      });
      // Mirror into the URL fragment so refresh keeps the section.
      try {
        history.replaceState(null, "", "#" + found);
      } catch (e) { /* ignore */ }
    }

    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        show(tab.getAttribute("data-section"));
      });
    });

    // Deep link: /settings#account
    var initial = (window.location.hash || "").replace("#", "") || "profile";
    show(initial);
  }

  /* ---- Theme radios -> existing theme system ---- */
  function initThemeRadios() {
    var radios = qsa("[data-theme-radio]");
    if (!radios.length) return;
    var root = document.documentElement;

    radios.forEach(function (radio) {
      radio.addEventListener("change", function () {
        if (!radio.checked) return;
        var value = radio.value;
        var applied = value === "system"
          ? (window.matchMedia &&
             window.matchMedia("(prefers-color-scheme: light)").matches
               ? "light"
               : "dark")
          : value;
        root.setAttribute("data-theme", applied);
        try {
          localStorage.setItem("interviewiq-theme", applied);
        } catch (e) { /* ignore storage errors */ }
      });
    });
  }

  /* ---- Save forms: in-page submit + "Saving…" then toast "Saved ✓" ----
   * Preference/notification/appearance forms are submitted with fetch so the
   * page never reloads and we can show the inline saving state. The password
   * form (data-no-ajax) navigates normally because validation errors arrive
   * as server-side flash messages.
   */
  var toastTimer = null;

  function showToast(message, isError) {
    var toast = qs("[data-toast]");
    if (!toast) return;
    var text = qs(".iq-toast-text", toast);
    if (text) text.textContent = message || "Saved";
    toast.classList.toggle("is-error", !!isError);
    toast.classList.add("is-visible");
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      toast.classList.remove("is-visible");
    }, 2200);
  }

  function initSaveForms() {
    var forms = qsa("[data-save-form]");
    forms.forEach(function (form) {
      if (form.hasAttribute("data-no-ajax")) return; // normal submit
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var btn = qs(".iq-save-btn", form);
        var label = btn ? btn.textContent : "";
        if (btn) {
          btn.textContent = "Saving…";
          btn.classList.add("is-saving");
        }

        var body = new FormData(form);
        fetch(form.action, {
          method: "POST",
          body: body,
          credentials: "same-origin",
        })
          .then(function (response) {
            if (btn) {
              setTimeout(function () {
                btn.textContent = label;
                btn.classList.remove("is-saving");
              }, 400);
            }
            if (response.ok) {
              showToast("Saved \u2713");
            } else {
              showToast("Couldn\u2019t save \u2014 try again.");
            }
          })
          .catch(function () {
            if (btn) {
              btn.textContent = label;
              btn.classList.remove("is-saving");
            }
            showToast("Couldn\u2019t save \u2014 try again.");
          });
      });
    });
  }

  function boot() {
    initTabs();
    initThemeRadios();
    initSaveForms();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();