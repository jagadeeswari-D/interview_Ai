"""Light Theme browser QA for InterviewIQ (Playwright + system Chrome).

Run after seeding (qa/seed_qa.py) and starting the dev server:
    python qa/qa_browser.py

Produces:
    - qa/screenshots/...  (full-page captures per page/width/theme)
    - qa/qa_results.json  (numeric checks, console/page errors, verdicts)
    - console summary table
"""

import json
import os
import sqlite3
import sys
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_DIR, "instance", "interviewiq.db")
SHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
BASE_URL = os.environ.get("QA_BASE_URL", "http://127.0.0.1:5000")

EMAIL = "qa@example.com"
PASSWORD = "Qa-password-1"

LIGHT_BG = "rgb(238, 241, 246)"      # --bg in light
LIGHT_TEXT = "rgb(22, 26, 35)"       # --text in light
DARK_BG = "rgb(8, 9, 11)"            # --bg in dark
DARK_TEXT = "rgb(242, 243, 245)"     # --text in dark

FULL_WIDTHS = [1440, 1024, 768, 560, 360]
PAIR_WIDTHS = [1440, 360]
DARK_WIDTHS = [1440, 768]

from playwright.sync_api import sync_playwright  # noqa: E402


def parse_rgba(s):
    s = s.replace("rgba", "rgb").strip()
    inner = s[s.index("(") + 1 : s.rindex(")")]
    parts = [p.strip() for p in inner.split(",")]
    vals = []
    for p in parts:
        if p.endswith("%"):
            vals.append(int(float(p[:-1]) * 255 / 100))
        else:
            vals.append(int(round(float(p))))
    while len(vals) < 4:
        vals.append(255)
    return tuple(vals)


