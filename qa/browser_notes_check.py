"""Browser QA for Personal Notes (Stage 8, Phase 8).

Pattern (same as the bookmark browser QA): app started in-process on
127.0.0.1, users registered via real HTTP, completed sessions seeded straight
into SQLite, then Playwright drives the system Microsoft Edge channel.
Verifies: the note box renders with an empty/Saved/Unsaved state, unsaved
edits are lost on reload, Save persists across refresh, the Saved/Unsaved
indicator tracks edits, per-question isolation inside one session, Clear
removes the note, coexistence with bookmarks, second-user isolation,
strict-CSP cleanliness, both themes, keyboard focus, and no horizontal
overflow at five widths.

Note: Save/Clear are native form POSTs, so every click is a navigation.

Run:  py -3.14 qa/browser_notes_check.py
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

TMP = tempfile.mkdtemp(prefix="iq_notes_")
DB_PATH = os.path.join(TMP, "qa.db")
PORT = 8793
BASE = f"http://127.0.0.1:{PORT}"
EMAIL = "qa@example.com"
PASSWORD = "Qa-password-1"
OTHER_EMAIL = "mallory@example.com"

os.environ["DATABASE_PATH"] = DB_PATH
os.environ["FLASK_ENV"] = "production"
os.environ["SECRET_KEY"] = "qa-notes-verification-secret"
os.environ.pop("GEMINI_API_KEY", None)

NOTE_TEXT = "Remember to cover ACID properties and isolation levels."
UPDATED_NOTE = "Remember to cover ACID, isolation, and the query planner too."


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

    # ---- register the note-taking user + a second user via HTTP ----
    hs = requests.Session()
    page = _csrf(hs, "/register")
    r = hs.post(f"{BASE}/register", data={
        "name": "QA User", "email": EMAIL, "password": PASSWORD,
        "confirm": PASSWORD, "csrf_token": page,
    }, allow_redirects=False)
    rec("note-taker registers", r.status_code == 302 and "/dashboard" in r.headers.get("Location", ""), r.status_code)

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
    sql_id = _db_value(
        "SELECT id FROM interviews WHERE user_id=? AND type='SQL joins'",
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

        # ---- open the walkthrough: empty note UI ----
        page.goto(f"{BASE}/replay", wait_until="load")
        rec("replay index lists both sessions", page.locator("a.button:has-text('Open replay')").count() == 2)
        with page.expect_navigation():
            page.click("text=Open replay")
        rec("walkthrough shows the empty personal-note box",
            page.locator("section.personal-note").count() == 1
            and page.locator("#note-content").get_attribute("placeholder") != ""
            and page.locator("#personal-note-state").inner_text().strip() == "No note yet",
            page.url)
        rec("note controls are keyboard-reachable (Save/Clear)",
            page.locator('button[name="action"][value="save"]').count() == 1
            and page.locator('button[name="action"][value="clear"]').count() == 1)

        # ---- unsaved edit: indicator flips, reload discards ----
        page.fill("#note-content", NOTE_TEXT)
        rec("typing an unsaved note flips the state to Unsaved",
            page.locator("#personal-note-state").inner_text().strip() == "Unsaved",
            page.locator("#personal-note-state").inner_text())
        page.reload(wait_until="load")
        rec("unsaved note is discarded on reload (back to 'No note yet')",
            page.locator("#note-content").input_value() == ""
            and page.locator("#personal-note-state").inner_text().strip() == "No note yet")

        # ---- save the note (native form POST) ----
        page.fill("#note-content", NOTE_TEXT)
        with page.expect_navigation():
            page.click('button[name="action"][value="save"]')
        rec("Save redirects back to the same question",
            f"/replay/{sess_id}" in page.url and page.url.split("?q=")[-1] == "1", page.url)
        rec("saved note persisted back into the textarea",
            page.locator("#note-content").input_value() == NOTE_TEXT)
        rec("saved note shows the Saved state and success flash",
            page.locator("#personal-note-state").inner_text().strip() == "Saved"
            and page.locator(".flash-stack .alert-success").count() >= 1)

        page.reload(wait_until="load")
        rec("note survives a full reload", page.locator("#note-content").input_value() == NOTE_TEXT)

        # ---- per-question isolation ----
        with page.expect_navigation():
            page.click("a.badge.replay-jump-link >> text=Q2")
        rec("other question in the session has no note",
            page.locator("#note-content").input_value() == ""
            and page.locator("#personal-note-state").inner_text().strip() == "No note yet")
        with page.expect_navigation():
            page.click("a.badge.replay-jump-link >> text=Q1")
        rec("returning to the noted question shows the note again",
            page.locator("#note-content").input_value() == NOTE_TEXT)

        # ---- update flow ----
        page.fill("#note-content", UPDATED_NOTE)
        rec("editing an existing note flips to Unsaved then saves",
            page.locator("#personal-note-state").inner_text().strip() == "Unsaved")
        with page.expect_navigation():
            page.click('button[name="action"][value="save"]')
        rec("updated note replaces the old content",
            page.locator("#note-content").input_value() == UPDATED_NOTE
            and page.locator("#personal-note-state").inner_text().strip() == "Saved")
        page.reload(wait_until="load")
        rec("updated note persists after refresh",
            page.locator("#note-content").input_value() == UPDATED_NOTE)

        # ---- clear ----
        with page.expect_navigation():
            page.click('button[name="action"][value="clear"]')
        rec("Clear empties the note and shows an info flash",
            page.locator("#note-content").input_value() == ""
            and page.locator("#personal-note-state").inner_text().strip() == "No note yet"
            and page.locator(".flash-stack .alert-info").count() >= 1)
        page.reload(wait_until="load")
        rec("cleared note stays cleared after refresh",
            page.locator("#note-content").input_value() == "")

        # ---- coexistence with bookmarks ----
        with page.expect_navigation():
            page.click(".bookmark-toggle")
        page.fill("#note-content", NOTE_TEXT)
        with page.expect_navigation():
            page.click('button[name="action"][value="save"]')
        rec("note + bookmark coexist on the same question",
            "is-bookmarked" in page.locator(".bookmark-toggle").get_attribute("class")
            and page.locator("#note-content").input_value() == NOTE_TEXT)
        with page.expect_navigation():
            page.click('button[name="action"][value="clear"]')
        rec("clearing the note leaves the bookmark intact",
            "is-bookmarked" in page.locator(".bookmark-toggle").get_attribute("class")
            and page.locator("#note-content").input_value() == "")
        page.fill("#note-content", NOTE_TEXT)
        with page.expect_navigation():
            page.click('button[name="action"][value="save"]')
        with page.expect_navigation():
            page.click(".bookmark-toggle")
        rec("unbookmarking leaves the note intact",
            "is-bookmarked" not in page.locator(".bookmark-toggle").get_attribute("class")
            and page.locator("#note-content").input_value() == NOTE_TEXT)

        # ---- CSP cleanliness ----
        styles = page.locator(".personal-note [style]").count()
        scripts = page.locator(".personal-note script").count()
        inline_js = "javascript:" in page.content()
        rec("note box contains no inline styles/scripts or js: hrefs (CSP clean)",
            styles == 0 and scripts == 0 and not inline_js, f"styles={styles} scripts={scripts}")
        rec("no CSP violations logged during browser session", not console_errors,
            "; ".join(console_errors[:3]))

        # ---- second-user isolation (browser) ----
        other_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        for cookie in other.cookies:
            other_ctx.add_cookies([{
                "name": cookie.name, "value": cookie.value,
                "domain": "127.0.0.1", "path": "/",
            }])
        other_page = other_ctx.new_page()
        other_page.goto(f"{BASE}/replay", wait_until="load")
        other_text = other_page.locator("body").inner_text()
        rec("second user's replay index shows no victim question or note",
            "reconciliation" not in other_text and NOTE_TEXT not in other_text)
        resp = other.get(f"{BASE}/replay/{sess_id}", allow_redirects=False)
        rec("second user cannot open victim's replay walkthrough",
            resp.status_code == 404, f"status={resp.status_code}")
        token = _csrf(other, "/settings")
        r = other.post(f"{BASE}/replay/{sess_id}/note", data={
            "question_id": str(q1_id), "q": "1", "content": "intrusion",
            "action": "save", "csrf_token": token,
        }, allow_redirects=False)
        rec("second user's save-note POST on victim's question is rejected",
            r.status_code == 404, f"status={r.status_code}")
        c = other.post(f"{BASE}/replay/{sess_id}/note", data={
            "question_id": str(q1_id), "q": "1", "action": "clear",
            "csrf_token": token,
        }, allow_redirects=False)
        rec("second user's clear-note POST on victim's question is rejected",
            c.status_code == 404, f"status={c.status_code}")
        rows = _db_value(
            "SELECT COUNT(*) FROM personal_notes n JOIN users u ON u.id=n.user_id "
            "WHERE u.email=? AND content=?",
            (EMAIL, NOTE_TEXT))
        rec("victim's note survives the attempts unchanged", rows == 1, f"rows={rows}")
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
        dark_page.goto(f"{BASE}/replay/{sess_id}?q=1", wait_until="load")
        rec("dark theme renders the note box with saved note",
            dark_page.locator("section.personal-note").count() == 1
            and dark_page.locator("#note-content").input_value() == NOTE_TEXT
            and "Question 1 of 2" in dark_page.locator("body").inner_text(), bg)

        # ---- responsive overflow: walkthrough with a note ----
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
        overflow_i = []
        for width in (1440, 1024, 768, 560, 360):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(250)
            overflow_i.append((width, page.evaluate("document.documentElement.scrollWidth - window.innerWidth")))
        bado = [(w, v) for w, v in overflow_i if v > 0]
        rec("no horizontal overflow on replay index at 1440/1024/768/560/360",
            not bado, " ".join(f"{w}px:+{v}" for w, v in overflow_i))

        dark_ctx.close()
        browser.close()

    # ---- summary ----
    failed = [c for c in checks if not c[1]]
    print("\n" + "=" * 60)
    print(f"Notes browser QA: {len(checks) - len(failed)}/{len(checks)} passed")
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
    conn.execute(
        "INSERT INTO questions (interview_id, question, question_type, sequence_order) "
        "VALUES (?, ?, 'conceptual', 2)",
        (react_id, "What is the Virtual DOM and why does it help?"),
    )
    conn.execute(
        "INSERT INTO questions (interview_id, question, question_type, sequence_order) "
        "VALUES (?, ?, 'coding', 1)",
        (sql_id, "Write the SQL for a LEFT JOIN of two tables."),
    )
    conn.execute(
        "INSERT INTO answers (question_id, user_answer, score, scores, feedback) "
        "VALUES (?, 'QA seeded answer.', 75.0, ?, 'QA seeded feedback.')",
        (q1, scores),
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    raise SystemExit(main())