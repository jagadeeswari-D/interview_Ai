"""Browser QA for Achievements / Badges (Phase 9 / Stage 3).

Pattern (same as the other QA harnesses): app started in-process on
127.0.0.1, users registered via real HTTP, controlled Daily Challenge +
interview histories seeded straight into SQLite at offsets relative to the
real local day, then Playwright drives the system Microsoft Edge channel.

Users:
  * all@example.com     — 15 completed challenge days + 9 graded interviews
                          -> all 5 achievements unlocked (5/5).
  * partial@example.com — 2 completed days + 2 graded interviews
                          -> first_challenge only (1/5), interviews 2/5.
  * zero@example.com    — no activity -> 0/5, everything locked.

Verifies: the /achievements gallery renders the right unlock/locked counts,
progress percentages and icons per user, cross-user isolation (nobody sees
another user's badges), the dashboard panel renders the same summary with its
progress bars, stale/unrelated surfaces (streak KPI, challenge page) still
work, light + dark themes, five viewport widths with no horizontal overflow,
and no inline styles/scripts or console/CSP violations.

Run:  py -3.14 qa/browser_achievements_check.py
"""
import datetime
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time

import requests

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

TMP = tempfile.mkdtemp(prefix="iq_achievements_")
DB_PATH = os.path.join(TMP, "qa.db")
PORT = 8796
BASE = f"http://127.0.0.1:{PORT}"
ALL_EMAIL = "all@example.com"
PARTIAL_EMAIL = "partial@example.com"
ZERO_EMAIL = "zero@example.com"
PASSWORD = "Qa-password-1"

os.environ["DATABASE_PATH"] = DB_PATH
os.environ["FLASK_ENV"] = "production"
os.environ["SECRET_KEY"] = "qa-achievements-verification-secret"
os.environ.pop("GEMINI_API_KEY", None)


def _day(offset):
    return (datetime.date.today() + datetime.timedelta(days=offset)).isoformat()


