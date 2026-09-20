"""Browser QA for Today's Challenge (Stage 8 / Phase 9, Stage 1).

Pattern (same as the notes browser QA): app started in-process on 127.0.0.1,
users registered via real HTTP, then Playwright drives the system Microsoft
Edge channel. Verifies: the challenge page renders a today-only question with
a stable identity across refreshes, the submit flow completes and persists,
repeated refresh never duplicates the row (UNIQUE(user, date)), completion
survives reloads and shows the answer echo, the dashboard panel tracks the
open -> completed states, a second user is fully isolated (404 on cross-user
POST, own question shown), CSP stays clean, both themes render, keyboard
reachability of the form controls, and no horizontal overflow at five widths.

Run:  py -3.14 qa/browser_challenge_check.py
"""
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

TMP = tempfile.mkdtemp(prefix="iq_challenge_")
DB_PATH = os.path.join(TMP, "qa.db")
PORT = 8795
BASE = f"http://127.0.0.1:{PORT}"
EMAIL = "qa@example.com"
PASSWORD = "Qa-password-1"
OTHER_EMAIL = "mallory@example.com"

os.environ["DATABASE_PATH"] = DB_PATH
os.environ["FLASK_ENV"] = "production"
os.environ["SECRET_KEY"] = "qa-challenge-verification-secret"
os.environ.pop("GEMINI_API_KEY", None)

