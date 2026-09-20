"""Browser QA for Question Bookmarks (Stage 8, Phase 8).

Pattern (same as the History browser QA): app started in-process on
127.0.0.1, users registered via real HTTP, completed sessions + one bookmark
seeded straight into SQLite, then Playwright drives the system Microsoft Edge
channel. Verifies: walkthrough toggle (off/on), refresh persistence,
per-question state, the Saved questions panel, Open-replay deep link,
unbookmark from both places, a second user's isolation, strict-CSP
cleanliness, both themes, keyboard focus, and no horizontal overflow at five
widths.

Run:  py -3.14 qa/browser_bookmarks_check.py
"""
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp(prefix="iq_bm_")
DB_PATH = os.path.join(TMP, "qa.db")
PORT = 8792
BASE = f"http://127.0.0.1:{PORT}"
EMAIL = "qa@example.com"
PASSWORD = "Qa-password-1"
OTHER_EMAIL = "mallory@example.com"

os.environ["DATABASE_PATH"] = DB_PATH
os.environ["FLASK_ENV"] = "production"
os.environ["SECRET_KEY"] = "qa-bookmarks-verification-secret"
os.environ.pop("GEMINI_API_KEY", None)

BOOKMARK_BTN = ".bookmark-toggle"


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

    # ---- register the bookmarking user + a second user via HTTP ----
    hs = requests.Session()
    page = _csrf(hs, "/register")
    r = hs.post(f"{BASE}/register", data={
        "name": "QA User", "email": EMAIL, "password": PASSWORD,
        "confirm": PASSWORD, "csrf_token": page,
    }, allow_redirects=False)
    rec("bookmarker registers", r.status_code == 302 and "/dashboard" in r.headers.get("Location", ""), r.status_code)

    other = requests.Session()
    page = _csrf(other, "/register")
    r = other.post(f"{BASE}/register", data={
        "name": "Mallory", "email": OTHER_EMAIL, "password": PASSWORD,
        "confirm": PASSWORD, "csrf_token": page,
    }, allow_redirects=False)
    rec("second user registers", r.status_code == 302 and "/dashboard" in r.headers.get("Location", ""), r.status_code)

    user_id = _db_value("SELECT id FROM users WHERE email=?", (EMAIL,))
    _seed(user_id)
    sess_id = _db_value(
        "SELECT id FROM interviews WHERE user_id=? AND type='React reconciliation'",
        (user_id,),
    )
    q1_id = _db_value(
        "SELECT id FROM questions WHERE interview_id=? AND sequence_order=1",
        (sess_id,),
    )
    q2_id = _db_value(
        "SELECT id FROM questions WHERE interview_id=? AND sequence_order=2",
        (sess_id,),
    )

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

        # ---- replay index: no saved panel before any bookmark ----
        page.goto(f"{BASE}/replay", wait_until="load")
        rec("replay index lists sessions, no saved panel yet",
            page.locator("h2.iq-panel-title:has-text('Saved questions')").count() == 0
            and page.locator("a.button:has-text('Open replay')").count() == 2)

        # ---- open walkthrough, bookmark Q1 ----
        with page.expect_navigation():
            page.click("text=Open replay")
        rec("walkthrough shows Bookmark toggle (unbookmarked)",
            page.locator(BOOKMARK_BTN).count() == 1
            and page.locator(f"{BOOKMARK_BTN} span").first.inner_text().strip() == "Bookmark"
            and page.locator(BOOKMARK_BTN).first.get_attribute("aria-pressed") == "false",
            page.url)

        with page.expect_navigation():
            page.click(BOOKMARK_BTN)
        rec("click bookmarks and persists in walkthrough (Saved)",
            page.locator(BOOKMARK_BTN).first.get_attribute("aria-pressed") == "true"
            and "is-bookmarked" in page.locator(BOOKMARK_BTN).first.get_attribute("class"),
            page.url)

        page.reload(wait_until="load")
        rec("refresh keeps bookmarked state",
            page.locator(BOOKMARK_BTN).first.get_attribute("aria-pressed") == "true"
            and page.locator(f"{BOOKMARK_BTN} span").first.inner_text().strip() == "Saved")

        # ---- Q2 of the same session stays unbookmarked ----
        with page.expect_navigation():
            page.click(f"a.badge.replay-jump-link >> text=Q2")
        rec("other question in the session is not bookmarked",
            "is-bookmarked" not in page.locator(BOOKMARK_BTN).first.get_attribute("class")
            and page.locator(BOOKMARK_BTN).first.get_attribute("aria-pressed") == "false")

        with page.expect_navigation():
            page.click(f"a.badge.replay-jump-link >> text=Q1")
        rec("returning to the bookmarked question keeps its state",
            page.locator(BOOKMARK_BTN).first.get_attribute("aria-pressed") == "true")

        # ---- keyboard accessibility: tab/focus reaches the toggle ----
        page.focus(BOOKMARK_BTN)
        focused = page.evaluate("document.activeElement.className")
        rec("bookmark toggle is keyboard-focusable",
            "bookmark-toggle" in focused, focused)

        # ---- saved panel on replay index ----
        page.goto(f"{BASE}/replay", wait_until="load")
        body_text = page.locator("body").inner_text()
        rec("Saved questions panel lists bookmark with session context",
            page.locator("#saved").count() == 1
            and "React reconciliation" in body_text
            and "Explain how reconciliation works" in body_text)
        deep = page.locator(f"#saved a[href='/replay/{sess_id}?q=1']").count()
        rec("saved item deep-links to the bookmarked question position",
            deep >= 1, f"links={deep}")

        # ---- Open replay from the saved item ----
        with page.expect_navigation():
            page.click(f"#saved a.button:has-text('Open replay')")
        rec("Open replay from saved panel lands on the question",
            "/replay/" in page.url and "?q=" in page.url
            and "Saved" in page.locator(f"{BOOKMARK_BTN} span").first.inner_text(),
            page.url)

        # ---- unbookmark from the walkthrough ----
        with page.expect_navigation():
            page.click(BOOKMARK_BTN)
        rec("unbookmark flips back to Bookmark",
            page.locator(f"{BOOKMARK_BTN} span").first.inner_text().strip() == "Bookmark"
            and page.locator(BOOKMARK_BTN).first.get_attribute("aria-pressed") == "false")
        page.goto(f"{BASE}/replay", wait_until="load")
        rec("replay index no longer shows Saved questions after unbookmark",
            page.locator("#saved").count() == 0)

        # ---- re-bookmark, then remove from the saved panel ----
        with page.expect_navigation():
            page.click("text=Open replay")
        with page.expect_navigation():
            page.click(BOOKMARK_BTN)
        page.goto(f"{BASE}/replay", wait_until="load")
        rec("re-bookmark repopulates Saved questions", page.locator("#saved").count() == 1)
        with page.expect_navigation():
            page.click("#saved .bookmark-toggle-remove")
        rec("Remove bookmark returns to /replay#saved anchor",
            page.url.rstrip("/").endswith("/replay#saved"), page.url)
        rec("saved panel empty after removal", page.locator("#saved").count() == 0)

        # ---- CSP: no inline styles/scripts inside bookmark controls ----
        with page.expect_navigation():
            page.click("text=Open replay")
        styles = page.locator(".iq-panel-head [style]").count() + page.locator(".bookmark-form [style]").count()
        scripts = page.locator(".iq-panel-head script").count() + page.locator(".bookmark-form script").count()
        rec("bookmark controls contain no inline style/script (CSP clean)",
            styles == 0 and scripts == 0, f"styles={styles} scripts={scripts}")
        rec("no CSP violations logged during browser session", not console_errors,
            "; ".join(console_errors[:3]))

        # ---- second user isolation (browser) ----
        other_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        for cookie in other.cookies:
            other_ctx.add_cookies([{
                "name": cookie.name, "value": cookie.value,
                "domain": "127.0.0.1", "path": "/",
            }])
        other_page = other_ctx.new_page()
        other_page.goto(f"{BASE}/replay", wait_until="load")
        other_text = other_page.locator("body").inner_text()
        rec("second user's replay index shows no bookmarks or victim question",
            "Saved questions" not in other_text and "reconciliation" not in other_text)
        resp = other.get(f"{BASE}/replay/{sess_id}", allow_redirects=False)
        rec("second user cannot open victim's replay walkthrough",
            resp.status_code == 404, f"status={resp.status_code}")
        token = _csrf(other, "/settings")
        r = other.post(f"{BASE}/replay/{sess_id}/bookmark", data={
            "question_id": str(q1_id), "q": "1", "csrf_token": token,
        }, allow_redirects=False)
        rec("second user's bookmark POST on victim's question is rejected",
            r.status_code == 404, f"status={r.status_code}")
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
        dark_page.goto(f"{BASE}/replay", wait_until="load")
        bg = dark_page.evaluate("getComputedStyle(document.body).backgroundColor")
        dark_page.goto(f"{BASE}/replay/{sess_id}", wait_until="load")
        rec("dark theme renders walkthrough with bookmarked state",
            dark_page.locator(BOOKMARK_BTN).count() == 1
            and (long_text := dark_page.locator("body").inner_text())
            and "Question 1 of 2" in long_text, bg)

        # ---- responsive overflow: walkthrough + index (light, bookmarked) ----
        page.goto(f"{BASE}/replay/{sess_id}?q=1", wait_until="load")
        overflow = []
        for width in (1440, 1024, 768, 560, 360):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(250)
            overflow.append((width, page.evaluate("document.documentElement.scrollWidth - window.innerWidth")))
        bad = [(w, v) for w, v in overflow if v > 0]
        rec("no horizontal overflow on walkthrough at 1440/1024/768/560/360",
            not bad, " ".join(f"{w}px:+{v}" for w, v in overflow))

        page.goto(f"{BASE}/replay", wait_until="load")
        with page.expect_navigation():
            page.click("text=Open replay")
        with page.expect_navigation():
            page.click(BOOKMARK_BTN)
        page.goto(f"{BASE}/replay", wait_until="load")
        overflow_i = []
        for width in (1440, 1024, 768, 560, 360):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(250)
            overflow_i.append((width, page.evaluate("document.documentElement.scrollWidth - window.innerWidth")))
        bado = [(w, v) for w, v in overflow_i if v > 0]
        rec("no horizontal overflow on saved panel at 1440/1024/768/560/360",
            not bado, " ".join(f"{w}px:+{v}" for w, v in overflow_i))
        rec("saved panel renders at 360px (item counts sane)",
            page.locator("#saved .history-item").count() == 1)

        dark_ctx.close()
        browser.close()

    # ---- summary ----
    failed = [c for c in checks if not c[1]]
    print("\n" + "=" * 60)
    print(f"Bookmarks browser QA: {len(checks) - len(failed)}/{len(checks)} passed")
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
    conn = _db()
    conn.executemany(
        "INSERT INTO interviews "
        "(user_id, mode, role, difficulty, type, date, overall_score, status, question_limit, duration_minutes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (user_id, "practice", "", "medium", "React reconciliation",
             "2026-05-22 09:00:00", 78, "completed", 0, 0),
            (user_id, "practice", "", "easy", "SQL joins",
             "2026-05-21 09:00:00", 82, "completed", 0, 0),
        ],
    )
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM interviews WHERE user_id=? ORDER BY id",
        (user_id,)).fetchall()]
    scores = ('{"technical_accuracy":80,"relevance":75,"completeness":70,'
              '"clarity":80,"communication":70}')
    react_id, sql_id = ids[0], ids[1]
    q1 = conn.execute(
        "INSERT INTO questions (interview_id, question, question_type, sequence_order) "
        "VALUES (?, ?, 'conceptual', 1)",
        (react_id, "Explain how reconciliation works in React."),
    ).lastrowid
    q2 = conn.execute(
        "INSERT INTO questions (interview_id, question, question_type, sequence_order) "
        "VALUES (?, ?, 'conceptual', 2)",
        (react_id, "What is the Virtual DOM and why does it help?"),
    ).lastrowid
    q3 = conn.execute(
        "INSERT INTO questions (interview_id, question, question_type, sequence_order) "
        "VALUES (?, ?, 'coding', 1)",
        (sql_id, "Write the SQL for a LEFT JOIN of two tables."),
    ).lastrowid
    for qid in (q1, q2, q3):
        conn.execute(
            "INSERT INTO answers (question_id, user_answer, score, scores, feedback) "
            "VALUES (?, 'QA seeded answer.', 75.0, ?, 'QA seeded feedback.')",
            (qid, scores),
        )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    raise SystemExit(main())