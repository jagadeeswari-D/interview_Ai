/* InterviewIQ — Performance Analytics charts (Stage 6, Item 11).
 * Data comes from GET /analytics/data (aggregate values only); rendering is
 * client-side via the locally vendored Chart.js build (CSP: script-src
 * 'self'). Charts re-render on light/dark theme changes, respect
 * prefers-reduced-motion, and fall back to the server-rendered numbers when
 * JavaScript is unavailable.
 *
 * Presentation-only: everything below is derived from the exact aggregate
 * payload (never new or fake values). The design mirrors the Dashboard chart
 * language — accent line + gradient fill, hover emphasis, theme-aware
 * tooltips, and rounded, tier-coloured bars (high/mid/low = success/warning/
 * danger, matching the server's heat_tier thresholds and the heatmap grid).
 */
(function () {
  "use strict";

  var prefersReduced = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* Theme tokens are defined on the scoped body.page-* selectors, so read the
   * palette from the BODY's computed styles (inherits :root tokens too). */
  function cssVar(name, fallback) {
    var value = getComputedStyle(document.body)
      .getPropertyValue(name)
      .trim();
    return value || fallback;
  }

  function palette() {
    return {
      text: cssVar("--text", "#f2f3f5"),
      muted: cssVar("--chart-axis-text", cssVar("--text-muted", "#9aa0aa")),
      grid: cssVar("--chart-grid", "rgba(255,255,255,0.08)"),
      accent: cssVar("--primary-text", "#7c8cff"),
      surface: cssVar("--bg-elevated", "#14181f"),
      tooltipBg: cssVar("--chart-tooltip-bg", "#14181f"),
      tooltipText: cssVar("--chart-tooltip-text", "#f2f3f5"),
      tooltipBorder: cssVar("--chart-tooltip-border", "rgba(255,255,255,0.14)"),
      success: cssVar("--success", "#42d9a0"),
      warning: cssVar("--warning", "#e7a84b"),
      danger: cssVar("--danger", "#f0786f"),
    };
  }

  /* ---- Small colour utilities (presentation only) ---- */
  function rgba(hex, alpha) {
    var m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex).trim());
    if (!m) return hex;
    var h = m[1];
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    var r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  function lighten(hex, amount) {
    var m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex).trim());
    if (!m) return hex;
    var h = m[1];
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    var r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    r = Math.round(r + (255 - r) * amount);
    g = Math.round(g + (255 - g) * amount);
    b = Math.round(b + (255 - b) * amount);
    return "rgb(" + r + "," + g + "," + b + ")";
  }

  /* Tier thresholds mirror app/analytics.py heat_tier and the app-wide
   * badge thresholds — display only, values are untouched. */
  function tierFor(avg) {
    if (avg == null) return "low";
    if (avg >= 75) return "high";
    if (avg >= 50) return "mid";
    return "low";
  }

  function tierLabel(tier) {
    if (tier === "high") return "Strong";
    if (tier === "mid") return "Developing";
    return "Needs work";
  }

  function tierBand(tier) {
    if (tier === "high") return "75-100";
    if (tier === "mid") return "50-74";
    return "0-49";
  }

  function tierColor(tier, colors) {
    if (tier === "high") return colors.success;
    if (tier === "mid") return colors.warning;
    return colors.danger;
  }

  /* ---- Readable labels from the stored YYYY-MM-DD labels (formatting only) */
  function parseDay(value) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ""));
    if (!m) return null;
    var d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    return isNaN(d.getTime()) ? null : d;
  }

  function shortDayLabel(value) {
    var d = parseDay(value);
    if (!d) return String(value);
    return d.toLocaleDateString("en-US", {
      month: "short", day: "numeric", timeZone: "UTC",
    });
  }

  function fullDateLabel(value) {
    var d = parseDay(value);
    if (!d) return String(value);
    return d.toLocaleDateString("en-US", {
      month: "short", day: "numeric", year: "numeric", timeZone: "UTC",
    });
  }

  /* Subtle vertical gradient from the theme accent colour fading upward.
   * Mirror of the Dashboard fill. NOTE: Chart.js passes a *scriptable
   * context* — the real canvas 2D context lives on ctx.chart.ctx. */
  function accentFill(ctx, accent) {
    var chart = ctx && ctx.chart;
    var area = chart && chart.chartArea;
    var render = chart && chart.ctx;
    if (!area || !render || typeof render.createLinearGradient !== "function") {
      return rgba(accent, 0.12);
    }
    var grad = render.createLinearGradient(0, area.bottom, 0, area.top);
    grad.addColorStop(0, rgba(accent, 0.28));
    grad.addColorStop(0.55, rgba(accent, 0.08));
    grad.addColorStop(1, rgba(accent, 0));
    return grad;
  }

  function deviceRatio() {
    return (window.devicePixelRatio || 1) > 1.5
      ? window.devicePixelRatio
      : 2;
  }

  function debounce(fn, wait) {
    var timer;
    return function () {
      var ctx = this;
      var args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function () {
        fn.apply(ctx, args);
      }, wait);
    };
  }

  var trendChart = null;
  var skillsChart = null;
  var trendCanvas = null;
  var skillsCanvas = null;
  var cachedData = null;

  function destroy(chart) {
    if (chart) {
      chart.destroy();
    }
    return null;
  }

  /* ---- Trend line (single dataset = "Overall score") ---- */
  function renderTrendChart(trend, colors) {
    var labels = trend.labels;
    var scores = trend.scores;
    var count = scores.length;
    var single = count === 1;

    return new window.Chart(trendCanvas, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Overall score",
            data: scores,
            borderColor: colors.accent,
            backgroundColor: function (ctx) {
              return accentFill(ctx, colors.accent);
            },
            borderWidth: single ? 2 : 2.5,
            borderCapStyle: "round",
            borderJoinStyle: "round",
            tension: 0.4,
            pointRadius: single ? 7 : (count <= 4 ? 4.5 : 3.5),
            pointHitRadius: 16,
            pointHoverRadius: single ? 12 : 8,
            pointHoverBackgroundColor: colors.accent,
            pointBackgroundColor: colors.accent,
            pointBorderColor: single
              ? rgba(colors.accent, 0.32)
              : colors.surface,
            pointBorderWidth: single ? 7 : 2,
            pointHoverBorderColor: colors.surface,
            pointHoverBorderWidth: 2.5,
            fill: true,
            animation: prefersReduced ? false : {
              y: {
                duration: 900,
                easing: "easeOutQuart",
                from: 100,
              },
            },
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        devicePixelRatio: deviceRatio(),
        interaction: {
          mode: single ? "nearest" : "index",
          intersect: false,
          axis: "x",
        },
        animation: prefersReduced ? false : {
          duration: 900,
          easing: "easeOutQuart",
        },
        layout: { padding: { top: 6 } },
        scales: {
          y: {
            min: 0,
            max: 100,
            beginAtZero: true,
            grid: {
              color: colors.grid,
              drawTicks: false,
              drawBorder: false,
            },
            border: { display: false },
            ticks: {
              color: colors.muted,
              stepSize: 20,
              padding: 10,
              font: { size: 11 },
            },
            title: {
              display: true,
              text: "Score (0-100)",
              color: colors.muted,
              font: { size: 10, weight: "600" },
            },
          },
          x: {
            grid: { display: false },
            border: { display: false },
            ticks: {
              color: colors.muted,
              maxRotation: 0,
              autoSkip: true,
              maxTicksLimit: count >= 10 ? 6 : (count >= 5 ? 7 : count),
              padding: 10,
              font: { size: 11 },
              callback: function (value, index) {
                if (index < 0 || index >= labels.length) return "";
                return shortDayLabel(labels[index]);
              },
            },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: analyticsTooltip(colors, {
            title: function (items) {
              var idx = items && items[0] && items[0].dataIndex;
              return (idx >= 0 && idx < labels.length)
                ? fullDateLabel(labels[idx])
                : "Interview session";
            },
            label: function (c) {
              return "Score: " + c.parsed.y + "/100";
            },
            afterLabel: function (c) {
              if (c.dataIndex <= 0 || count < 2) return "";
              var delta = Math.round(scores[c.dataIndex]) -
                          Math.round(scores[c.dataIndex - 1]);
              if (delta > 0) return "+" + delta + " vs last session";
              if (delta < 0) return "-" + (-delta) + " vs last session";
              return "same as last session";
            },
            footer: function (items) {
              var idx = items && items[0] && items[0].dataIndex;
              return "Session " + (idx + 1) + " of " + count;
            },
          }),
        },
      },
    });
  }

  /* ---- Skill heatmap (horizontal bars, tier-coloured) ---- */
  function renderSkillsChart(skills, colors) {
    var names = skills.map(function (row) { return row.skill; });
    var avgs = skills.map(function (row) { return row.average; });
    var paletteByTier = skills.map(function (row) {
      var t = tierFor(row.average);
      return {
        fill: tierColor(t, colors),
        hover: lighten(tierColor(t, colors), 0.3),
      };
    });

    return new window.Chart(skillsCanvas, {
      type: "bar",
      data: {
        labels: names,
        datasets: [
          {
            label: "Average score",
            data: avgs,
            backgroundColor: paletteByTier.map(function (p) { return p.fill; }),
            hoverBackgroundColor: paletteByTier.map(function (p) {
              return p.hover;
            }),
            borderColor: colors.surface,
            borderWidth: 1,
            hoverBorderColor: paletteByTier.map(function (p) { return p.fill; }),
            hoverBorderWidth: 2,
            borderRadius: 6,
            borderSkipped: false,
            maxBarThickness: 26,
            animation: prefersReduced ? false : {
              x: {
                duration: 900,
                easing: "easeOutQuart",
                from: 0,
              },
            },
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        devicePixelRatio: deviceRatio(),
        indexAxis: "y",
        interaction: {
          mode: "nearest",
          intersect: true,
        },
        animation: prefersReduced ? false : {
          duration: 900,
          easing: "easeOutQuart",
        },
        scales: {
          x: {
            min: 0,
            max: 100,
            beginAtZero: true,
            grid: {
              color: colors.grid,
              drawTicks: false,
              drawBorder: false,
            },
            border: { display: false },
            ticks: {
              color: colors.muted,
              stepSize: 25,
              padding: 8,
              font: { size: 11 },
            },
            title: {
              display: true,
              text: "Average score (0-100)",
              color: colors.muted,
              font: { size: 10, weight: "600" },
            },
          },
          y: {
            grid: { display: false },
            border: { display: false },
            ticks: {
              color: colors.muted,
              autoSkip: false,
              padding: 10,
              font: { size: 12, weight: "600" },
            },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: analyticsTooltip(colors, {
            title: function (items) {
              var idx = items && items[0] && items[0].dataIndex;
              return (idx >= 0 && idx < names.length) ? names[idx] : "Skill";
            },
            label: function (c) {
              return "Average: " + c.parsed.x + "/100";
            },
            afterLabel: function (c) {
              var row = c.dataIndex >= 0 ? skills[c.dataIndex] : null;
              if (!row) return "";
              return tierLabel(tierFor(row.average)) +
                     " band (" + tierBand(tierFor(row.average)) + ")";
            },
            footer: function (items) {
              var idx = items && items[0] && items[0].dataIndex;
              var row = idx >= 0 ? skills[idx] : null;
              if (!row) return "";
              return "Based on " + row.samples +
                     (row.samples === 1 ? " graded answer" : " graded answers");
            },
          }),
        },
      },
    });
  }

  /* Shared, theme-aware tooltip shell. */
  function analyticsTooltip(colors, callbacks) {
    return {
      enabled: true,
      displayColors: false,
      backgroundColor: colors.tooltipBg,
      titleColor: colors.tooltipText,
      bodyColor: colors.tooltipText,
      footerColor: colors.muted,
      borderColor: rgba(colors.accent, 0.55),
      borderWidth: 1,
      cornerRadius: 12,
      padding: 12,
      boxPadding: 6,
      caretSize: 6,
      titleFont: { weight: "700", size: 12 },
      bodyFont: { size: 13, weight: "600" },
      footerFont: { size: 11 },
      footerMarginTop: 8,
      animation: prefersReduced ? false : {
        duration: 400,
        easing: "easeOutQuart",
      },
      callbacks: callbacks,
    };
  }

  /* Adaptive heatmap height: keep bar rows readable without cramming many
   * skills into a fixed 280px box. Values applied via the CSSOM (CSP-safe,
   * same pattern as dashboard.js / pages.js). */
  function applyHeatHeight() {
    if (!skillsCanvas || !cachedData || !cachedData.skills ||
        !cachedData.skills.length) {
      return;
    }
    var wrap = skillsCanvas.parentElement;
    if (!wrap) return;
    var n = cachedData.skills.length;
    var narrow = window.matchMedia &&
      window.matchMedia("(max-width: 560px)").matches;
    var floor = narrow ? 180 : 200;
    var ceil = narrow ? 380 : 520;
    var target = Math.max(floor, Math.min(ceil, n * 42 + 84));
    wrap.style.height = target + "px";
    if (skillsChart) {
      skillsChart.resize();
    }
  }

  function renderCharts(data) {
    trendChart = destroy(trendChart);
    skillsChart = destroy(skillsChart);

    var colors = palette();
    window.Chart.defaults.color = colors.text;
    window.Chart.defaults.borderColor = colors.grid;
    window.Chart.defaults.font.family =
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";

    trendCanvas = document.getElementById("trend-chart");
    if (trendCanvas && data.trend && data.trend.labels.length) {
      trendChart = renderTrendChart(data.trend, colors);
    }

    skillsCanvas = document.getElementById("skills-chart");
    if (skillsCanvas && data.skills && data.skills.length) {
      applyHeatHeight();
      skillsChart = renderSkillsChart(data.skills, colors);
    }
  }

  function render(data) {
    if (!window.Chart) {
      return; // vendored library failed to load — tables remain usable
    }
    try {
      renderCharts(data);
    } catch (e) {
      /* A malformed data payload must never break the page. */
    }
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
          cachedData = data;
          render(data);
        }
      })
      .catch(function () {
        /* Charts stay empty; server-rendered lists remain the fallback. */
      });
  }

  // Keep heat rows readable when the viewport crosses breakpoints.
  window.addEventListener("resize", debounce(applyHeatHeight, 120));

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