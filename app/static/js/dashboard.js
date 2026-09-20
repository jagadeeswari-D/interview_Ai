/* InterviewIQ — Product dashboard interactions (vanilla JS).
 * - reveals sections on scroll (once)
 * - animates KPI counters into view
 * - animates skill bars when revealed
 * - renders the performance Chart.js line chart from GET /dashboard/data
 * - re-renders the chart on light/dark theme changes without reloading
 * Chart.js is the existing vendored build (CSP: script-src 'self').
 */
(function () {
  "use strict";

  var prefersReduced = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* Theme tokens are defined on the scoped body.page-* selectors (see
   * theme.css), so read the palette from the BODY's computed styles first,
   * then fall back to the documentElement defaults. */
  function cssVar(name, fallback) {
    var value = getComputedStyle(document.body)
      .getPropertyValue(name)
      .trim();
    if (!value) {
      value = getComputedStyle(document.documentElement)
        .getPropertyValue(name)
        .trim();
    }
    return value || fallback;
  }

  function palette() {
    var accent = cssVar("--primary-text", "#cf9d7b");
    var text = cssVar("--text", "#f3ece4");
    return {
      text: text,
      muted: cssVar("--chart-axis-text", cssVar("--text-muted", "#a89b8e")),
      grid: cssVar("--chart-grid", cssVar("--border", "#2b3438")),
      accent: accent,
      surface: cssVar("--bg-elevated", "#1c262c"),
      tooltipBg: cssVar("--chart-tooltip-bg", "#14181f"),
      tooltipText: cssVar("--chart-tooltip-text", text),
      tooltipBorder: cssVar("--chart-tooltip-border", rgba(accent, 0.5)),
    };
  }

  /* ---- Scroll reveal (one-shot) ---- */
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

  /* ---- Animated counters ---- */
  function animateCounters() {
    var counters = document.querySelectorAll("[data-count]");
    if (!counters.length) return;
    if (prefersReduced) return; // values are already rendered in the markup

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        observer.unobserve(el);
        var target = parseInt(el.getAttribute("data-count"), 10) || 0;
        var duration = 500;
        var start = null;
        function step(timestamp) {
          if (!start) start = timestamp;
          var progress = Math.min((timestamp - start) / duration, 1);
          var eased = 1 - Math.pow(1 - progress, 3);
          el.textContent = String(Math.round(eased * target));
          if (progress < 1) {
            window.requestAnimationFrame(step);
          }
        }
        window.requestAnimationFrame(step);
      });
    }, { threshold: 0.5 });
    counters.forEach(function (el) { observer.observe(el); });
  }

  /* ---- Skill + roadmap progress bars + readiness ring ----
   * Values are read from data-* attributes (not inline styles, which the
   * strict CSP style-src 'self' blocks) and applied via the CSSOM, which is
   * CSP-safe. */
  function initProgressBars() {
    var fills = document.querySelectorAll(
      ".iq-skill-fill, .iq-roadmap-fill, .iq-achievement-fill"
    );
    if (!fills.length) return;
    function reveal(fill) {
      var skill = fill.closest(".iq-skill");
      var value = (skill && skill.getAttribute("data-bar")) ||
                  fill.getAttribute("data-w");
      var pct = parseInt(value, 10);
      if (!isNaN(pct)) {
        fill.style.width = Math.max(0, Math.min(100, pct)) + "%";
      }
    }
    if (!("IntersectionObserver" in window) || prefersReduced) {
      fills.forEach(reveal);
      return;
    }
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          reveal(entry.target);
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.4 });
    fills.forEach(function (fill) { observer.observe(fill); });
  }

  function initReadinessRing() {
    var ring = document.querySelector(".iq-ring[data-score]");
    if (!ring) return;
    var value = parseFloat(ring.getAttribute("data-score"));
    if (!isNaN(value)) {
      ring.style.setProperty(
        "--r",
        Math.max(0, Math.min(100, value)) + "%"
      );
    }
  }

  /* ---- Chart ----
   * Lifecycle rules (mirror analytics.js so re-renders never break):
   *  - A single Chart instance per canvas is tracked; destroy before recreating.
   *  - Creation/rendering is wrapped so a failure never leaves a blank canvas
   *    with a stale reference (which would make the chart irrecoverable).
   *  - Initialization is idempotent: repeated calls never stack renders.
   *  - Theme toggles re-fetch + re-render (self-healing), like analytics.js.
   */
  var trendChart = null;
  var cachedData = null;
  var chartInitiated = false;   // idempotency guard
  var chartFetching = false;    // in-flight guard (no stacked fetches)
  var layoutRetries = 0;
  var MAX_LAYOUT_RETRIES = 6;

  function destroyChart() {
    if (trendChart) {
      trendChart.destroy();
      trendChart = null;
    }
  }

  function wrapHasSize(canvas) {
    var wrap = canvas && canvas.parentElement;
    return !!(wrap && wrap.offsetWidth > 0 && wrap.offsetHeight > 0);
  }

  function chartReadyCanvas() {
    var canvas = document.getElementById("iq-performance-chart");
    return wrapHasSize(canvas) ? canvas : null;
  }

  function scheduleLayoutRetry(data) {
    if (layoutRetries >= MAX_LAYOUT_RETRIES) {
      console.warn(
        "[InterviewIQ] Dashboard chart container never got a measurable layout; keeping the empty state."
      );
      destroyChart();
      cachedData = null;
      return;
    }
    layoutRetries += 1;
    window.requestAnimationFrame(function () {
      renderChart(data);
    });
  }

  function flushChartSize() {
    window.requestAnimationFrame(function () {
      if (trendChart) trendChart.resize();
    });
  }

  function recoverChartIfNeeded() {
    var canvas = document.getElementById("iq-performance-chart");
    if (!canvas || !window.Chart || !cachedData || !wrapHasSize(canvas)) return;
    var needsRedraw = !trendChart ||
      (trendChart.canvas && trendChart.canvas.offsetWidth === 0);
    if (needsRedraw) renderChart(cachedData);
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

  /* Build a subtle vertical gradient from the theme accent colour, fading to
   * transparent towards the top. Keeps the palette untouched.
   * NOTE: Chart.js passes a *scriptable context* here — the real canvas 2D
   * context lives on ctx.chart.ctx (calling createLinearGradient on the script
   * context itself throws, which used to blank the whole chart). */
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

  /* ---- Readable labels built from the real stored session datetimes ----
   * Formatting only — nothing here invents or alters the underlying data. */
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

  function timeLabel(value) {
    var m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(String(value || ""));
    return m ? m[4] + ":" + m[5] : "";
  }

  /* X-axis tick for one session: short day, and a time suffix when two real
   * sessions share the same day so the labels stay distinct. */
  function xTickLabel(index, data) {
    var labels = data.trend.labels;
    if (!labels || index < 0 || index >= labels.length) return "";
    var label = labels[index];
    var pts = data.trend.points;
    if (pts && pts[index] && index > 0 && labels[index - 1] === label) {
      return shortDayLabel(label) + " · " + timeLabel(pts[index].date);
    }
    return shortDayLabel(label);
  }

  function pointMeta(data, index) {
    var pts = data.trend.points;
    return (pts && pts[index]) ? pts[index] : null;
  }

  function rgba(hex, alpha) {
    var m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex).trim());
    if (!m) return hex;
    var h = m[1];
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    var r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  function renderChart(data) {
    destroyChart();
    if (!data || !data.trend || !data.trend.labels.length) {
      cachedData = null;
      return;
    }
    var canvas = chartReadyCanvas();
    if (!canvas || !window.Chart) {
      if (document.getElementById("iq-performance-chart")) {
        scheduleLayoutRetry(data);
      }
      return;
    }
    layoutRetries = 0;
    var colors = palette();
    window.Chart.defaults.color = colors.text;
    window.Chart.defaults.borderColor = colors.grid;
    window.Chart.defaults.font.family =
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";

    var slow = prefersReduced;
    var scores = data.trend.scores;
    var count = scores.length;
    var single = count === 1;

    try {
      trendChart = new window.Chart(canvas, {
      type: "line",
      data: {
        labels: data.trend.labels,
        datasets: [{
          label: "Overall score",
          data: scores,
          borderColor: colors.accent,
          backgroundColor: function (ctx) {
            return accentFill(ctx, colors.accent);
          },
          borderWidth: single ? 2 : 2.5,
          borderCapStyle: "round",
          borderJoinStyle: "round",
          tension: 0.42,
          pointRadius: single ? 7 : (count <= 4 ? 4.5 : 3.5),
          pointHitRadius: 16,
          pointHoverRadius: single ? 12 : 8,
          pointHoverBackgroundColor: colors.accent,
          pointBackgroundColor: colors.accent,
          pointBorderColor: single
            ? rgba(colors.accent, 0.34)
            : cssVar("--bg-elevated", "#1c262c"),
          pointBorderWidth: single ? 7 : 2,
          pointHoverBorderColor: cssVar("--bg-elevated", "#1c262c"),
          pointHoverBorderWidth: 2.5,
          fill: true,
          animation: slow ? false : {
            y: {
              duration: 950,
              easing: "easeOutQuart",
              from: 100,
            },
          },
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: single ? "nearest" : "index",
          intersect: false,
          axis: "x",
        },
        devicePixelRatio: (window.devicePixelRatio || 1) > 1.5
          ? window.devicePixelRatio : 2,
        animation: slow ? false : {
          duration: 950,
          easing: "easeOutQuart",
        },
        scales: {
          y: {
            min: 0,
            max: 100,
            beginAtZero: true,
            grid: {
              color: colors.grid,
              drawBorder: false,
              drawTicks: false,
            },
            ticks: {
              color: colors.muted,
              stepSize: 20,
              padding: 10,
              font: { size: 11 },
            },
            border: { display: false },
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
              maxTicksLimit: 8,
              padding: 10,
              font: { size: 11 },
              callback: function (value, index) {
                return xTickLabel(index, data);
              },
            },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            enabled: true,
            displayColors: false,
            backgroundColor: colors.tooltipBg,
            titleColor: colors.tooltipText,
            bodyColor: colors.tooltipText,
            footerColor: colors.tooltipText,
            borderColor: colors.tooltipBorder,
            borderWidth: 1,
            cornerRadius: 12,
            padding: 12,
            boxPadding: 6,
            caretSize: 6,
            usePointStyle: true,
            titleFont: { weight: "700", size: 12 },
            bodyFont: { size: 13, weight: "600" },
            footerFont: { size: 11 },
            footerMarginTop: 6,
            callbacks: {
              title: function (items) {
                var p = pointMeta(data, items && items[0] && items[0].dataIndex);
                if (p) {
                  return (p.mode === "real" ? "Real interview · " : "Practice · ") + p.name;
                }
                return "Interview session";
              },
              label: function (ctx2) {
                return "Score: " + ctx2.parsed.y + "/100";
              },
              footer: function (items) {
                var p = pointMeta(data, items && items[0] && items[0].dataIndex);
                if (!p) return "";
                var day = fullDateLabel(p.date);
                var time = timeLabel(p.date);
                return time ? day + " · " + time : day;
              },
            },
          },
        },
      },
    });
    } catch (err) {
      /* A failed render must never leave a stale instance: clear it so the
       * theme/init observers can safely retry and rebuild the chart. */
      destroyChart();
      cachedData = null;
      console.error(
        "[InterviewIQ] Dashboard chart render failed: " +
        ((err && err.message) ? err.message : String(err))
      );
    }
    if (trendChart) flushChartSize();
  }

  /* Re-render from the cached payload, falling back to a fresh fetch. Used by
   * the theme observer so toggling light/dark never drops the chart. */
  function reloadChart() {
    if (cachedData) {
      renderChart(cachedData);
      return;
    }
    loadChart();
  }

  function loadChart() {
    // Never stack concurrent fetches; a single in-flight request is enough.
    if (chartFetching) return;
    chartFetching = true;
    fetch("/dashboard/data", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (res) {
        if (!res.ok) {
          console.warn(
            "[InterviewIQ] Dashboard chart data request failed with status " + res.status + "."
          );
          return null;
        }
        return res.json();
      })
      .then(function (data) {
        chartFetching = false;
        if (data && data.trend && data.trend.labels.length) {
          cachedData = data;
          renderChart(data);
          window.setTimeout(recoverChartIfNeeded, 800);
        } else {
          // No /insufficient data: leave the empty state in place (no canvas).
          cachedData = null;
          destroyChart();
        }
      })
      .catch(function (err) {
        chartFetching = false;
        console.error(
          "[InterviewIQ] Dashboard chart data could not be loaded: " +
          ((err && err.message) ? err.message : "request failed")
        );
      });
  }

  /* Idempotent initializer: whatever the ready-state / observer timing, the
   * chart is loaded exactly once and never re-initialized on the same canvas. */
  function initChartOnce() {
    var canvas = document.getElementById("iq-performance-chart");
    if (!canvas || chartInitiated) return;
    chartInitiated = true;
    if ("IntersectionObserver" in window && !prefersReduced) {
      var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting && !entry.target.dataset.loaded) {
            entry.target.dataset.loaded = "1";
            observer.disconnect();
            loadChart();
          }
        });
      }, { threshold: 0.2 });
      observer.observe(canvas);
      // Fallback: if it hasn't scrolled in yet within a moment, still draw it.
      window.setTimeout(function () {
        if (canvas && !canvas.dataset.loaded) {
          canvas.dataset.loaded = "1";
          loadChart();
        }
      }, 1200);
    } else {
      // No IntersectionObserver (or reduced motion): load directly, once.
      canvas.dataset.loaded = "1";
      loadChart();
    }
  }

  /* ---- Boot ---- */
  function boot() {
    initReveal();
    animateCounters();
    initProgressBars();
    initReadinessRing();
    initChartOnce();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // Refresh the chart on light/dark theme changes without a reload. Uses the
  // crash-safe reload path: re-render the cached payload, or refetch if the
  // cache was cleared after an error. Never destroys the chart permanently.
  if (window.MutationObserver && window.Chart) {
    new MutationObserver(function () {
      reloadChart();
    }).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
  }

  // If the chart ever collapses (e.g. the container was measured while hidden
  // during the reveal/layout), re-draw from the cached payload once the layout
  // has a real size. Debounced so rapid resizes never stack renders.
  window.addEventListener("resize", debounce(recoverChartIfNeeded, 120));
})();
