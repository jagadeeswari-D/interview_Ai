/* InterviewIQ — Performance Analytics charts (Stage 6, Item 11).
 * Data comes from GET /analytics/data (aggregate values only); rendering is
 * client-side via the locally vendored Chart.js build (CSP: script-src
 * 'self'). Charts re-render on light/dark theme changes and fall back to the
 * server-rendered numbers when JavaScript is unavailable.
 */
(function () {
  "use strict";

  function cssVar(name, fallback) {
    var value = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    return value || fallback;
  }

  function palette() {
    return {
      text: cssVar("--text", "#1b2430"),
      muted: cssVar("--text-muted", "#5b6b7c"),
      grid: cssVar("--border", "#dfe4ea"),
      accent: cssVar("--primary", "#4f46e5"),
    };
  }

  var trendChart = null;
  var skillsChart = null;

  function destroy(chart) {
    if (chart) {
      chart.destroy();
    }
    return null;
  }

  function render(data) {
    if (!window.Chart) {
      return; // vendored library failed to load — tables remain usable
    }
    trendChart = destroy(trendChart);
    skillsChart = destroy(skillsChart);

    var colors = palette();
    window.Chart.defaults.color = colors.text;
    window.Chart.defaults.borderColor = colors.grid;
    window.Chart.defaults.font.family =
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";

    var trendCanvas = document.getElementById("trend-chart");
    if (trendCanvas && data.trend && data.trend.labels.length) {
      trendChart = new window.Chart(trendCanvas, {
        type: "line",
        data: {
          labels: data.trend.labels,
          datasets: [
            {
              label: "Overall score",
              data: data.trend.scores,
              borderColor: colors.accent,
              backgroundColor: colors.accent,
              tension: 0.25,
              fill: false,
              pointRadius: 3,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: { duration: prefersReducedMotion() ? 0 : 300 },
          scales: {
            y: { min: 0, max: 100, ticks: { color: colors.muted } },
            x: { ticks: { color: colors.muted } },
          },
          plugins: { legend: { display: false } },
        },
      });
    }

    var skillsCanvas = document.getElementById("skills-chart");
    if (skillsCanvas && data.skills && data.skills.length) {
      skillsChart = new window.Chart(skillsCanvas, {
        type: "bar",
        data: {
          labels: data.skills.map(function (row) { return row.skill; }),
          datasets: [
            {
              label: "Average score",
              data: data.skills.map(function (row) { return row.average; }),
              backgroundColor: colors.accent,
              borderRadius: 4,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          indexAxis: "y",
          animation: { duration: prefersReducedMotion() ? 0 : 300 },
          scales: {
            x: { min: 0, max: 100, ticks: { color: colors.muted } },
            y: { ticks: { color: colors.muted } },
          },
          plugins: { legend: { display: false } },
        },
      });
    }
  }

  function prefersReducedMotion() {
    return window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function load() {
    fetch("/analytics/data", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (data) {
        if (data) {
          render(data);
        }
      })
      .catch(function () {
        /* Charts stay empty; server-rendered lists remain the fallback. */
      });
  }

  // Follow light/dark toggles without a page reload.
  if (window.MutationObserver) {
    new MutationObserver(load).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();