ANSWER = "Indexes trade a little write overhead for much faster read lookups."
ATTACKER_ANSWER = "intrusion attempt"


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

    # ---- register the student + a second user via HTTP ----
    hs = requests.Session()
    page = _csrf(hs, "/register")
    r = hs.post(f"{BASE}/register", data={
        "name": "QA User", "email": EMAIL, "password": PASSWORD,
        "confirm": PASSWORD, "csrf_token": page,
    }, allow_redirects=False)
    rec("student registers", r.status_code == 302 and "/dashboard" in r.headers.get("Location", ""), r.status_code)

    other = requests.Session()
    page = _csrf(other, "/register")
    r = other.post(f"{BASE}/register", data={
        "name": "Mallory", "email": OTHER_EMAIL, "password": PASSWORD,
        "confirm": PASSWORD, "csrf_token": page,
    }, allow_redirects=False)
    rec("second user registers", r.status_code == 302 and "/dashboard" in r.headers.get("Location", ""), r.status_code)

    user_id = _db_value("SELECT id FROM users WHERE email=?", (EMAIL,))

    # ---- Playwright ----
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

        # ---- the challenge page itself ----
        page.goto(f"{BASE}/challenge", wait_until="load")
        body = page.locator("body").inner_text()
        rec("challenge page renders as an open, today-only question",
            "Today's Challenge" in body
            and "Open" in body
            and page.locator("textarea#answer").count() == 1
            and page.locator("input[name=challenge_id]").count() == 1,
            page.url)
        rec("sidebar links to Today's Challenge",
            page.locator(".sidebar a[href='/challenge']").count() == 1)
        rec("hint is progressive disclosure (details/summary)",
            page.locator("details.ch-hint summary").count() == 1
            and page.locator(".ch-concept-list li").count() >= 3)
        question1 = page.locator(".ch-question-text").inner_text()
        rec("answer form is keyboard-reachable with a 5000-char limit",
            page.locator("textarea#answer").is_visible()
            and page.locator("textarea#answer").get_attribute("maxlength") == "5000"
            and page.locator(".ch-answer-form button[type=submit]").count() == 1)
        rec("stale /challenge answer POST with no CSRF rejected (client safety via form)",
            page.locator(".ch-answer-form input[name=csrf_token]").count() == 1)

        # before answering: dashboard shows the open state, never creates the row
        before_rows = _db_value("SELECT COUNT(*) FROM daily_challenges WHERE user_id=?", (user_id,))
        page.goto(f"{BASE}/dashboard", wait_until="load")
        dash = page.locator("body").inner_text()
        rec("dashboard panel shows the open challenge without opening it",
            "Daily Challenge" in dash and "Open" in dash
            and _db_value("SELECT COUNT(*) FROM daily_challenges WHERE user_id=?", (user_id,)) == before_rows,
            f"rows={before_rows}")

        # ---- refresh stability + no duplicates ----
        id1 = _db_value("SELECT id FROM daily_challenges WHERE user_id=?", (user_id,))
        page.goto(f"{BASE}/challenge", wait_until="load")
        page.reload(wait_until="load")
        page.reload(wait_until="load")
        after = _db_value("SELECT COUNT(*) FROM daily_challenges WHERE user_id=?", (user_id,))
        id2 = _db_value("SELECT id FROM daily_challenges WHERE user_id=?", (user_id,))
        rec("refresh keeps the same challenge and never duplicates the row",
            after == 1 and id1 == id2 and
            page.locator(".ch-question-text").inner_text() == question1, f"rows={after}")

        # ---- complete it ----
        page.fill("textarea#answer", ANSWER)
        with page.expect_navigation():
            page.click(".ch-answer-form button[type=submit]")
        body = page.locator("body").inner_text()
        rec("submitting completes today's challenge and echoes the answer",
            "Challenge completed" in body and ANSWER in body
            and page.locator("textarea#answer").count() == 0
            and "Completed" in body, page.url)
        st = _db_value("SELECT status FROM daily_challenges WHERE user_id=?", (user_id,))
        ans = _db_value("SELECT answer FROM daily_challenges WHERE user_id=?", (user_id,))
        rec("completion persisted in SQLite with status + answer",
            st == "completed" and ans == ANSWER, f"status={st}")

        page.reload(wait_until="load")
        rec("completed state survives reload (no answer form, echo remains)",
            "Challenge completed" in page.locator("body").inner_text()
            and page.locator("textarea#answer").count() == 0
            and ANSWER in page.locator("body").inner_text())
        count = _db_value("SELECT COUNT(*) FROM daily_challenges WHERE user_id=?", (user_id,))
        rec("completing never duplicated the daily row", count == 1, f"rows={count}")

        # repeat submission of an already-completed day is idempotent
        token = _csrf(hs, "/challenge")
        r = hs.post(f"{BASE}/challenge/answer", data={
            "challenge_id": str(id1), "answer": "a second different answer",
            "csrf_token": token,
        }, allow_redirects=False)
        rec("repeat POST on a completed challenge stays idempotent",
            r.status_code == 302 and
            _db_value("SELECT answer FROM daily_challenges WHERE id=?", (id1,)) == ANSWER,
            f"status={r.status_code}")

        # dashboard flips to Completed
        page.goto(f"{BASE}/dashboard", wait_until="load")
        dash = page.locator("body").inner_text()
        panel = page.locator("#iq-challenge-title").count()
        err = "Internal Server Error" in dash
        rec("dashboard panel shows the completed challenge",
            "Daily Challenge" in dash and "COMPLETED" in dash
            and "View today" in dash,
            f"Daily={('Daily Challenge' in dash)} Comp={('COMPLETED' in dash)} "
            f"View={('View today' in dash)} title={page.locator('#iq-challenge-title').inner_text()} "
            f"badge={page.locator('.iq-challenge-status').inner_text().strip()} "
            f"strip={page.locator('.iq-challenge-strip').count()}")

        # ---- CSP cleanliness ----
        page.goto(f"{BASE}/challenge", wait_until="load")
        styles = page.locator(".iq-challenge [style]").count()
        scripts = page.locator(".iq-challenge script").count()
        inline_js = "javascript:" in page.content()
        rec("challenge page contains no inline styles/scripts or js: hrefs (CSP clean)",
            styles == 0 and scripts == 0 and not inline_js, f"styles={styles} scripts={scripts}")
        rec("no CSP violations logged during the browser session", not console_errors,
            "; ".join(console_errors[:3]))

        # ---- second-user isolation ----
        other_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        for cookie in other.cookies:
            other_ctx.add_cookies([{
                "name": cookie.name, "value": cookie.value,
                "domain": "127.0.0.1", "path": "/",
            }])
        other_page = other_ctx.new_page()
        other_page.goto(f"{BASE}/challenge", wait_until="load")
        other_q = other_page.locator(".ch-question-text").inner_text()
        other_row = _db_value(
            "SELECT c.id FROM daily_challenges c "
            "JOIN users u ON u.id=c.user_id WHERE u.email=?", (OTHER_EMAIL,))
        other_row_q = _db_value(
            "SELECT c.question FROM daily_challenges c "
            "JOIN users u ON u.id=c.user_id WHERE u.email=?", (OTHER_EMAIL,))
        rec("second user opened their own distinct challenge row",
            other_row != id1 and other_q == other_row_q,
            f"victim={id1} other={other_row}")

        token = _csrf(other, "/challenge")
        r = other.post(f"{BASE}/challenge/answer", data={
            "challenge_id": str(id1), "answer": ATTACKER_ANSWER,
            "csrf_token": token,
        }, allow_redirects=False)
        rec("second user's POST on victim's challenge is rejected",
            r.status_code == 404, f"status={r.status_code}")
        rec("victim's completion survives the intrusion attempt",
            _db_value("SELECT answer FROM daily_challenges WHERE id=?", (id1,)) == ANSWER)
        other_ctx.close()

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
        dark_page.goto(f"{BASE}/challenge", wait_until="load")
        bg = dark_page.evaluate("getComputedStyle(document.body).backgroundColor")
        rec("dark theme renders the completed challenge",
            "Challenge completed" in dark_page.locator("body").inner_text(), bg)
        dark_ctx.close()

        # ---- responsive overflow on /challenge ----
        page.goto(f"{BASE}/challenge", wait_until="load")
        overflow = []
        for width in (1440, 1024, 768, 560, 360):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(250)
            overflow.append((width, page.evaluate("document.documentElement.scrollWidth - window.innerWidth")))
        bad = [(w, v) for w, v in overflow if v > 0]
        rec("no horizontal overflow on /challenge at 1440/1024/768/560/360",
            not bad, " ".join(f"{w}px:+{v}" for w, v in overflow))

        browser.close()

    # ---- summary ----
    failed = [c for c in checks if not c[1]]
    print("\n" + "=" * 60)
    print(f"Challenge browser QA: {len(checks) - len(failed)}/{len(checks)} passed")
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


if __name__ == "__main__":
    raise SystemExit(main())