def luminance(rgb):
    def f(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb[0], rgb[1], rgb[2]
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(text_rgb, bg_rgb):
    l1, l2 = sorted([luminance(text_rgb), luminance(bg_rgb)], reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def alpha_over(fg_rgb, bg_rgb):
    a = fg_rgb[3] / 255.0
    return tuple(round(fg * a + bg * (1 - a)) for fg, bg in zip(fg_rgb[:3], bg_rgb[:3]))


def rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(rgb[:3])


def db_ids():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    completed = cur.execute(
        "SELECT id FROM interviews WHERE status='completed' AND overall_score IS NOT NULL "
        "ORDER BY id LIMIT 1"
    ).fetchone()
    live = cur.execute(
        "SELECT id, mode FROM interviews WHERE status='in_progress' ORDER BY id"
    ).fetchall()
    conn.close()
    return {
        "completed_id": completed[0] if completed else None,
        "live": {mode: i for i, mode in live},
    }


def overflow(page):
    return page.evaluate(
        "({sw: document.documentElement.scrollWidth,"
        " cw: document.documentElement.clientWidth,"
        " hw: document.body ? document.body.scrollWidth : 0})"
    )


class QARunner:
    def __init__(self):
        self.results = {"pages": {}, "issues": [], "fixes_considered": True}
        self.console_errors = []
        self.page_errors = []

    def run(self):
        ids = db_ids()
        pages = [
            ("dashboard", "/dashboard", FULL_WIDTHS, self.check_dashboard),
            ("roadmap", "/roadmap", FULL_WIDTHS, self.check_roadmap),
            ("practice", "/practice", FULL_WIDTHS, self.check_common),
            ("interview", "/interview", FULL_WIDTHS, self.check_interview_setup),
            ("analytics", "/analytics", FULL_WIDTHS, self.check_analytics),
            ("live-practice",
             f"/interview/{ids['live'].get('practice')}", PAIR_WIDTHS, self.check_live_practice),
            ("live-real",
             f"/interview/{ids['live'].get('real')}", PAIR_WIDTHS, self.check_live_real),
            ("history", "/history", PAIR_WIDTHS, self.check_common),
            ("replay", f"/replay/{ids['completed_id']}", PAIR_WIDTHS, self.check_common),
            ("resume", "/resume", PAIR_WIDTHS, self.check_common),
            ("reports", "/reports", PAIR_WIDTHS, self.check_common),
            ("profile", "/profile", PAIR_WIDTHS, self.check_common),
            ("settings", "/settings", PAIR_WIDTHS, self.check_common),
            ("error-404", "/definitely-not-a-real-route", PAIR_WIDTHS, self.check_common),
        ]
        dark_pages = ["dashboard", "roadmap", "analytics", "practice", "interview"]

        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            context.add_init_script(
                "localStorage.setItem('interviewiq-theme', 'light');"
            )
            page = context.new_page()
            self._wire_errors(page)

            # ---- Login (real user flow) ----
            page.goto(f"{BASE_URL}/login", wait_until="networkidle")
            page.fill('input[name="email"]', EMAIL)
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url("**/dashboard", timeout=15000)
            assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "light"

            # ---- Light pass ----
            for name, url, widths, checker in pages:
                self._run_page(page, name, url, widths, checker, theme="light")

            # ---- Auth/landing anonymous context (light + dark) ----
            self._auth_pass(browser, page)

            # ---- Dark regression (same logged-in context, real toggle) ----
            page.goto(f"{BASE_URL}/dashboard", wait_until="networkidle")
            page.click("#theme-toggle")
            assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark"
            for name, url, _w, checker in pages:
                if name not in dark_pages:
                    continue
                self._run_page(page, name, url, DARK_WIDTHS, checker, theme="dark")

            browser.close()

        self._write_report()

    # ------------------------------------------------------------------ infra
    def _wire_errors(self, page):
        def on_console(msg):
            if msg.type == "error":
                self.console_errors.append({"url": page.url, "text": msg.text})

        def on_pageerror(err):
            self.page_errors.append({"url": page.url, "text": str(err)})

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)

    def _run_page(self, page, name, url, widths, checker, theme):
        for w in widths:
            page.set_viewport_size({"width": w, "height": 900})
            try:
                page.goto(f"{BASE_URL}{url}", wait_until="networkidle", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(350)
            theme_now = page.evaluate("document.documentElement.getAttribute('data-theme')")
            ov = overflow(page)
            bg = page.evaluate("getComputedStyle(document.body).backgroundColor").strip()
            txt = page.evaluate("getComputedStyle(document.body).color").strip()
            result = {
                "url": url, "theme": theme_now, "width": w,
                "overflow": ov["sw"] - ov["cw"], "bg": bg, "text": txt,
            }
            # Scroll charts into view so canvases render before screenshots/checks.
            page.evaluate("document.querySelectorAll('canvas').forEach(c=>c.scrollIntoView({block:'center'}))")
            page.wait_for_timeout(700 if "chart" in checker.__name__ else 250)
            if checker and checker is not self.check_common:
                result.update(checker(page, w) or {})
            shot = os.path.join(SHOT_DIR, f"{name}_{w}_{theme}.png")
            page.screenshot(path=shot, full_page=True)
            result["screenshot"] = shot
            name_1 = f"{name}@{w}"
            self.results["pages"][name_1] = result
            self._pr(result, name_1)

    def _auth_pass(self, browser, main_page):
        ids = []
        for theme in ("light", "dark"):
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            ctx.add_init_script(f"localStorage.setItem('interviewiq-theme', '{theme}');")
            pg = ctx.new_page()
            self._wire_errors(pg)
            for w in (1440, 360):
                pg.set_viewport_size({"width": w, "height": 900})
                for label, url in (("login", "/login"), ("register", "/register"), ("error", "/nope")):
                    pg.goto(f"{BASE_URL}{url}", wait_until="networkidle", timeout=15000)
                    pg.wait_for_timeout(200)
                    ov = overflow(pg)
                    bg = pg.evaluate("getComputedStyle(document.body).backgroundColor").strip()
                    shot = os.path.join(SHOT_DIR, f"auth_{label}_{theme}_{w}.png")
                    pg.screenshot(path=shot, full_page=True)
                    key = f"auth-{label}@{theme}@{w}"
                    self.results["pages"][key] = {
                        "url": url, "theme": theme, "width": w,
                        "overflow": ov["sw"] - ov["cw"], "bg": bg,
                        "screenshot": shot,
                    }
                    self._pr(self.results["pages"][key], key)
            ctx.close()

    def _pr(self, r, key):
        flag = "OK "
        if r.get("overflow", 0) > 1:
            flag = "OVF"
        if r.get("bg") not in (LIGHT_BG, DARK_BG):
            flag = "BG!" if r.get("bg") else ""
        print(f"[{flag}] {key:38s} bg={r['bg']:<22s} ovf={r.get('overflow',0):>4}")

    # ------------------------------------------------------------- page checks
    def check_common(self, page, w):
        if w not in (1440, 768, 360):
            return {}
        out = {}
        txt = page.evaluate("getComputedStyle(document.querySelector('h1,h2')||document.body).color").strip()
        out["heading_color"] = txt
        return out

    def check_dashboard(self, page, w):
        out = {}
        if w == 1440:
            btn = page.evaluate(
                "(()=>{const b=document.querySelector('.iq-btn-primary');"
                "return b?getComputedStyle(b).backgroundColor:null})()"
            )
            sky = page.evaluate(
                "(()=>{const el=document.querySelector('.iq-skill-fill.is-high');"
                "return el?getComputedStyle(el).backgroundColor:null})()"
            )
            out["primary_btn"] = btn or ""
            out["skill_fill_high"] = sky or ""
            out.update(self._hover_tooltip(page, "#iq-performance-chart", "dash_tooltip"))
        return out

    def check_analytics(self, page, w):
        out = {}
        if w == 1440:
            heat = page.evaluate("""
                (()=>{
                  const out={};
                  document.querySelectorAll('.heatmap-cell').forEach(el=>{
                    const tier=(el.className.match(/is-(high|mid|low)/)||[])[1];
                    if(!out[tier]) out[tier]=[];
                    const cs=getComputedStyle(el);
                    out[tier].push({bg:cs.backgroundColor, text:cs.color,
                                    border:cs.borderColor});
                  });
                  const pill=document.querySelector('.iq-tone-pill');
                  const legendHigh=document.querySelector('.iq-tier-legend-swatch.is-high');
                  const legendMid=document.querySelector('.iq-tier-legend-swatch.is-mid');
                  const legendLow=document.querySelector('.iq-tier-legend-swatch.is-low');
                  return {heat: out, pill:pill?getComputedStyle(pill).backgroundColor:null,
                          legend:{
                            high:legendHigh?getComputedStyle(legendHigh).backgroundColor:null,
                            mid:legendMid?getComputedStyle(legendMid).backgroundColor:null,
                            low:legendLow?getComputedStyle(legendLow).backgroundColor:null}};
                })()
            """)
            out["heatmap"] = heat
            out.update(self._hover_tooltip(page, "#trend-chart", "trend_tooltip"))
            out.update(self._hover_tooltip(page, "#skills-chart", "skills_tooltip"))
        return out

    def check_roadmap(self, page, w):
        out = {}
        out["phases"] = page.evaluate("document.querySelectorAll('.rm-phase').length")
        if w == 1440:
            done = page.evaluate("""
                (()=>{const el=document.querySelector('.rm-phase.is-completed .rm-phase-node');
                return el?getComputedStyle(el).backgroundColor:null})()
            """)
            pending = page.evaluate("""
                (()=>{const el=document.querySelector('.rm-phase:not(.is-completed) .rm-phase-node');
                return el?getComputedStyle(el).backgroundColor:null})()
            """)
            out["node_done_bg"] = done or ""
            out["node_pending_bg"] = pending or ""
        return out

    def check_interview_setup(self, page, w):
        out = {"mode_cards": page.evaluate("document.querySelectorAll('[data-mode-card]').length")}
        if w == 1440:
            out["card_bg"] = page.evaluate(
                "(()=>{const c=document.querySelector('[data-mode-card]');"
                "return c?getComputedStyle(c).backgroundColor:null})()"
            ) or ""
            page.click('[data-mode-card="text-text"] input, [data-mode-card="text-text"]')
            page.wait_for_timeout(120)
            sel = page.evaluate("""
                (()=>{const el=document.querySelector('[data-mode-card].is-selected,'+
                '[data-mode-card="text-text"]');
                return el?{bg:getComputedStyle(el).backgroundColor,
                           border:getComputedStyle(el).borderColor}:null})()
            """)
            out["selected_state"] = sel
        return out

    def check_live_practice(self, page, w):
        return self._check_live(page, w, "practice")

    def check_live_real(self, page, w):
        return self._check_live(page, w, "real")

    def _check_live(self, page, w, mode):
        out = {}
        out["timer"] = page.evaluate(
            "(()=>{const t=document.getElementById('timer-value');"
            "return t?t.textContent.trim():null})()"
        )
        out["question"] = bool(page.locator(".riv-question, .sp-question-text").count())
        out["answer_area"] = bool(page.locator(".sp-answer-form, textarea").count())
        out["avatar"] = bool(page.locator("[data-avatar]").count())
        if w == 1440 and mode == "real":
            out["timer_color"] = page.evaluate(
                "(()=>{const t=document.getElementById('timer-value');"
                "return t?getComputedStyle(t).color:null})()"
            ) or ""
        return out

    def _hover_tooltip(self, page, canvas_sel, key):
        """Hover a chart point and pixel-sample the drawn tooltip region."""
        import io
        from PIL import Image
        loc = page.locator(canvas_sel)
        if loc.count() == 0:
            return {key: {"canvas": False}}
        box = loc.bounding_box()
        if not box or box["width"] < 10:
            return {key: {"canvas": False}}
        hx = box["x"] + box["width"] * 0.62
        hy = box["y"] + box["height"] * 0.40
        page.mouse.move(hx, hy)
        page.wait_for_timeout(550)
        png = page.screenshot(clip={"x": 0, "y": 0, "width": box["width"], "height": box["height"]})
        img = Image.open(io.BytesIO(png)).convert("RGB")
        # Sample a generous box around the hover point.
        x0 = max(0, int(box["width"] * 0.45))
        x1 = min(img.width, int(box["width"] * 0.85))
        y0 = max(0, int(box["height"] * 0.10))
        y1 = min(img.height, int(box["height"] * 0.80))
        crop = img.crop((x0, y0, x1, y1))
        white = dark = 0
        from collections import Counter
        cnt = Counter()
        data = crop.tobytes()
        for i in range(0, len(data), 3):
            r, g, b = data[i], data[i + 1], data[i + 2]
            if r > 245 and g > 245 and b > 245:
                white += 1
            if abs(r - 20) <= 12 and abs(g - 24) <= 12 and abs(b - 31) <= 12:
                dark += 1
            cnt[(r // 32, g // 32, b // 32)] += 1
        top = [f"#{r*32:02x}{g*32:02x}{b*32:02x}" for (r, g, b), _ in cnt.most_common(5)]
        theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
        return {key: {"theme": theme, "white_px": white, "dark_px": dark, "top_colors": top}}

    # ---------------------------------------------------------------- report
    def _write_report(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qa_results.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.results, fh, indent=2, default=str)
        print("\n=== QA RESULTS JSON ===")
        print(f"Total page checks: {len(self.results['pages'])}")
        print(f"Console errors: {len(self.console_errors)}")
        print(f"Page errors: {len(self.page_errors)}")
        for e in self.console_errors[:10]:
            print("  console:", e["url"], e["text"][:160])
        for e in self.page_errors[:10]:
            print("  pageerr:", e["url"], e["text"][:160])
        print(f"Report: {path}")


if __name__ == "__main__":
    os.makedirs(SHOT_DIR, exist_ok=True)
    QARunner().run()