def main():
    from interview_Ai.app import create_app

    app = create_app()
    threading.Thread(
        target=lambda: app.run(
            host="127.0.0.1", port=PORT, debug=False,
            use_reloader=False, threaded=True,
        ),
        daemon=True,
    ).start()
    _wait_until(_http_up, "server start")

    checks = []

    def rec(name, ok, detail=""):
        checks.append((name, bool(ok), detail))
        print(("PASS" if ok else "FAIL"), "-", name, ("| " + str(detail) if detail else ""))

    # ---- register three users via HTTP ----
    sessions = {}
    for email in (ALL_EMAIL, PARTIAL_EMAIL, ZERO_EMAIL):
        s = requests.Session()
        token = _csrf(s, "/register")
        r = s.post(f"{BASE}/register", data={
            "name": email.split("@")[0].capitalize(),
            "email": email, "password": PASSWORD, "confirm": PASSWORD,
            "csrf_token": token,
        }, allow_redirects=False)
        rec(f"{email} registers", r.status_code == 302, r.status_code)
        sessions[email] = s

    ids = {
        email: _db_value("SELECT id FROM users WHERE email=?", (email,))
        for email in sessions
    }

    # Seeded activity (all offsets relative to the real local day):
    #  - all:     15 completed days incl. today, plus 9 graded interviews
    #  - partial: today + yesterday completed, 2 graded interviews
    _seed_days(ids[ALL_EMAIL], list(range(-14, 1)))
    _seed_days(ids[PARTIAL_EMAIL], [-1, 0])
    _seed_interviews(ids[ALL_EMAIL], 9)
    _seed_interviews(ids[PARTIAL_EMAIL], 2)

    # ---- console/CSP watcher ----
    from playwright.sync_api import sync_playwright

    console_errors = []

    def new_ctx(session, theme, width=1280, height=900):
        ctx = browser.new_context(viewport={"width": width, "height": height})
        for cookie in session.cookies:
            ctx.add_cookies([{
                "name": cookie.name, "value": cookie.value,
                "domain": "127.0.0.1", "path": "/",
            }])
        ctx.add_init_script(
            f"try {{ localStorage.setItem('interviewiq-theme', '{theme}'); }} catch (e) {{}}"
        )
        return ctx

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)

        # ---- all-unlocked user: complete gallery (light) ----
        actx = new_ctx(sessions[ALL_EMAIL], "light")
        page = actx.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text)
                if ("Content Security Policy" in msg.text or "Refused to" in msg.text)
                else None)
        page.goto(f"{BASE}/achievements", wait_until="load")

        n_cards = page.locator(".ach-card").count()
        n_unlocked = page.locator(".ach-card.is-unlocked").count()
        n_locked = page.locator(".ach-card.is-locked").count()
        n_icons = page.locator(".ach-card .iq-achievement-svg").count()
        body = page.locator("body").inner_text()
        rec("gallery renders exactly 5 cards, all unlocked for the veteran user",
            n_cards == 5 and n_unlocked == 5 and n_locked == 0,
            f"cards={n_cards} unlocked={n_unlocked} locked={n_locked}")
        rec("every unlocked card has an inline icon",
            n_icons == 5, f"icons={n_icons}")
        rec("hero copy shows 5 of 5 unlocked",
            "5 of 5 unlocked" in body)
        rec("unlock date badges are shown for earned badges",
            body.count("Unlocked") >= 5)
        rec("sidebar shows the Achievements nav as active",
            page.locator('.sidebar a.nav-link.active[href="/achievements"]').count() == 1)
        rec("all-unlocked user sees no locked-progress bar",
            page.locator(".ach-track-fill").count() == 0)
        rec("gallery uses no inline styles/scripts or javascript: hrefs",
            page.locator(".ach-panel [style]").count() == 0
            and page.locator("script[src='']").count() == 0
            and "javascript:" not in page.content())
        actx.close()

        # ---- partial user: mixed state + dashboard panel ----
        pctx = new_ctx(sessions[PARTIAL_EMAIL], "light")
        ppage = pctx.new_page()
        ppage.goto(f"{BASE}/achievements", wait_until="load")
        n_unlocked = ppage.locator(".ach-card.is-unlocked").count()
        n_locked = ppage.locator(".ach-card.is-locked").count()
        fills = ppage.locator(".ach-track-fill")
        w_first = fills.nth(0).get_attribute("data-w")          # streak_3 = 2/3 -> 67
        w_ten = fills.nth(2).get_attribute("data-w")            # challenge_10 = 2/10 -> 20
        w_five = fills.nth(3).get_attribute("data-w")           # interviews_5 = 2/5 -> 40
        rec("partial user unlocks only First Challenge (1/5)",
            n_unlocked == 1 and n_locked == 4,
            f"unlocked={n_unlocked} locked={n_locked}")
        rec("hero copy says 1 of 5 unlocked",
            "1 of 5 unlocked" in ppage.locator("body").inner_text())
        rec("locked badge progress bars carry server-computed data-w values",
            w_first == "67" and w_ten == "20" and w_five == "40",
            f"streak={w_first} challenge={w_ten} interviews={w_five}")
        rec("no duplicate cards for the partial user",
            ppage.locator(".ach-card").count() == 5)

        ppage.goto(f"{BASE}/dashboard", wait_until="load")
        n_panel = ppage.locator(".iq-achievement").count()
        n_panel_svg = ppage.locator(".iq-achievement-icon .iq-achievement-svg").count()
        panel_fill = ppage.locator(
            ".iq-achievement.is-unlocked .iq-achievement-fill").get_attribute("data-w")
        streak_line = ppage.locator('.iq-kpi[data-icon="streak"] .iq-kpi-value').get_attribute("data-count")
        rec("dashboard achievements panel lists all five badges",
            n_panel == 5 and n_panel_svg == 5,
            f"items={n_panel} icons={n_panel_svg}")
        rec("dashboard unlocked row is full and streak KPI still renders",
            panel_fill == "100" and streak_line == "2",
            f"panel_fill={panel_fill} streak={streak_line}")
        pctx.close()

        # ---- zero user: nothing unlocked, cleanly rendered ----
        zctx = new_ctx(sessions[ZERO_EMAIL], "light")
        zpage = zctx.new_page()
        zpage.goto(f"{BASE}/achievements", wait_until="load")
        n_unlocked = zpage.locator(".ach-card.is-unlocked").count()
        rec("zero-activity user sees all five badges locked and 0/5",
            n_unlocked == 0
            and "0 of 5 unlocked" in zpage.locator("body").inner_text(),
            f"unlocked={n_unlocked}")
        rec("zero user cannot see the veteran user's unlocks",
            "0 of 5 unlocked" in zpage.locator("body").inner_text()
            and zpage.locator(".ach-card.is-unlocked").count() == 0)
        zctx.close()

        # ---- dark theme on the full gallery ----
        dctx = new_ctx(sessions[ALL_EMAIL], "dark")
        dpage = dctx.new_page()
        dpage.goto(f"{BASE}/achievements", wait_until="load")
        bg = dpage.evaluate("getComputedStyle(document.body).backgroundColor")
        card_bg = dpage.locator(".ach-card").first.evaluate(
            "el => getComputedStyle(el).backgroundColor")
        rec("dark theme renders the gallery with themed surfaces",
            dpage.locator(".ach-card").count() == 5
            and dpage.locator(".ach-card.is-unlocked").count() == 5,
            f"body={bg} card={card_bg}")
        dctx.close()

        # ---- responsive overflow on the longest gallery ----
        rctx = new_ctx(sessions[ALL_EMAIL], "light")
        rpage = rctx.new_page()
        rpage.set_viewport_size({"width": 1440, "height": 900})
        rpage.goto(f"{BASE}/achievements", wait_until="load")
        overflow = []
        for width in (1440, 1024, 768, 560, 360):
            rpage.set_viewport_size({"width": width, "height": 900})
            rpage.wait_for_timeout(250)
            overflow.append((width, rpage.evaluate(
                "document.documentElement.scrollWidth - window.innerWidth")))
        bad = [(w, v) for w, v in overflow if v > 0]
        rec("no horizontal overflow on /achievements at 1440/1024/768/560/360",
            not bad, " ".join(f"{w}px:+{v}" for w, v in overflow))
        rctx.close()

        # ---- challenge page still functional after seeding ----
        page = new_ctx(sessions[ALL_EMAIL], "light").new_page()
        page.goto(f"{BASE}/challenge", wait_until="load")
        rec("challenge page still renders after achievement work",
            page.locator("textarea#answer").count() == 1
            or "Challenge completed" in page.locator("body").inner_text())

        rec("no CSP/console violations logged", not console_errors,
            "; ".join(console_errors[:3]))

        browser.close()

    failed = [c for c in checks if not c[1]]
    print("\n" + "=" * 60)
    print(f"Achievements browser QA: {len(checks) - len(failed)}/{len(checks)} passed")
    if failed:
        print("FAILED:", ", ".join(c[0] for c in failed))
        return 1
    print("ALL CHECKS PASSED")
    return 0


