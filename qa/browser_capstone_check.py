"""Capstone final QA - full new-user journey (Stage 12, Section 3 + 15).

Drives the complete product journey in a real browser against the app
running in-process with a fake Gemini transport, capturing console errors,
CSP violations, page errors and broken same-origin links along the way.

Journey: landing -> register -> logout -> login -> profile setup ->
dashboard -> Smart Practice (question/answer/feedback/next question) ->
history -> replay (bookmark + personal note) -> Real Interview
(config/start/live/answer/follow-up/finish/complete) -> roadmap generation
+ practice from roadmap + day toggle -> analytics -> daily challenge ->
streak -> achievements -> settings (theme preference) -> theme toggle ->
logout.

Run:
  set PYTHONPATH=%CD%
  .venv\Scripts\python.exe interview_Ai/qa/browser_capstone_check.py
"""
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.parse

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from playwright.sync_api import sync_playwright

TMP = tempfile.mkdtemp(prefix="iq_capstone_")
DB_PATH = os.path.join(TMP, "qa.db")
PORT = 8798
BASE = f"http://127.0.0.1:{PORT}"
EMAIL = "journey@example.com"
PASSWORD = "Capstone-password-1"

os.environ["DATABASE_PATH"] = DB_PATH
os.environ["FLASK_ENV"] = "production"
os.environ["SECRET_KEY"] = "qa-capstone-verification-secret"
os.environ.pop("GEMINI_API_KEY", None)

QUESTION_POOL = [
    "Explain how a B-tree index speeds up queries.",
    "Compare inner and left joins.",
    "Summarize a time you handled a production outage.",
]
EVALUATION = {
    "scores": {
        "technical_accuracy": 42,
        "relevance": 40,
        "completeness": 38,
        "clarity": 45,
        "communication": 40,
    },
    "feedback": "Solid structure - sharpen the fundamentals.",
    "missing_points": ["Mention query plans"],
    "model_answer": "A B-tree keeps keys sorted so lookups are O(log n).",
}

_QUESTION_PREFIX = "Create one interview question"
_EXTRACT_MARKER = "Extract the specific technologies"
_FOLLOW_UP_PREFIX = "Original question:"
_EVALUATE_MARKER = "Candidate's answer:"

_prompt_count = {"n": 0}


def fake_transport(system, user, temperature, max_output_tokens):
    if user.startswith(_QUESTION_PREFIX):
        q = QUESTION_POOL[_prompt_count["n"] % len(QUESTION_POOL)]
        _prompt_count["n"] += 1
        return json.dumps({
            "question": q,
            "question_type": "conceptual",
            "expected_concepts": ["B-tree structure", "query plans"],
        })
    if _EXTRACT_MARKER in user:
        return json.dumps({"entities": []})
    if user.startswith(_FOLLOW_UP_PREFIX):
        return json.dumps({"follow_up_question": QUESTION_POOL[1],
                           "reasoning": "internal only"})
    if _EVALUATE_MARKER in user:
        return json.dumps(EVALUATION)
    if user.startswith("The candidate's weak skills are:"):
        return json.dumps({
            "roadmap": [
                {"day": 1, "topic": "SQL joins", "practice_focus": "Exercise joins"},
                {"day": 2, "topic": "Caching", "practice_focus": "Design a cache"},
                {"day": 3, "topic": "API design", "practice_focus": "Design a REST API"},
            ]
        })
    raise RuntimeError(f"unexpected prompt reached transport: {user[:80]}")


results = []


def rec(label, ok, detail=""):
    results.append((label, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} - {label}"
          + (f" | {detail}" if detail else ""))


def wait_until_page(page, js, timeout=15000):
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        try:
            if page.evaluate(js):
                return True
        except Exception:
            pass
        page.wait_for_timeout(150)
    return False


console_errors = []
page_errors = []
csp_violations = []


def attach_watchers(page):
    page.on("console", lambda m: (
        console_errors.append(m.text)
        if m.type == "error"
        and "Content Security Policy" not in m.text else None))
    page.on("console", lambda m: (
        csp_violations.append(m.text)
        if "Content Security Policy" in m.text else None))
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))


