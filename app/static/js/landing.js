/* InterviewIQ — cinematic landing engine.
 * Every transform is a pure function of scroll progress and/or a lerped
 * pointer position, so all motion reverses naturally when scrolling up or
 * moving the cursor. Runs behind a single requestAnimationFrame; rects are
 * cached and refreshed on resize only. Degrades to a fully static, readable
 * page when JS is unavailable or reduced motion is preferred.
 */
(function () {
  "use strict";

  var REDUCED =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var doc = document.documentElement;
  var vw = 0;
  var vh = 0;
  var fps = performance.now() / 1000;
  var wantFrames = true;
  var pointerActive = false;
  var fx3d = 1; // 0 below 640px: disables heavy 3D rotation, keeps parallax
  var state = {
    pointer: { nx: 0, ny: 0, tx: 0, ty: 0, gx: -1200, gy: -1200, x: -1200, y: -1200 },
    layers: [],
    splitBlocks: [],
    secHero: { top: 0, height: 1 },
    secStage2: { top: 0, height: 1 },
    secEditorial: { top: 0, height: 1 },
    secProduct: { top: 0, height: 1 },
    secSmart: { top: 0, height: 1 },
    secModes: { top: 0, height: 1 },
    secHow: { top: 0, height: 1 },
    secDash: { top: 0, height: 1 },
    secStack: { top: 0, height: 1 },
    secCta: { top: 0, height: 1 },
    smartRows: [],
    modeHot: -1,
    modeCard: [],
    modesBound: false,
    aiTargets: [],
    aiNames: [],
    ringTargets: [86, 64, 37],
    metricTargets: [],
  };

  var nodes = {
    nav: document.getElementById("ln-nav"),
    hero: document.getElementById("hero"),
    copy: document.querySelector(".ln-hero-copy"),
    ghost: document.querySelector(".ln-ghost"),
    heroBg: document.getElementById("heroBg"),
    heroGrid: document.getElementById("heroGrid"),
    motes: document.querySelector(".ln-motes"),
    scene: document.getElementById("heroScene"),
    stage: document.querySelector(".ln-stage"),
    hint: document.getElementById("heroHint"),
    stage2: document.getElementById("stage2"),
    glow: document.getElementById("cursorGlow"),
    railFill: document.getElementById("railFill"),
    editorial: document.querySelector(".ln-editorial"),
    edBg: document.querySelector(".ln-ed-bg"),
    edLight: document.querySelector(".ln-ed-light"),
    product: document.getElementById("product"),
    prodTitle: document.querySelector(".ln-reimagine-title"),
    rvDevice: document.querySelector(".ln-rv-device"),
    aiPath: document.getElementById("aiPath"),
    aiVals: document.querySelectorAll(".ln-ai-m > em"),
    aiBars: document.querySelectorAll(".ln-ai-m > i > b"),
    smart: document.getElementById("smart"),
    smartStage: document.querySelector(".ln-smart-stage"),
    spCard: document.getElementById("spCard"),
    grid: document.getElementById("modeGrid"),
    modeCards: document.querySelectorAll(".ln-mode"),
    how: document.getElementById("how"),
    trackFill: document.getElementById("trackFill"),
    steps: document.querySelectorAll("#how .ln-how-step"),
    howStage: document.querySelector(".ln-how-stage"),
    howScreens: document.querySelectorAll(".ln-how-screen"),
    dash: document.getElementById("dash"),
    scoreNum: document.getElementById("scoreNum"),
    rings: document.querySelectorAll(".ln-syp-svg .ring"),
    metricVals: document.querySelectorAll(".ln-metric b"),
    metricFills: document.querySelectorAll(".ln-metric-fill"),
    stack: document.getElementById("stack"),
    stackCards: document.querySelectorAll(".ln-stack-card"),
    cta: document.getElementById("cta"),
    ctaGlow: document.getElementById("ctaGlow"),
    ctaTitle: document.querySelector(".ln-cta-title"),
    ctaSub: document.querySelector(".ln-cta-sub"),
    ctaActions: document.querySelector(".ln-cta-actions"),
  };

  var glowHalf = 170;
  var stackHeight = 0;

  function clamp(v, lo, hi) {
    return v < lo ? lo : v > hi ? hi : v;
  }

  function lerp(a, b, t) {
    return a + (b - a) * t;
  }

  function eased(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3);
  }

  function smoothstep(a, b, t) {
    var x = clamp((t - a) / (b - a), 0, 1);
    return x * x * (3 - 2 * x);
  }

/* Progress of a document-rect through the viewport:
 * 0 = just entering at the bottom, 1 = fully through the top.
 * rect.top is a document coordinate; subtract the current scroll
 * so progress is recomputed live on every frame (reversible).
 */
  function viewProgress(rect) {
    var span = (rect.height || vh) + vh;
    var rTop = (rect.top || 0) - (window.scrollY || 0);
    var p = (vh - rTop) / span;
    return clamp(p, 0, 1);
  }

  function classNum(cls) {
    var m = /w(\d+)/.exec(cls);
    return m ? parseInt(m[1], 10) : 0;
  }

  function measure() {
    vw = doc.clientWidth;
    vh = window.innerHeight;
    fx3d = vw >= 640 ? 1 : 0;
    var sc = window.scrollY || 0;

    function rectTop(el) {
      if (!el) return { top: 0, height: 1 };
      var r = el.getBoundingClientRect();
      return { top: r.top + sc, height: r.height };
    }

    state.secHero = rectTop(nodes.hero);
    state.secStage2 = rectTop(nodes.stage2);
    state.secEditorial = rectTop(nodes.editorial);
    state.secProduct = rectTop(nodes.product);
    state.secSmart = rectTop(nodes.smart);
    state.secModes = rectTop(nodes.grid);
    state.secHow = rectTop(nodes.how);
    state.secDash = rectTop(nodes.dash);
    state.secStack = rectTop(nodes.stack);
    stackHeight = state.secStack.height;
    state.secCta = rectTop(nodes.cta);

    var sceneRect = nodes.scene ? nodes.scene.getBoundingClientRect() : null;
    var layerEls = document.querySelectorAll(".ln-aq-layer, .ln-aq-card");
    state.layers = Array.prototype.map.call(layerEls, function (el) {
      var r = el.getBoundingClientRect();
      var cx = r.left + r.width / 2;
      var cy = r.top + r.height / 2;
      return {
        el: el,
        d: Number(el.getAttribute("data-depth")) || 0,
        z: Number(el.getAttribute("data-z")) || 0,
        spread: el.getAttribute("data-spread") === "1",
        panel: el.classList.contains("ln-aq-layer"),
        dirX: sceneRect
          ? cx < sceneRect.left + sceneRect.width / 2 ? -1 : 1
          : 1,
        dirY: sceneRect
          ? cy < sceneRect.top + sceneRect.height / 2 ? -1 : 1
          : 1,
      };
    });

    state.smartRows = Array.prototype.map.call(
      document.querySelectorAll(".ln-sp-row"),
      function (el, i) {
        return { el: el, i: i };
      }
    );

    state.aiTargets = Array.prototype.map.call(nodes.aiBars, function (b) {
      return classNum(b.className);
    });
    state.aiNames = Array.prototype.map.call(nodes.aiVals, function (em) {
      var m = /\d+/.exec(em.textContent || "");
      return m ? parseInt(m[0], 10) : 0;
    });
    state.metricTargets = Array.prototype.map.call(nodes.metricVals, function (b) {
      var v = parseInt(b.textContent, 10);
      return isNaN(v) ? 0 : v;
    });

    state.modeCard = [];
    for (var msi = 0; msi < nodes.modeCards.length; msi++) {
      state.modeCard.push({
        rx: 0, ry: 0, tx: 0, ty: 0, tz: 0, s: 1,
        trx: 0, try: 0, ttx: 0, tty: 0, ttz: 0, ts: 1,
        hover: false
      });
    }

    if (nodes.glow) {
      glowHalf = nodes.glow.offsetWidth / 2 || 170;
    }

    for (var i = 0; i < state.splitBlocks.length; i++) {
      var r = state.splitBlocks[i].el.getBoundingClientRect();
      state.splitBlocks[i].top = r.top + sc;
      state.splitBlocks[i].height = r.height;
    }
  }

  function buildSplit(el) {
    var words = el.textContent.trim().split(/\s+/);
    el.textContent = "";
    var frag = document.createDocumentFragment();
    Array.prototype.forEach.call(words, function (word) {
      var s = document.createElement("span");
      s.className = "wd";
      s.textContent = word;
      s.appendChild(document.createTextNode("\u00a0"));
      frag.appendChild(s);
    });
    el.appendChild(frag);
    return { el: el, words: words.length, top: 0, height: 0 };
  }

  function matchMediaTouch() {
    return (
      window.matchMedia &&
      window.matchMedia("(pointer: coarse)").matches
    );
  }

  function init() {
    if (REDUCED) {
      return; // static fallback handled entirely by CSS
    }

    doc.classList.add("ln-js");

    var splitEls = document.querySelectorAll("[data-split]");
    Array.prototype.forEach.call(splitEls, function (el) {
      state.splitBlocks.push(buildSplit(el));
    });

    if (!matchMediaTouch()) {
      window.addEventListener(
        "pointermove",
        function (e) {
          pointerActive = true;
          state.pointer.tnx = (e.clientX / vw) * 2 - 1;
          state.pointer.tny = (e.clientY / vh) * 2 - 1;
          state.pointer.x = e.clientX;
          state.pointer.y = e.clientY;
          wantFrames = true;
        },
        { passive: true }
      );
    }

    window.addEventListener("resize", function () {
      measure();
      wantFrames = true;
    }, { passive: true });
    window.addEventListener("scroll", function () {
      wantFrames = true;
    }, { passive: true });

    wantFrames = true;
    measure();
    requestAnimationFrame(frame);
  }

  /* ----------------------------------------------------- frame */
  function frame(now) {
    var dt = now / 1000 - fps;
    fps = now / 1000;
    if (!document.hidden) {
      if (wantFrames || pointerActive) {
        render(dt);
        wantFrames = false;
      }
    }
    requestAnimationFrame(frame);
  }

  function refreshModeStates() {
    var hot = -1;
    for (var mi = 0; mi < nodes.modeCards.length; mi++) {
      if (state.modeCard[mi].hover) {
        hot = mi;
        break;
      }
    }
    state.modeHot = hot;
    for (var mj = 0; mj < nodes.modeCards.length; mj++) {
      nodes.modeCards[mj].classList.toggle("is-hot", mj === hot);
      nodes.modeCards[mj].classList.toggle("is-away", hot !== -1 && mj !== hot);
      if (hot !== -1 && mj !== hot) {
        state.modeCard[mj].ttz = -40;
        state.modeCard[mj].ts = 0.94;
      } else if (hot === -1) {
        state.modeCard[mj].ttz = 0;
        state.modeCard[mj].ts = 1;
      }
    }
  }

  function bindModeEvents() {
    if (state.modesBound || !nodes.modeCards.length) return;
    for (var i = 0; i < nodes.modeCards.length; i++) {
      (function (idx, el) {
        el.addEventListener("mouseenter", function () {
          state.modeCard[idx].hover = true;
          refreshModeStates();
          wantFrames = true;
        });
        el.addEventListener("mousemove", function (e) {
          if (!state.modeCard[idx].hover) return;
          var r = el.getBoundingClientRect();
          var cnx = clamp(
            (e.clientX - (r.left + r.width / 2)) / (r.width / 2),
            -1,
            1
          );
          var cny = clamp(
            (e.clientY - (r.top + r.height / 2)) / (r.height / 2),
            -1,
            1
          );
          var st = state.modeCard[idx];
          st.trx = -cny * 8;
          st.try = cnx * 9;
          st.ttx = cnx * 14;
          st.tty = -8;
          st.ttz = 30;
          st.ts = 1.04;
          wantFrames = true;
        });
        el.addEventListener("mouseleave", function () {
          state.modeCard[idx].hover = false;
          var st2 = state.modeCard[idx];
          st2.trx = 0;
          st2.try = 0;
          st2.ttx = 0;
          st2.tty = 0;
          st2.ttz = 0;
          st2.ts = 1;
          refreshModeStates();
          wantFrames = true;
        });
      })(i, nodes.modeCards[i]);
    }
    state.modesBound = true;
  }

  function render(dt) {
    var k = Math.min(1, (dt || 0.016) * 7);
    var p = state.pointer;

    if (pointerActive) {
      p.nx = lerp(p.nx, p.tnx, k);
      p.ny = lerp(p.ny, p.tny, k);
      p.gx = lerp(p.gx, p.x, k * 1.3);
      p.gy = lerp(p.gy, p.y, k * 1.3);
    }

    var scrolled = window.scrollY || 0;

    if (nodes.nav) {
      nodes.nav.classList.toggle("is-scrolled", scrolled > 24);
    }

    /* --- Progress rail. --- */
    if (nodes.railFill) {
      var total = Math.max(1, doc.scrollHeight - vh);
      nodes.railFill.style.transform =
        "scaleY(" + clamp(scrolled / total, 0, 1).toFixed(4) + ")";
    }

    /* --- HERO: four continuous phases (pure function of scroll). --- */
    var pHero = clamp((scrolled - state.secHero.top) / (vh * 0.92), 0, 1);

    /* --- STAGE 2: the interview scene runs off its own scroll window. --- */
    var pStage2 = smoothstep(
      0.12,
      0.85,
      (scrolled - state.secStage2.top) / (state.secStage2.height || vh)
    );
    var pSpread2 = smoothstep(0.3, 0.72, pStage2); // cards move out

    if (nodes.copy) {
      var lift = easeOutCubic(pHero) * vh * 0.42;
      nodes.copy.style.transform =
        "translate3d(0, " + (-lift).toFixed(1) + "px, 0) scale(" +
        (1 - 0.18 * pHero).toFixed(4) + ")";
      nodes.copy.style.opacity = String(Math.max(0, 1 - pHero * 1.15));
    }

    if (nodes.ghost) {
      // Ghost drifts in the opposite direction to the cursor for depth.
      nodes.ghost.style.transform =
        "translate3d(" + (-p.nx * 10).toFixed(2) + "px, " +
        (-p.ny * 8).toFixed(2) + "px, 0) translate(-50%, calc(-54% - " +
        (pHero * 46).toFixed(1) + "%)) translateZ(-80px)";
      nodes.ghost.style.opacity = String(1 - pHero * 0.85);
    }

    if (nodes.heroBg) {
      nodes.heroBg.style.transform =
        "translate3d(" + (p.nx * 5).toFixed(2) + "px, " +
        (p.ny * 5).toFixed(2) + "px, 0) scale(" +
        (1 + 0.55 * pHero).toFixed(4) + ")";
      nodes.heroBg.style.opacity = String(Math.max(0, 0.8 - pHero * 0.5));
    }

    if (nodes.heroGrid) {
      nodes.heroGrid.style.transform =
        "translate3d(" + (p.nx * 4).toFixed(2) + "px, " +
        (p.ny * 4).toFixed(2) + "px, 0)";
      nodes.heroGrid.style.opacity = String(Math.max(0, 0.55 - pHero * 0.35));
    }

    if (nodes.motes) {
      nodes.motes.style.transform =
        "translate3d(" + (p.nx * 10).toFixed(2) + "px, " +
        (p.ny * 10 - pHero * 170).toFixed(2) + "px, 0)";
    }

    if (nodes.hint) {
      nodes.hint.style.opacity = String(1 - smoothstep(0, 0.14, pHero));
    }

    /* --- Central scene: gentle tilt (max ~7deg), zoom, upward-forward rise. --- */
    if (nodes.scene && nodes.stage) {
      var tiltX = fx3d * (p.nx * 7 + 5 * pSpread2);
      var tiltY = fx3d * (-p.ny * 5 + 4 * pSpread2);
      var sceneY = -easeOutCubic(pStage2) * vh * 0.1;
      nodes.scene.style.transform =
        "rotateY(" + tiltX.toFixed(3) + "deg) rotateX(" + tiltY.toFixed(3) +
        "deg) scale(" + (1 + 0.42 * pStage2).toFixed(4) +
        ") translate3d(0, " + sceneY.toFixed(1) + "px, 0)";
      nodes.stage.style.opacity = String(1 - pStage2 * 0.92);
    }

    /* --- Depth layers: parallax per layer + outward card spread. --- */
    for (var L = 0; L < state.layers.length; L++) {
      var ly = state.layers[L];
      var lx = ly.d * p.nx * 30;
      var ly2 = ly.d * p.ny * 30;
      var sx = 0;
      var sy = 0;
      if (ly.spread && fx3d) {
        sx = ly.dirX * 60 * pSpread2;
        sy = ly.dirY * 26 * pSpread2;
      }
      var t =
        "translate3d(" + (lx + sx).toFixed(2) + "px, " +
        (ly2 + sy).toFixed(2) + "px, " + ly.z + "px)";
      if (ly.panel) {
        t += " rotateY(" + (fx3d * 6 * pSpread2).toFixed(2) +
          "deg) rotateX(" + (fx3d * 4 * pSpread2).toFixed(2) + "deg)";
      }
      ly.el.style.transform = t;
    }

    /* --- Cursor glow (lerped toward pointer, hidden on touch). --- */
    if (nodes.glow && pointerActive && vw >= 640) {
      nodes.glow.style.transform =
        "translate3d(" + (p.gx - glowHalf).toFixed(1) + "px, " +
        (p.gy - glowHalf).toFixed(1) + "px, 0)";
      nodes.glow.style.opacity =
        String((0.55 * (1 - Math.abs(p.nx) * 0.25)).toFixed(3));
      wantFrames =
        Math.abs(p.nx - p.tnx) + Math.abs(p.ny - p.tny) +
        Math.abs(p.gx - p.x) + Math.abs(p.gy - p.y) > 0.15;
    }

    /* --- Editorial: background grid + light react to scroll. --- */
    var pe = viewProgress(state.secEditorial);
    var peMid = 0.5 - pe;
    if (nodes.edBg) {
      nodes.edBg.style.transform =
        "translate3d(0, " + (peMid * 60).toFixed(1) + "px, 0)";
    }
    if (nodes.edLight) {
      nodes.edLight.style.transform =
        "translate3d(" + (peMid * 90).toFixed(1) + "px, " +
        (peMid * 40).toFixed(1) + "px, 0)";
    }

    /* --- Split-text reveals (pure function of scroll). --- */
    for (var i = 0; i < state.splitBlocks.length; i++) {
      var block = state.splitBlocks[i];
      if (!block) continue;
      var pb = viewProgress({ top: block.top, height: block.height });
      var spread = Math.max(0.28, 1 - pb * 0.5);
      var words = block.el.querySelectorAll(".wd");
      var step = 0.55 / Math.max(1, block.words);
      for (var w = 0; w < words.length; w++) {
        var tw = clamp((pb - w * step) / spread, 0, 1);
        words[w].style.opacity = String(tw * tw * (3 - 2 * tw));
        words[w].style.transform =
          "translate3d(0, " + ((1 - tw) * 12).toFixed(1) + "px, 0)";
      }
    }

    /* --- INTERVIEW AI product screen: device forward + graph draw. --- */
    if (nodes.rvDevice) {
      var pr = viewProgress(state.secProduct);
      var ke = eased(clamp(pr * 1.15, 0, 1));
      var slide = (0.5 - Math.min(1, pr * 2)) * 80;
      nodes.rvDevice.style.transform =
        "translate3d(" + slide.toFixed(1) + "px, " +
        ((1 - ke) * 60).toFixed(1) + "px, " +
        ((ke - 1) * 120).toFixed(1) + "px) rotateY(" +
        ((ke - 2) * 3 * fx3d).toFixed(2) + "deg) rotateX(" +
        ((2 - pr * 2) * fx3d).toFixed(2) + "deg) scale(" +
        (0.86 + 0.14 * Math.min(1, pr * 1.4)).toFixed(4) + ")";
      nodes.rvDevice.style.opacity =
        String((0.15 + 0.85 * smoothstep(0, 0.06, pr)).toFixed(3));

      if (nodes.aiPath) {
        nodes.aiPath.style.strokeDashoffset = String((1 - ke).toFixed(3));
      }
      Array.prototype.forEach.call(nodes.aiBars, function (b, bi) {
        var target = state.aiTargets[bi] || 0;
        b.style.width = String((target * ke).toFixed(1)) + "%";
      });
      Array.prototype.forEach.call(nodes.aiVals, function (em, ei) {
        var target = state.aiNames[ei] || 0;
        em.textContent = String(Math.round(target * ke)) + "%";
      });
    }

    if (nodes.prodTitle) {
      var pc2 = viewProgress(state.secProduct);
      nodes.prodTitle.style.transform =
        "translateY(" + ((1 - pc2) * 24).toFixed(1) + "px)";
      nodes.prodTitle.style.opacity = String(clamp(pc2 * 1.6, 0, 1));
    }

    /* --- Smart Practice: scroll settle + pointer tilt. --- */
    var ps2 = viewProgress(state.secSmart);
    var sc3 = clamp(ps2 * 2, 0, 1);
    if (nodes.smartStage) {
      nodes.smartStage.style.transform =
        "translateY(" + ((1 - sc3) * 40).toFixed(1) + "px) scale(" +
        (0.94 + 0.06 * sc3).toFixed(4) + ")";
    }
    if (nodes.spCard && pointerActive && !matchMediaTouch() && fx3d) {
      var rect = nodes.spCard.getBoundingClientRect();
      var nx = clamp((p.x - (rect.left + rect.width / 2)) / (rect.width / 2), -1, 1);
      var ny = clamp((p.y - (rect.top + rect.height / 2)) / (rect.height / 2), -1, 1);
      nodes.spCard.style.transform =
        "translateZ(28px) rotateX(" + (-ny * 5).toFixed(2) + "deg) rotateY(" +
        (nx * 6).toFixed(2) + "deg)";
      for (var s = 0; s < state.smartRows.length; s++) {
        var row = state.smartRows[s];
        row.el.style.transform =
          "translate3d(" + (nx * 4).toFixed(1) + "px, 0, " +
          (22 + row.i * 3).toFixed(0) + "px)";
      }
    } else if (nodes.spCard) {
      nodes.spCard.style.transform = "translateZ(28px)";
      for (var s2 = 0; s2 < state.smartRows.length; s2++) {
        state.smartRows[s2].el.style.transform =
          "translate3d(0, 0, " + (22 + state.smartRows[s2].i * 3).toFixed(0) + "px)";
      }
    }

    /* --- Choose Your Mode: real mouse events drive tilt, rAF smooths. --- */
    if (!state.modesBound && nodes.modeCards.length && !matchMediaTouch()) {
      bindModeEvents();
    }
    var modesOn =
      nodes.modeCards.length && !matchMediaTouch() && fx3d;
    if (!modesOn && nodes.modeCards.length) {
      for (var mz = 0; mz < nodes.modeCards.length; mz++) {
        var msz = state.modeCard[mz];
        msz.trx = 0;
        msz.try = 0;
        msz.ttx = 0;
        msz.tty = 0;
        msz.ttz = 0;
        msz.ts = 1;
      }
    }
    if (nodes.modeCards.length && state.modeCard.length === nodes.modeCards.length) {
      var mease = Math.min(1, (dt || 0.016) * 8);
      for (var m2 = 0; m2 < nodes.modeCards.length; m2++) {
        var cr2 = nodes.modeCards[m2];
        var mst = state.modeCard[m2];
        mst.rx = lerp(mst.rx, mst.trx, mease);
        mst.ry = lerp(mst.ry, mst.try, mease);
        mst.tx = lerp(mst.tx, mst.ttx, mease);
        mst.ty = lerp(mst.ty, mst.tty, mease);
        mst.tz = lerp(mst.tz, mst.ttz, mease);
        mst.s = lerp(mst.s, mst.ts, mease);
        cr2.style.transform =
          "rotateX(" + mst.rx.toFixed(2) + "deg) rotateY(" + mst.ry.toFixed(2) +
          "deg) translate3d(" + mst.tx.toFixed(1) + "px, " + mst.ty.toFixed(1) +
          "px, " + mst.tz.toFixed(1) + "px) scale(" + mst.s.toFixed(3) + ")";
      }
    }

    /* --- How It Works: scroll-driven step states. --- */
    var pw = viewProgress(state.secHow);
    if (nodes.trackFill) {
      nodes.trackFill.style.transform = "scaleY(" + pw.toFixed(4) + ")";
    }
    var nSteps = nodes.steps.length;
    if (nSteps) {
      var rel = pw * nSteps;
      var activeStep = Math.max(0, Math.min(nSteps - 1, Math.floor(rel)));
      Array.prototype.forEach.call(nodes.steps, function (step, si) {
        step.classList.toggle("is-past", si < activeStep);
        step.classList.toggle("is-active", si === activeStep);
        step.classList.toggle("is-next", si === activeStep + 1);
      });

      /* Sticky centre visual: each mini-screen occupies a step window. */
      if (nodes.howStage) {
        nodes.howStage.style.transform =
          "rotateY(" + ((0.5 - pw) * 16).toFixed(2) + "deg) rotateX(" +
          ((0.5 - pw) * 6).toFixed(2) + "deg)";
        Array.prototype.forEach.call(nodes.howScreens, function (screen, si) {
          var oi =
            smoothstep(si - 0.12, si + 0.04, rel) *
            (1 - smoothstep(si + 0.96, si + 1.12, rel));
          oi = clamp(oi, 0, 1);
          var depth = (1 - oi) * -140;
          screen.style.zIndex = String(1 + Math.round(oi * 10));
          screen.style.opacity = String(oi.toFixed(3));
          screen.style.visibility = oi > 0.001 ? "visible" : "hidden";
          screen.style.transform =
            "translateZ(" + depth.toFixed(1) + "px) scale(" +
            (0.9 + 0.1 * oi).toFixed(4) + ") translateY(" +
            ((1 - oi) * 46).toFixed(1) + "px)";
          screen.style.filter = "blur(" + ((1 - oi) * 7).toFixed(2) + "px)";
        });
      }
    }

    /* --- See Your Progress: rings, counter, fills, depth. --- */
    var pd = viewProgress(state.secDash);
    var pdE = eased(clamp(pd * 1.6, 0, 1));
    if (nodes.scoreNum) {
      nodes.scoreNum.textContent = String(Math.round(pdE * 86));
      nodes.scoreNum.style.transform =
        "scale(" + (0.9 + 0.12 * pdE).toFixed(4) + ")";
    }
    Array.prototype.forEach.call(nodes.rings, function (ring, ri) {
      var target = state.ringTargets[ri] || 0;
      ring.style.strokeDashoffset =
        String((100 - target * pdE).toFixed(2));
    });
    Array.prototype.forEach.call(nodes.metricVals, function (b, mi) {
      var target = state.metricTargets[mi] || 0;
      b.textContent = String(Math.round(target * pdE));
    });
    Array.prototype.forEach.call(nodes.metricFills, function (fill, mi) {
      var target = state.metricTargets[mi] || 0;
      fill.style.width = String((target * pdE).toFixed(1)) + "%";
    });
    if (nodes.dash) {
      nodes.dash.style.transform =
        "perspective(1400px) rotateX(" + (6 * (1 - pdE)).toFixed(2) + "deg)";
      Array.prototype.forEach.call(
        document.querySelectorAll(".ln-metric"),
        function (metric, mi) {
          metric.style.transform =
            "rotateX(" + ((1 - pdE) * 4).toFixed(2) + "deg) translateZ(" +
            (mi * 16 - 24).toFixed(0) + "px)";
        }
      );
    }

    /* --- Feature stack: one card visible at a time. Each card lifts and
     * fades out fully by the midpoint of its scroll slot, so the next card
     * becomes visible only after the previous one is gone. --- */
    var ps = viewProgress(state.secStack);
    var n = nodes.stackCards.length;
    for (var c = 0; c < n; c++) {
      var covered = clamp(ps * n - c, 0, 1);
      var gone = smoothstep(0.05, 0.5, covered);
      var card2 = nodes.stackCards[c];
      card2.style.zIndex = String(c + 1);
      card2.style.transform =
        "translate3d(0, " + (-gone * 96).toFixed(1) + "px, " +
        (-gone * 140).toFixed(1) + "px) scale(" +
        (1 - 0.08 * gone).toFixed(4) + ")";
      card2.style.opacity = String(1 - gone);
    }

    /* --- Final CTA. ---
     * The CTA is the last, full-height section: viewProgress() only reaches 1
     * once a section scrolls fully past the top of the viewport, which a final
     * full-height section can never do -- leaving the reveal stranded at ~40%
     * forever. Drive its progress instead off how much of the viewport the
     * section currently fills, so it becomes fully dominant on-screen.
     */
    var pc = clamp(
      (vh - (state.secCta.top - scrolled)) / (vh * 1.1),
      0,
      1
    );
    if (nodes.ctaGlow) {
      nodes.ctaGlow.style.transform =
        "scale(" + (0.86 + 0.5 * eased(pc)).toFixed(4) + ")";
      nodes.ctaGlow.style.opacity = String(0.7 - pc * 0.25);
    }
    if (nodes.ctaTitle) {
      nodes.ctaTitle.style.transform =
        "translateY(" + ((1 - pc) * 40).toFixed(1) + "px) scale(" +
        (0.9 + 0.1 * pc).toFixed(4) + ")";
      nodes.ctaTitle.style.opacity = String(0 + pc);
    }
    if (nodes.ctaSub) {
      nodes.ctaSub.style.transform =
        "translateY(" + ((1 - pc) * 84).toFixed(1) + "px)";
      nodes.ctaSub.style.opacity = String(pc);
    }
    if (nodes.ctaActions) {
      nodes.ctaActions.style.transform =
        "translateY(" + ((1 - pc) * 56).toFixed(1) + "px)";
      nodes.ctaActions.style.opacity = String(pc);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();