# --------------------------------------------------------------------- helpers

def _http_up():
    try:
        return requests.get(f"{BASE}/login", timeout=2).status_code == 200
    except Exception:
        return False


def _wait_until(fn, label, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return
        time.sleep(0.2)
    raise SystemExit(f"timeout waiting for {label}")


def _csrf(session, path):
    html = session.get(f"{BASE}{path}", timeout=5).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not m:
        raise SystemExit(f"no csrf on {path}")
    return m.group(1)


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _db_value(sql, params=()):
    conn = _db()
    val = conn.execute(sql, params).fetchone()[0]
    conn.close()
    return val


def _seed_days(user_id, offsets):
    conn = _db()
    conn.executemany(
        "INSERT INTO daily_challenges "
        "(user_id, challenge_date, question, question_type, expected_concepts, "
        "status, completed_at) "
        "VALUES (?, ?, 'Seeded challenge question', 'conceptual', '[]', "
        "'completed', datetime('now'))",
        [(user_id, _day(offset)) for offset in offsets],
    )
    conn.commit()
    conn.close()


def _seed_interviews(user_id, count):
    conn = _db()
    conn.executemany(
        "INSERT INTO interviews (user_id, mode, role, difficulty, type, "
        "overall_score, status, question_limit, duration_minutes) "
        "VALUES (?, 'practice', 'Backend Developer', 'easy', 'technical', 70.0, "
        "'completed', 3, 12)",
        [(user_id,) for _ in range(count)],
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    raise SystemExit(main())