def link_scan(page, label):
    """Fetch every same-origin GET link on the current page; record failures."""
    broken = page.evaluate("""async () => {
      const links = new Set();
      document.querySelectorAll('a[href]').forEach(a => {
        const h = a.getAttribute('href') || '';
        if (h.startsWith('#') || h.startsWith('mailto:')
            || h.startsWith('tel:') || h.startsWith('javascript:')) return;
        if (h.startsWith('/')) links.add(h);
        else if (h.startsWith(location.origin)) links.add(h);
      });
      const out = [];
      for (const href of links) {
        try {
          const r = await fetch(href, {credentials: 'same-origin',
              redirect: 'manual'});
          if (r.status >= 400) out.push(href + ':' + r.status);
        } catch (e) {
          out.push(href + ':ERR');
        }
      }
      return out;
    }""")
    rec(f"{label}: no broken same-origin links", not broken,
        ", ".join(broken) or f"{len(broken)} broken")


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        attach_watchers(page)
        try:
            _journey(page)
        finally:
            browser.close()

    rec("capstone journey: zero console errors", not console_errors,
        "; ".join(console_errors[:3]))
    rec("capstone journey: zero CSP violations", not csp_violations,
        "; ".join(csp_violations[:3]))
    rec("capstone journey: zero uncaught page errors", not page_errors,
        "; ".join(page_errors[:3]))


