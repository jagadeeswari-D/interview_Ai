"""Browser QA for Performance Comparison (Phase 8, Stage 4).

Pattern mirrors qa/browser_notes_check.py: the app is started in-process on
127.0.0.1, users are registered over real HTTP, comparable sessions are
seeded straight into SQLite, then Playwright drives the system Microsoft Edge
channel. Verifies: the History detail comparison renders the previous
comparable session and delta, dimension deltas appear, an unrelated but more
recent session is never selected, a first session shows the "no comparable
session" note, other users see nothing, CSP stays clean, both themes render,
five widths have no horizontal overflow, and History filters / Replay notes /
bookmarks still work.

Run:  py -3.14 qa/browser_comparison_check.py
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

TMP = tempfile.mkdtemp(prefix="iq_compare_")
DB_PATH = os.path.join(TMP, "qa.db")
PORT = 8794
BASE = f"http://127.0.0.1:{PORT}"
EMAIL = "qa@example.com"
PASSWORD = "Qa-password-1"
OTHER_EMAIL = "mallory@example.com"


def main():
    os.environ["DATABASE_PATH"] = DB_PATH
    os.environ["FLASK_ENV"] = "production"
    os.environ["SECRET_KEY"] = "qa-comparison-verification-secret"
    os.environ.pop("GEMINI_API_KEY", None)

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

    # ---- register users over HTTP ----
    hs = requests.Session()
    page = _csrf(hs, "/register")
    r = hs.post(f"{BASE}/register", data={
        "name": "QA User", "email": EMAIL, "password": PASSWORD,
        "confirm": PASSWORD, "csrf_token": page,
    }, allow_redirects=False)
    rec("comparison user registers", r.status_code == 302, r.status_code)

    other = requests.Session()
    page = _csrf(other, "/register")
    r = other.post(f"{BASE}/register", data={
        "name": "Mallory", "email": OTHER_EMAIL, "password": PASSWORD,
        "confirm": PASSWORD, "csrf_token": page,
    }, allow_redirects=False)
    rec("second user registers", r.status_code == 302, r.status_code)

    user_id = _db_value("SELECT id FROM users WHERE email=?", (EMAIL,))
    seed = _seed(user_id)
    first_id = seed["first"]
    second_id = seed["second"]

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

        # ---- comparison on the later comparable session ----
        page.goto(f"{BASE}/history/{second_id}", wait_until="load")
        body = page.locator("body").inner_text()
        rec("comparison panel renders on History detail",
            page.locator("section.compare-panel").count() == 1)
        rec("previous comparable score (40) is shown",
            page.locator(".compare-tile", has_text="Previous session").count() == 1
            and "40" in page.locator(".compare-tile", has_text="Previous session").inner_text())
        rec("this-session score (80) is shown",
            "80" in page.locator(".compare-tile", has_text="This session").inner_text())
        rec("overall delta +40 is shown",
            "+40" in page.locator(".compare-delta").first.inner_text(),
            page.locator(".compare-delta").first.inner_text())
        rec("dimension rows are rendered",
            page.locator(".compare-row").count() >= 5,
            page.locator(".compare-row").count())
        rec("unrelated, more recent session (95) is never selected",
            "95" not in body)

        # ---- first comparable session: clear empty state ----
        page.goto(f"{BASE}/history/{first_id}", wait_until="load")
        rec("first session shows the no-comparable-session note",
            page.locator("section.compare-panel").count() == 1
            and "No earlier completed session matches" in page.locator("section.compare-panel").inner_text())

        # ---- CSP cleanliness of the new panel ----
        page.goto(f"{BASE}/history/{second_id}", wait_until="load")
        styles = page.locator(".compare-panel [style]").count()
        scripts = page.locator(".compare-panel script").count()
        inline_js = "javascript:" in page.content()
        rec("comparison panel is CSP clean (no inline styles/scripts/js: hrefs)",
            styles == 0 and scripts == 0 and not inline_js,
            f"styles={styles} scripts={scripts}")
        rec("no CSP violations logged during session", not console_errors,
            "; ".join(console_errors[:3]))

        # ---- History filters preserved ----
        page.goto(f"{BASE}/history?q=SQL", wait_until="load")
        rec("history search still filters sessions",
            page.locator("li.history-item").count() == 2,
            page.locator("li.history-item").count())
        page.goto(f"{BASE}/history?difficulty=medium", wait_until="load")
        rec("history difficulty filter still works",
            page.locator("li.history-item").count() == 3,
            page.locator("li.history-item").count())

        # ---- Personal notes + bookmarks still render in Replay ----
        page.goto(f"{BASE}/replay/{second_id}?q=1", wait_until="load")
        rec("personal note box still present in Replay",
            page.locator("section.personal-note").count() == 1)
        rec("bookmark control still present in Replay",
            page.locator(".bookmark-toggle").count() == 1)

        # ---- second-user isolation ----
        other_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        for cookie in other.cookies:
            other_ctx.add_cookies([{
                "name": cookie.name, "value": cookie.value,
                "domain": "127.0.0.1", "path": "/",
            }])
        other_page = other_ctx.new_page()
        other_page.goto(f"{BASE}/history", wait_until="load")
        other_text = other_page.locator("body").inner_text()
        rec("second user's history shows none of the victim's sessions",
            "SQL joins" not in other_text and "+40" not in other_text)
        resp = other.get(f"{BASE}/history/{second_id}", allow_redirects=False)
        rec("second user cannot open the victim's History detail",
            resp.status_code == 404, f"status={resp.status_code}")
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
        dark_page.goto(f"{BASE}/history/{second_id}", wait_until="load")
        bg = dark_page.evaluate("getComputedStyle(document.body).backgroundColor")
        rec("dark theme renders the comparison panel",
            dark_page.locator("section.compare-panel").count() == 1
            and "+40" in dark_page.locator(".compare-delta").first.inner_text(), bg)
        dark_ctx.close()

        # ---- responsive overflow ----
        page.goto(f"{BASE}/history/{second_id}", wait_until="load")
        overflow = []
        for width in (1440, 1024, 768, 560, 360):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(250)
            overflow.append((width, page.evaluate(
                "document.documentElement.scrollWidth - window.innerWidth")))
        bad = [(w, v) for w, v in overflow if v > 0]
        rec("no horizontal overflow on comparison at 1440/1024/768/560/360",
            not bad, " ".join(f"{w}px:+{v}" for w, v in overflow))

        browser.close()

    failed = [c for c in checks if not c[1]]
    print("\n" + "=" * 60)
    print(f"Comparison browser QA: {len(checks) - len(failed)}/{len(checks)} passed")
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


def _seed(user_id):
    """Two comparable 'SQL joins' sessions + one unrelated, newer session."""
    scores_low = ('{"technical_accuracy":40,"relevance":40,"completeness":40,'
                  '"clarity":40,"communication":40}')
    scores_high = ('{"technical_accuracy":80,"relevance":80,"completeness":80,'
                   '"clarity":80,"communication":80}')
    rows = [
        (user_id, "practice", "Software Engineer", "medium", "SQL joins",
         "2026-05-01 09:00:00", 40, "completed", scores_low),
        (user_id, "practice", "Software Engineer", "medium", "SQL joins",
         "2026-05-08 09:00:00", 80, "completed", scores_high),
        (user_id, "practice", "Software Engineer", "medium", "Data structures",
         "2026-05-15 09:00:00", 95, "completed", scores_high),
    ]
    conn = _db()
    ids = {}
    for user, mode, role, diff, topic, date, overall, status, scores in rows:
        cur = conn.execute(
            "INSERT INTO interviews (user_id, mode, role, difficulty, type, "
            "date, overall_score, status) VALUES (?,?,?,?,?,?,?,?)",
            (user, mode, role, diff, topic, date, overall, status),
        )
        interview_id = cur.lastrowid
        if topic == "SQL joins":
            qid = conn.execute(
                "INSERT INTO questions (interview_id, question, question_type, "
                "sequence_order) VALUES (?, ?, 'conceptual', 1)",
                (interview_id, f"Explain {topic} ({overall})."),
            ).lastrowid
            conn.execute(
                "INSERT INTO answers (question_id, user_answer, score, scores, "
                "feedback) VALUES (?, 'Seeded answer.', ?, ?, 'Seeded feedback.')",
                (qid, float(overall), scores),
            )
    first_id = conn.execute(
        "SELECT id FROM interviews WHERE type='SQL joins' AND overall_score=40"
    ).fetchone()[0]
    second_id = conn.execute(
        "SELECT id FROM interviews WHERE type='SQL joins' AND overall_score=80"
    ).fetchone()[0]
    conn.commit()
    conn.close()
    ids["first"], ids["second"] = first_id, second_id
    return ids


if __name__ == "__main__":
    raise SystemExit(main())
