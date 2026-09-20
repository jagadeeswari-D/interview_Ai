"""Browser QA for the Daily Challenge streak (Phase 9 / Stage 2).

Pattern (same as the other QA harnesses): app started in-process on
127.0.0.1, users registered via real HTTP, controlled Daily Challenge history
seeded straight into SQLite at offsets relative to the real local day, then
Playwright drives the system Microsoft Edge channel.

Verifies: the dashboard streak tile shows current + best values from server
computation, completing today's challenge advances the streak exactly once,
same-day refresh/re-submission never inflates it, a broken (gapless-logic)
history displays correctly per user, light + dark themes, five viewport
widths with no horizontal overflow, no console/CSP violations returned by the
streak page, navigation still works, and the Daily Challenge page still
functions after completion.

Run:  py -3.14 qa/browser_streak_check.py
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

TMP = tempfile.mkdtemp(prefix="iq_streak_")
DB_PATH = os.path.join(TMP, "qa.db")
PORT = 8796
BASE = f"http://127.0.0.1:{PORT}"
EMAIL = "qa@example.com"
BROKEN_EMAIL = "broken@example.com"
PASSWORD = "Qa-password-1"

os.environ["DATABASE_PATH"] = DB_PATH
os.environ["FLASK_ENV"] = "production"
os.environ["SECRET_KEY"] = "qa-streak-verification-secret"
os.environ.pop("GEMINI_API_KEY", None)

ANSWER = "Indexes trade write overhead for far faster lookups."


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

    # ---- register two users via HTTP ----
    hs = requests.Session()
    page = _csrf(hs, "/register")
    r = hs.post(f"{BASE}/register", data={
        "name": "QA User", "email": EMAIL, "password": PASSWORD,
        "confirm": PASSWORD, "csrf_token": page,
    }, allow_redirects=False)
    rec("student registers", r.status_code == 302, r.status_code)

    broken = requests.Session()
    page = _csrf(broken, "/register")
    r = broken.post(f"{BASE}/register", data={
        "name": "Broken History", "email": BROKEN_EMAIL, "password": PASSWORD,
        "confirm": PASSWORD, "csrf_token": page,
    }, allow_redirects=False)
    rec("broken-history user registers", r.status_code == 302, r.status_code)

    q_id = _db_value("SELECT id FROM users WHERE email=?", (EMAIL,))
    b_id = _db_value("SELECT id FROM users WHERE email=?", (BROKEN_EMAIL,))

    # Controlled history:
    #  - student: 5-day run ending today-8, then a gap, then 2 days (-2,-1)
    #    -> current 2, best 5 until today is completed, then current 3.
    #  - broken: 3-day run (-5,-4,-3), a miss on -1 and today (no -2 row)
    #    -> current 0, best 3.
    _seed_rows(q_id, [(-12, "completed"), (-11, "completed"), (-10, "completed"),
                      (-9, "completed"), (-8, "completed"),
                      (-2, "completed"), (-1, "completed")])
    _seed_rows(b_id, [(-5, "completed"), (-4, "completed"), (-3, "completed")])

    # ---- console/CSP watcher ----
    from playwright.sync_api import sync_playwright

    console_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        for cookie in hs.cookies:
            ctx.add_cookies([{
                "name": cookie.name, "value": cookie.value,
                "domain": "127.0.0.1", "path": "/",
            }])
        ctx.add_init_script(
            "try { localStorage.setItem('interviewiq-theme', 'light'); } catch (e) {}"
        )
        page = ctx.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text)
                if ("Content Security Policy" in msg.text or "Refused to" in msg.text)
                else None)

        # ---- dashboard streak tile (light) ----
        page.goto(f"{BASE}/dashboard", wait_until="load")
        body = page.locator("body").inner_text()
        tile = page.locator('.iq-kpi[data-icon="streak"]').inner_text()
        value = page.locator('.iq-kpi[data-icon="streak"] .iq-kpi-value').get_attribute("data-count")
        rec("dashboard streak tile shows current=2 and best=5 from seeded history",
            value == "2" and "2 days in a row" in tile
            and "best 5" in tile, f"value={value} | {tile.replace(chr(10), ' / ')}")
        rec("sidebar navigation unchanged (Dashboard + Today's Challenge)",
            page.locator(".sidebar a[href='/dashboard']").count() == 1
            and page.locator(".sidebar a[href='/challenge']").count() == 1)

        # ---- complete today's challenge via the real page ----
        page.goto(f"{BASE}/challenge", wait_until="load")
        rec("challenge page still renders an open question",
            page.locator("textarea#answer").count() == 1
            and page.locator(".ch-question-text").count() == 1)
        page.fill("textarea#answer", ANSWER)
        with page.expect_navigation():
            page.click(".ch-answer-form button[type=submit]")
        rec("submitting completes today's challenge",
            "Challenge completed" in page.locator("body").inner_text())

        page.goto(f"{BASE}/dashboard", wait_until="load")
        tile = page.locator('.iq-kpi[data-icon="streak"]').inner_text()
        value = page.locator('.iq-kpi[data-icon="streak"] .iq-kpi-value').get_attribute("data-count")
        rec("completing today advances the current streak to 3",
            value == "3" and "3 days in a row" in tile
            and "best 5" in tile, f"value={value} | {tile.replace(chr(10), ' / ')}")

        # same-day refresh must not inflate the streak or duplicate the row
        page.reload(wait_until="load")
        tile = page.locator('.iq-kpi[data-icon="streak"]').inner_text()
        value = page.locator('.iq-kpi[data-icon="streak"] .iq-kpi-value').get_attribute("data-count")
        t = _db_value("SELECT COUNT(*) FROM daily_challenges "
                      "WHERE user_id=? AND challenge_date=? AND status='completed'",
                      (q_id, _day(0)))
        rec("same-day refresh does not inflate streak (still 3, today=1 row)",
            value == "3" and "3 days in a row" in tile and "best 5" in tile
            and t == 1, f"value={value} today_rows={t}")

        # repeat POST on the already-completed day must stay idempotent
        token = _csrf(hs, "/challenge")
        r = hs.post(f"{BASE}/challenge/answer", data={
            "challenge_id": str(_db_value(
                "SELECT id FROM daily_challenges WHERE user_id=? AND challenge_date=?",
                (q_id, _day(0)))),
            "answer": "a second submission",
            "csrf_token": token,
        }, allow_redirects=False)
        t = _db_value("SELECT COUNT(*) FROM daily_challenges "
                      "WHERE user_id=? AND challenge_date=?",
                      (q_id, _day(0)))
        value = page.locator('.iq-kpi[data-icon="streak"] .iq-kpi-value').get_attribute("data-count")
        tile = page.locator('.iq-kpi[data-icon="streak"]').inner_text()
        rec("repeat submission on the same day keeps streak at 3 and one row",
            r.status_code == 302 and t == 1 and value == "3"
            and "3 days in a row" in tile,
            f"status={r.status_code} today_rows={t}")

        # ---- broken-history user (missed yesterday) ----
        b_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        for cookie in broken.cookies:
            b_ctx.add_cookies([{
                "name": cookie.name, "value": cookie.value,
                "domain": "127.0.0.1", "path": "/",
            }])
        b_page = b_ctx.new_page()
        b_page.goto(f"{BASE}/dashboard", wait_until="load")
        bvalue = b_page.locator('.iq-kpi[data-icon="streak"] .iq-kpi-value').get_attribute("data-count")
        btile = b_page.locator('.iq-kpi[data-icon="streak"]').inner_text()
        rec("broken history shows current 0 and the start CTA",
            bvalue == "0"
            and "Answer today's challenge to start a streak" in btile,
            f"value={bvalue} | {btile.replace(chr(10), ' / ')}")
        rec("broken user does not inherit the other user's streak numbers",
            "best 5" not in btile and "9 days" not in btile)
        b_ctx.close()

        # ---- CSP cleanliness on the dashboard streak surface ----
        page.goto(f"{BASE}/dashboard", wait_until="load")
        styles = page.locator('.iq-kpi[data-icon="streak"] [style]').count()
        scripts = page.locator('.iq-kpi[data-icon="streak"] script').count()
        inline_js = "javascript:" in page.content()
        rec("streak tile contains no inline styles/scripts or js: hrefs",
            styles == 0 and scripts == 0 and not inline_js,
            f"styles={styles} scripts={scripts}")
        rec("no CSP violations logged during the session", not console_errors,
            "; ".join(console_errors[:3]))

        # ---- dark theme ----
        dark_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        for cookie in hs.cookies:
            dark_ctx.add_cookies([{
                "name": cookie.name, "value": cookie.value,
                "domain": "127.0.0.1", "path": "/",
            }])
        dark_ctx.add_init_script(
            "try { localStorage.setItem('interviewiq-theme', 'dark'); } catch (e) {}"
        )
        dark_page = dark_ctx.new_page()
        dark_page.goto(f"{BASE}/dashboard", wait_until="load")
        bg = dark_page.evaluate("getComputedStyle(document.body).backgroundColor")
        dtile = dark_page.locator('.iq-kpi[data-icon="streak"]').inner_text()
        rec("dark theme renders the streak tile with current value",
            "3 days in a row" in dtile and "best 5" in dtile, bg)
        dark_ctx.close()

        # ---- responsive overflow on the dashboard ----
        page.goto(f"{BASE}/dashboard", wait_until="load")
        overflow = []
        for width in (1440, 1024, 768, 560, 360):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(250)
            overflow.append((width, page.evaluate(
                "document.documentElement.scrollWidth - window.innerWidth")))
        bad = [(w, v) for w, v in overflow if v > 0]
        rec("no horizontal overflow on dashboard at 1440/1024/768/560/360",
            not bad, " ".join(f"{w}px:+{v}" for w, v in overflow))

        # ---- challenge page still functional after completion ----
        page.goto(f"{BASE}/challenge", wait_until="load")
        rec("completed challenge page still shows the echo and no answer form",
            "Challenge completed" in page.locator("body").inner_text()
            and page.locator("textarea#answer").count() == 0
            and ANSWER in page.locator("body").inner_text())

        browser.close()

    failed = [c for c in checks if not c[1]]
    print("\n" + "=" * 60)
    print(f"Streak browser QA: {len(checks) - len(failed)}/{len(checks)} passed")
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


def _seed_rows(user_id, rows):
    conn = _db()
    conn.executemany(
        "INSERT INTO daily_challenges "
        "(user_id, challenge_date, question, question_type, expected_concepts, "
        "status) VALUES (?, ?, 'Seeded challenge question', 'conceptual', '[]', ?)",
        [(user_id, _day(offset), status) for offset, status in rows],
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    raise SystemExit(main())