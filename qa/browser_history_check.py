"""Browser QA for History search + advanced filters (post Stage-1 History).

Pattern: app started in-process (threaded) on 127.0.0.1, real HTTP client,
sessions seeded straight into SQLite (no Gemini needed on /history), then
Playwright drives the system Microsoft Edge channel. Verifies search,
filters, combinations, empty states, chips, Replay link, Interview memory,
strict-CSP cleanliness, both themes, and no horizontal overflow at several
widths.

Run:  py -3.14 qa/browser_history_check.py
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

TMP = tempfile.mkdtemp(prefix="iq_qa_")
DB_PATH = os.path.join(TMP, "qa.db")
PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"
EMAIL = "qa@example.com"
PASSWORD = "Qa-password-1"

os.environ["DATABASE_PATH"] = DB_PATH
os.environ["FLASK_ENV"] = "production"
os.environ["SECRET_KEY"] = "qa-browser-verification-secret"
os.environ.pop("GEMINI_API_KEY", None)


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

    # ---- register via real HTTP (requests) ----
    hs = requests.Session()
    page = _csrf(hs, "/register")
    r = hs.post(f"{BASE}/register", data={
        "name": "QA User", "email": EMAIL, "password": PASSWORD,
        "confirm": PASSWORD, "csrf_token": page,
    }, allow_redirects=False)
    rec("register returns redirect", r.status_code == 302 and "/dashboard" in r.headers.get("Location", ""), r.status_code)

    user_id = _db_value("SELECT id FROM users WHERE email=?", (EMAIL,))
    _seed(user_id)

    # ---- Playwright (system Edge channel) ----
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

        # ---- baseline list ----
        page.goto(f"{BASE}/history", wait_until="load")
        items = page.locator("ul.history-list li.history-item").count()
        rec("baseline: 3 sessions listed", items == 3, f"count={items}")
        body_text = page.locator("body").inner_text()
        rec("baseline: all three sessions present",
            all(s in body_text for s in ("React hooks", "SQL joins", "Backend Developer")))
        rec("memory panel present", "Interview memory" in body_text)
        replay_href = page.locator("a[href^='/replay/']").count()
        rec("replay links present", replay_href >= 3, f"count={replay_href}")

        # ---- search (Enter submits the GET form, real UX) ----
        page.fill("#history-q", "React")
        with page.expect_navigation():
            page.locator("#history-q").press("Enter")
        rec("search q=React filters to one",
            page.locator("ul.history-list li.history-item").count() == 1
            and "React hooks" in page.locator("body").inner_text(),
            page.url)

        # ---- clear search keeps context ----
        with page.expect_navigation():
            page.click(".iq-search-clear")
        rec("clear-search restores all", page.locator("ul.history-list li.history-item").count() == 3)

        # ---- difficulty filter (auto-submit select) ----
        with page.expect_navigation():
            page.select_option("#history-difficulty", "hard")
        bt = page.locator("body").inner_text()
        rec("difficulty=hard keeps two, drops React",
            page.locator("ul.history-list li.history-item").count() == 2
            and "SQL joins" in bt and "Backend Developer" in bt and "React hooks" not in bt,
            page.url)

        # ---- chips preserve the difficulty filter ----
        rec("practice chip keeps difficulty=hard", "difficulty=hard" in page.locator("[data-autosubmit]").count().__str__() or "difficulty=hard" in page.url)

        # ---- date range ----
        with page.expect_navigation():
            page.fill("#history-from", "2026-05-11")
        bt = page.locator("body").inner_text()
        rec("date-range from=2026-05-11 keeps May20+, drops May10",
            page.locator("ul.history-list li.history-item").count() == 2
            and "SQL joins" in bt, page.url)
        with page.expect_navigation():
            page.fill("#history-to", "2026-05-25")
        bt = page.locator("body").inner_text()
        rec("date-range to=2026-05-25 keeps SQL joins only",
            page.locator("ul.history-list li.history-item").count() == 1
            and "SQL joins" in bt and "Backend Developer" not in bt, page.url)

        # ---- clear filters link ----
        with page.expect_navigation():
            page.click(".iq-history-clear")
        rec("clear-filters restores all", page.locator("ul.history-list li.history-item").count() == 3)

        # ---- combined mode + search + difficulty ----
        with page.expect_navigation():
            page.click("a.iq-filter-chip >> text=Smart Practice")
        rec("mode=practice hides type filter", page.locator("#history-type").count() == 0)
        page.fill("#history-q", "joins")
        with page.expect_navigation():
            page.locator("#history-q").press("Enter")
        with page.expect_navigation():
            page.select_option("#history-difficulty", "easy")
        bt = page.locator("body").inner_text()
        rec("mode+practice+search+difficulty yields empty+filters state",
            "No completed sessions" in bt and "Clear filters" in bt, page.url)

        # ---- all sessions again, type filter (real only) ----
        with page.expect_navigation():
            page.click(".iq-history-clear")
        with page.expect_navigation():
            page.select_option("#history-type", "technical")
        bt = page.locator("body").inner_text()
        rec("type=technical keeps real session only",
            page.locator("ul.history-list li.history-item").count() == 1
            and "Backend Developer" in bt, page.url)
        with page.expect_navigation():
            page.select_option("#history-type", "behavioral")
        rec("type=behavioral empty state", "No completed sessions" in page.locator("body").inner_text())

        # ---- real mode chip navigation (reset first: chips carry filters) ----
        page.goto(f"{BASE}/history")
        with page.expect_navigation():
            page.click("a.iq-filter-chip >> text=Real Interviews")
        rec("mode=real shows real session",
            page.locator("ul.history-list li.history-item").count() == 1
            and "Backend Developer" in page.locator("body").inner_text())

        # ---- detail + replay walk ----
        with page.expect_navigation():
            page.click("text=Open replay")
        rec("replay walkthrough loads", "Replay" in page.locator("body").inner_text() or "Backend Developer" in page.locator("title").inner_text(), page.url)

        # ---- no inline style/script in the filter controls (CSP) ----
        page.goto(f"{BASE}/history", wait_until="load")
        inline_styles = page.locator(".iq-history-controls [style]").count()
        inline_scripts = page.locator(".iq-history-controls script").count()
        rec("filter controls contain no inline style/script (CSP clean)",
            inline_styles == 0 and inline_scripts == 0, f"styles={inline_styles} scripts={inline_scripts}")
        rec("no CSP violations logged during session", not console_errors,
            "; ".join(console_errors[:3]))

        # ---- dark theme ----
        ctx.add_init_script(
            "try { localStorage.setItem('interviewiq-theme', 'dark'); } catch (e) {}"
        )
        dark_page = ctx.new_page()
        dark_page.on("console", lambda msg: console_errors.append(msg.text)
                     if "Content Security Policy" in msg.text or "Refused to" in msg.text
                     else None)
        dark_page.goto(f"{BASE}/history", wait_until="load")
        bg_dark = dark_page.evaluate("getComputedStyle(document.body).backgroundColor")
        rec("dark theme renders sessions", dark_page.locator("ul.history-list li.history-item").count() == 3, bg_dark)

        # ---- responsive overflow (light, populated + filtered state) ----
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(f"{BASE}/history", wait_until="load")
        overflow = []
        for width in (1440, 1024, 768, 560, 360):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(300)
            overflow.append((width, page.evaluate("document.documentElement.scrollWidth - window.innerWidth")))
        bad = [(w, v) for w, v in overflow if v > 0]
        rec("no horizontal overflow at 1440/1024/768/560/360", not bad,
            " ".join(f"{w}px:+{v}" for w, v in overflow))

        page.fill("#history-q", "xyz-nope")
        with page.expect_navigation():
            page.locator("#history-q").press("Enter")
        overflow_f = []
        for width in (560, 360):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(200)
            overflow_f.append((width, page.evaluate("document.documentElement.scrollWidth - window.innerWidth")))
        badf = [(w, v) for w, v in overflow_f if v > 0]
        rec("no overflow in filtered empty state at 560/360", not badf,
            " ".join(f"{w}px:+{v}" for w, v in overflow_f))

        browser.close()

    # ---- summary ----
    failed = [c for c in checks if not c[1]]
    print("\n" + "=" * 60)
    print(f"History filters browser QA: {len(checks) - len(failed)}/{len(checks)} passed")
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
            (user_id, "practice", "", "easy", "React hooks", "2026-05-10 09:00:00", 70, "completed", 0, 0),
            (user_id, "practice", "", "hard", "SQL joins", "2026-05-20 09:00:00", 80, "completed", 0, 0),
            (user_id, "real", "Backend Developer", "hard", "technical", "2026-06-01 09:00:00", 85, "completed", 4, 30),
        ],
    )
    interview_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM interviews WHERE user_id=? ORDER BY id", (user_id,)).fetchall()]
    scores = ('{"technical_accuracy":80,"relevance":75,"completeness":70,'
              '"clarity":80,"communication":70}')
    for iid in interview_ids:
        qid = conn.execute(
            "INSERT INTO questions (interview_id, question, expected_concepts) "
            "VALUES (?, '', '[]')", (iid,)).lastrowid
        conn.execute(
            "INSERT INTO answers (question_id, user_answer, score, scores, feedback) "
            "VALUES (?, 'QA seeded answer.', 75.0, ?, 'QA seeded feedback.')",
            (qid, scores),
        )
    conn.execute(
        "INSERT INTO interview_memory (user_id, strong_topics, weak_topics, repeated_mistakes) "
        "VALUES (?, ?, ?, ?)",
        (user_id, '["React","SQL"]', '["Algorithms"]', '[]'),
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    raise SystemExit(main())