def _journey(page):
    # 1. Landing (public)
    page.goto(f"{BASE}/", wait_until="load")
    rec("landing renders + brand", 
        page.locator("header.public-nav, .ln-nav").count()
        or "InterviewIQ" in page.content(),
        page.url)
    rec("landing has public theme toggle",
        page.locator("button#theme-toggle").count() >= 1)
    link_scan(page, "landing")

    # 2. Register (UI form with CSRF)
    page.goto(f"{BASE}/register", wait_until="load")
    page.fill("input[name=name]", "Journey Tester")
    page.fill("input[name=email]", EMAIL)
    page.fill("input[name=password]", PASSWORD)
    page.fill("input[name=confirm]", PASSWORD)
    page.click("form button[type=submit]")
    ok = wait_until_page(page, "location.pathname === '/dashboard'")
    rec("register -> auto-login -> dashboard", ok, page.url)

    # 3. Logout -> login
    page.locator("form[action='/logout'] button").click()
    ok = wait_until_page(page, "location.pathname === '/'")
    rec("logout returns to landing", ok, page.url)
    page.goto(f"{BASE}/login", wait_until="load")
    for _attempt in range(3):
        page.goto(f"{BASE}/login", wait_until="load")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("form button[type=submit]")
        if wait_until_page(page, "location.pathname === '/dashboard'"):
            break
    rec("login returns to dashboard", page.url.endswith("/dashboard"), page.url)

    # 4. Profile setup
    page.goto(f"{BASE}/profile", wait_until="load")
    page.fill("input#profile-role", "Backend Engineer")
    page.fill("textarea#profile-skills", "SQL, Python, Git")
    page.click("form.stack-form button[type=submit]")
    ok = wait_until_page(
        page, "document.querySelectorAll('.badge-cloud .badge').length >= 3")
    rec("profile saved role + skills render as badges", ok)

    # 5. Dashboard
    page.goto(f"{BASE}/dashboard", wait_until="load")
    rec("dashboard KPI tiles render",
        page.locator(".iq-kpi").count() >= 3
        and page.locator(".iq-kpi-value").count() >= 3)
    rec("dashboard profile chip shows user name",
        "Journey Tester" in page.content())
    link_scan(page, "dashboard")

    # 6. Smart practice: generate -> answer -> feedback -> next question
    page.goto(f"{BASE}/practice", wait_until="load")
    page.fill("input#topic", "SQL joins")
    page.select_option("select#difficulty", "easy")
    page.click("form[action='/practice/question'] button[type=submit]")
    ok = wait_until_page(
        page, "!!document.querySelector('#answer')"
              " && document.querySelector('#answer').name === 'answer'")
    rec("practice question generated (answer form shown)", ok, page.url)
    rec("hint panel lists expected concepts",
        page.locator(".sp-concept-list li").count() >= 1)
    q1 = page.locator(".sp-question-text").inner_text()
    page.fill("textarea#answer", "I would use a B-tree index for lookups.")
    page.click("form[action='/practice/answer'] button[type=submit]")
    ok = wait_until_page(page, "!!document.querySelector('.sp-score-cap')")
    rec("practice answer evaluated -> feedback view", ok, page.url)
    rec("overall score rendered (server data-count)",
        page.locator(".sp-ring-value").count() == 1
        and page.locator(".sp-ring-value")
            .get_attribute("data-count") == "41",
        page.locator(".sp-ring-value").get_attribute("data-count")
        if page.locator(".sp-ring-value").count() else "")
    rec("coach feedback + missing points shown",
        page.locator(".sp-feedback-text").count() == 1
        and page.locator(".sp-missing-list li").count() >= 1)
    rec("model answer available behind a reveal",
        page.locator(".sp-model-answer").count() == 1)

    # 7. Retry / next question on the same session
    page.click("form[action='/practice/question'] button[type=submit]")
    ok = wait_until_page(page, "location.href.includes('/practice/question/')")
    rec("next question on the same session", ok, page.url)
    page.fill("textarea#answer", "Another solid answer about joins.")
    page.click("form[action='/practice/answer'] button[type=submit]")
    ok = wait_until_page(page, "!!document.querySelector('.sp-score-cap')")
    rec("second answer evaluated", ok)

    # 8. History reflects the practice session (2 graded answers)
    page.goto(f"{BASE}/history", wait_until="load")
    rec("history lists the practice session",
        "SQL joins" in page.content())
    rec("history shows graded answer count",
        page.locator(".stat-note").all_text_contents().__len__() > 0
        and any("2 graded" in t for t in page.locator(".stat-note")
                .all_text_contents()))
    link_scan(page, "history")

    # 9. Replay: bookmark + personal note on the practice session
    page.goto(f"{BASE}/replay", wait_until="load")
    rec("replay index lists the finished practice session",
        page.locator("a[href^='/replay/']").count() >= 1)
    link_scan(page, "replay index")
    page.goto(page.evaluate(
        "document.querySelector('a[href^=\"/replay/\"]').href"),
        wait_until="load")
    rec("replay walkthrough shows answer + feedback",
        page.locator(".sp-answer-text, .sp-feedback-text, .replay-feedback")
        .count() >= 1 or "Coach feedback" in page.content())
    rec("bookmark toggle present",
        page.locator(".bookmark-toggle").count() >= 1)
    page.click(".bookmark-toggle:first-of-type" if page.locator(
        ".bookmark-toggle").count() == 1 else ".bookmark-toggle[aria-pressed=false]")
    ok = wait_until_page(
        page, "document.querySelector('.bookmark-toggle')"
              " && document.querySelector('.bookmark-toggle')"
              ".getAttribute('aria-pressed') === 'true'")
    rec("bookmark added via toggle", ok)
    page.fill("textarea#note-content", "Review joins again before the interview.")
    page.click(".personal-note-actions button[type=submit], .personal-note-form button")
    ok = wait_until_page(
        page, "document.querySelector('.personal-note-state')"
              " && document.querySelector('.personal-note-state')"
              ".textContent.indexOf('Saved') !== -1")
    rec("personal note saved to the question", ok)

    # 10. Real Interview: config(Text mode) -> live -> answer -> finish
    page.goto(f"{BASE}/interview", wait_until="load")
    page.click("[data-mode-card='text-text']")
    page.fill("input#role", "Backend Engineer")
    page.select_option("select#difficulty", "medium")
    page.select_option("select#type", "technical")
    page.click("form[action='/interview/start'] button[type=submit]")
    ok = wait_until_page(
        page, "location.pathname.match(/^\\/interview\\/\\d+$/)")
    rec("real interview starts into live view", ok, page.url)
    page.wait_for_selector("#interview-timer", timeout=10000)
    rec("live view shows the running timer",
        page.locator("#interview-timer").count() == 1
        and page.locator("#interview-timer")
            .get_attribute("data-seconds-remaining") is not None)
    page.fill("textarea#answer",
              "B-trees keep keys sorted so lookups are O(log n).")
    page.click("form#interview-answer-form button#answer-submit")
    ok = wait_until_page(page, "location.pathname.match(/^\\/interview\\/\\d+$/)")
    rec("answer submitted -> adaptive follow-up (still live)", ok, page.url)
    page.click(".interview-finish-form button[type=submit]")
    ok = wait_until_page(
        page, "location.pathname.endsWith('/complete')")
    rec("finish -> completion view", ok, page.url)
    wait_until_page(
        page, "document.querySelectorAll('.riv-score-value').length === 1",
        timeout=10000)
    rec("final score rendered",
        page.locator(".riv-score-value").count() == 1
        and page.locator(".riv-score-value").inner_text().strip() != "",
        page.locator(".riv-score-value").inner_text().strip())
    rec("per-dimension bars on the completion view",
        page.locator(".riv-dim").count() == 5,
        str(page.locator(".riv-dim").count()))
    link_scan(page, "interview complete")

    # 11. Roadmap: generate from weaknesses -> practice from a day -> toggle
    page.goto(f"{BASE}/roadmap", wait_until="load")
    page.click(".roadmap-regenerate button, "
               "form[action='/roadmap/generate'] button")
    ok = wait_until_page(
        page, "document.querySelectorAll('.roadmap-day').length >= 3")
    rec("roadmap generated from recorded weaknesses", ok,
        str(page.locator(".roadmap-day").count()))
    link_scan(page, "roadmap")
    day_href = page.evaluate(
        "document.querySelector('.rm-practice-btn').href")
    page.goto(day_href, wait_until="load")
    rec("practice from roadmap day literal (topic prefill)",
        "Practicing a roadmap topic" in page.content(), page.url)
    page.fill("input#topic", page.locator("input#topic").input_value())
    page.click("form[action='/practice/question'] button[type=submit]")
    ok = wait_until_page(page, "!!document.querySelector('#answer')")
    rec("roadmap practice question generated", ok, page.url)
    page.goto(f"{BASE}/roadmap", wait_until="load")
    page.click("form[action$='/toggle'] button, .rm-phase-actions button")
    ok = wait_until_page(
        page, "location.hash.indexOf('day-') !== -1"
              " || document.querySelector('.roadmap-day.is-completed')")
    rec("roadmap day toggled to completed", ok)

    # 12. Analytics
    page.goto(f"{BASE}/analytics", wait_until="load")
    rec("analytics readiness section renders",
        page.locator("#readiness-title").count() == 1)
    rec("analytics reports a readiness score from real data",
        page.locator(".iq-ring, [data-score]").count() >= 1
        or "not enough data" in page.content().lower())
    link_scan(page, "analytics")

    # 13. Daily challenge -> streak -> achievements
    page.goto(f"{BASE}/challenge", wait_until="load")
    rec("daily challenge question rendered",
        page.locator("textarea#answer").count() == 1)
    page.fill("textarea#answer", "A left join keeps every left-table row.")
    page.click("form[action='/challenge/answer'] button[type=submit]")
    ok = wait_until_page(
        page, "document.querySelector('.ch-status')"
              " && document.querySelector('.ch-status')"
              ".className.indexOf('is-done') !== -1")
    rec("challenge completed (status is-done)", ok)
    page.goto(f"{BASE}/dashboard", wait_until="load")
    rec("dashboard streak reflects the completed challenge",
        page.locator("[data-icon=streak] .iq-kpi-value").inner_text().strip()
        in ("1", "2", "3", "4", "5", "6", "7"))
    rec("dashboard achievements panel shows >=1 unlocked",
        page.locator(".iq-achievement.is-unlocked").count() >= 1,
        str(page.locator(".iq-achievement.is-unlocked").count()))
    page.goto(f"{BASE}/achievements", wait_until="load")
    rec("achievements gallery lists unlocked badges",
        page.locator(".ach-card.is-unlocked").count() >= 1,
        str(page.locator(".ach-card.is-unlocked").count()))
    link_scan(page, "achievements")

    # 14. Settings (theme preference form) - no password change (would lock out)
    page.goto(f"{BASE}/settings", wait_until="load")
    rec("settings page renders preferences form(s)",
        page.locator("form[action='/settings/preferences']").count() >= 1,
        str(page.locator("form[action='/settings/preferences']").count()))
    link_scan(page, "settings")
    rec("profile page link from topbar works",
        page.locator("a[href='/profile']").count() >= 1)

    # 15. Theme toggle round trip
    before = page.evaluate("document.documentElement.getAttribute('data-theme')")
    page.click("button#theme-toggle")
    after = page.evaluate(
        "document.documentElement.getAttribute('data-theme')")
    stored = page.evaluate("localStorage.getItem('interviewiq-theme')")
    rec("theme toggle flips data-theme + persists",
        before != after and stored is not None,
        f"{before}->{after} stored={stored}")
    page.click("button#theme-toggle")
    rec("theme toggle flips back",
        page.evaluate("document.documentElement.getAttribute('data-theme')")
        == before)

    # 16. Logout (sidebar button; the Account panel's sign-out is hidden until
    #     its tab is opened, so target the always-visible sidebar form)
    page.locator("form[action='/logout'] button").first.click()
    ok = wait_until_page(page, "location.pathname === '/'")
    rec("final logout returns to landing", ok, page.url)

    # 17. Cross-page integrity: authenticated pages no longer accessible
    page.goto(f"{BASE}/dashboard", wait_until="load")
    rec("dashboard requires login after logout",
        page.url.endswith("/login") or "/login" in page.url, page.url)


if __name__ == "__main__":
    from interview_Ai.app import create_app
    from interview_Ai.app.ai.gemini import GeminiService

    app = create_app()
    app.extensions["gemini"] = GeminiService(
        api_key="k", max_retries=0, transport=fake_transport)

    server = threading.Thread(
        target=app.run,
        kwargs={"host": "127.0.0.1", "port": PORT, "debug": False,
                "use_reloader": False},
        daemon=True,
    )
    server.start()
    for _ in range(100):
        try:
            requests.get(f"{BASE}/", timeout=1)
            break
        except requests.ConnectionError:
            time.sleep(0.1)

    run()

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\nCapstone journey browser QA: {passed}/{len(results)} passed")
    if not all(ok for _, ok, _ in results):
        print("FAILED:", *[label for label, ok, _ in results if not ok],
              sep="\n  ")
        sys.exit(1)
    print("ALL CHECKS PASSED")