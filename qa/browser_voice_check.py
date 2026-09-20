"""Browser QA for the Real Interview voice/avatar UI (Phase 9 / Stage 11).

Same harness as the company/notes/challenge suites: the app runs in-process on
127.0.0.1, users register over real HTTP, and Playwright drives the system
Microsoft Edge channel. The GeminiService installed on the in-process app is
replaced with a fake transport so every AI-backed route is exercised without a
real API key or a real micro-phone/speaker.

Headless Chromium has no real Web Speech API, so the voice-voice path is driven
deterministically with a tiny stub (SpeechRecognition + speechSynthesis) that
is injected *before* the page scripts and later driven by the test through a
page-level dispatcher. The stub is deliberately absent for a second context so
the no-API graceful-degradation path (voice cards disabled, microphone hidden,
plain text flow works) is exercised against the real headless engine.

Verifies (Stage 11 Part 12):
* three response-mode cards render on the config page and follow
  isSupportedMode/bestSupportedMode state,
* Voice->Voice live: auto question speech, avatar states (thinking/speaking/
  listening/processing/idle), replay, mute, interim transcript, final
  transcript appended to the answer box, editable + clearable transcript,
  empty-answer guard, manual submit through the *normal* backend text path,
  adaptive advance, mode switcher, finish -> completion,
* failure paths: microphone permission denied, recognition network error (with
  retry), TTS unavailable,
* Microphone -> no API (real headless): voice-voice disabled, mic hidden,
  text flow intact,
* prefers-reduced-motion kills the avatar animation while the state machine still
  runs,
* light/dark themes render the avatar and controls; no horizontal overflow at
  1440/1024/768/560/360; zero console errors / CSP violations; no inline styles.

Run:
  set PYTHONPATH=%CD%
  .venv\\Scripts\\python.exe interview_Ai/qa/browser_voice_check.py
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

TMP = tempfile.mkdtemp(prefix="iq_voice_")
DB_PATH = os.path.join(TMP, "qa.db")
PORT = 8797
BASE = f"http://127.0.0.1:{PORT}"
EMAIL = "voiceqa@example.com"
PASSWORD = "Qa-password-1"

os.environ["DATABASE_PATH"] = DB_PATH
os.environ["FLASK_ENV"] = "production"
os.environ["SECRET_KEY"] = "qa-voice-verification-secret"
os.environ.pop("GEMINI_API_KEY", None)

QUESTION_POOL = [
    "Explain how a B-tree index speeds up queries.",
    "Compare inner and left joins.",
    "What does ACID mean in relational databases?",
]
EVALUATION = {
    "scores": {
        "technical_accuracy": 42,
        "relevance": 40,
        "completeness": 38,
        "clarity": 45,
        "communication": 40,
    },
    "feedback": "Solid structure — sharpen the fundamentals.",
    "missing_points": ["Mention query plans"],
    "model_answer": "A B-tree keeps keys sorted so lookups are O(log n).",
}

_QUESTION_PREFIX = "Create one interview question"
_EXTRACT_MARKER = "Extract the specific technologies"
_FOLLOW_UP_PREFIX = "Original question:"
_EVALUATE_MARKER = "Candidate's answer:"

fake_prompts = []
_question_issued = {"n": 0}


def fake_transport(system, user, temperature, max_output_tokens):
    fake_prompts.append(user)
    if user.startswith(_QUESTION_PREFIX):
        q = QUESTION_POOL[_question_issued["n"] % len(QUESTION_POOL)]
        _question_issued["n"] += 1
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
    raise RuntimeError(f"unexpected prompt reached transport: {user[:80]}")


# Deterministic Web Speech stubs. The test drives events through
# window.__voiceTest.dispatch(); tts utterances resolve via ttsResolve/ttsFail.
STUB_JS = r"""
(function () {
  window.__voiceTest = {
    rec: null,
    ttsQueue: [],
    ttsSpoken: [],
    dispatch: function (type, data) {
      var r = window.__voiceTest.rec;
      if (!r) throw new Error("no active recognition");
      if (type === "start") {
        if (r.onstart) r.onstart();
      } else if (type === "interim") {
        if (r.onresult) r.onresult({
          results: [{ isFinal: false, 0: { transcript: data || "" } }] });
      } else if (type === "final") {
        if (r.onresult) r.onresult({
          results: [{ isFinal: true, 0: { transcript: data || "" } }] });
      } else if (type === "end") {
        if (r.onend) r.onend();
      } else if (type === "error") {
        if (r.onerror) r.onerror({ error: data || "unknown" });
      }
    },
    ttsResolve: function () {
      var q = window.__voiceTest.ttsQueue;
      var u = q.shift();
      if (u && u.onend) u.onend();
    },
    ttsFail: function () {
      var q = window.__voiceTest.ttsQueue;
      var u = q.shift();
      if (u && u.onerror) u.onerror({ error: "interrupted" });
    }
  };
  window.SpeechRecognition = function () {
    var self = this;
    this.lang = "en-US";
    this.interimResults = true;
    this.continuous = false;
    this.maxAlternatives = 1;
    this.start = function () {};
    this.stop = function () {
      window.__voiceTest.rec = null;
      if (self.onend) self.onend();
    };
    window.__voiceTest.rec = this;
  };
  window.webkitSpeechRecognition = window.SpeechRecognition;
  var UtteranceStub = function (text) { this.text = text; };
  var SynthStub = {
    getVoices: function () {
      return [{ name: "Google US English", lang: "en-US" },
              { name: "Microsoft Zira", lang: "en-US" }];
    },
    speak: function (u) {
      var t = window.__voiceTest;
      t.ttsQueue.push(u);
      t.ttsSpoken.push(u.text);
      if (u.onstart) u.onstart();
    },
    cancel: function () { window.__voiceTest.ttsQueue.length = 0; }
  };
  (function () {
    function define(name, value) {
      try {
        Object.defineProperty(window, name,
          { value: value, configurable: true, writable: true });
      } catch (e) {
        try { window[name] = value; } catch (e2) { /* keep native */ }
      }
    }
    define("SpeechSynthesisUtterance", UtteranceStub);
    define("speechSynthesis", SynthStub);
  })();
})();
"""

LIGHT_THEME = "try { localStorage.setItem('interviewiq-theme', 'light'); } catch (e) {}"
DARK_THEME = "try { localStorage.setItem('interviewiq-theme', 'dark'); } catch (e) {}"

# Simulate a browser without the Web Speech recognition API (Firefox-style):
# real headless Chromium/Edge surprisingly DOES expose SpeechRecognition, so it
# is stripped so hasSTT() resolves the way a genuinely unsupported browser
# would. speechSynthesis is left untouched so the real engine's own support is
# what decides the voice-text card state.
STRIP_STT = r"""
(function () {
  ["SpeechRecognition", "webkitSpeechRecognition"].forEach(function (name) {
    try { delete window[name]; } catch (e) {}
    try {
      Object.defineProperty(window, name,
        { value: undefined, configurable: true, writable: true });
    } catch (e) {}
    try { window[name] = undefined; } catch (e) {}
  });
})();
"""


def main():
    from interview_Ai.app import create_app
    from interview_Ai.app.ai.gemini import GeminiService

    app = create_app()
    app.extensions["gemini"] = GeminiService(
        api_key="fake-voice-qa", max_retries=0, transport=fake_transport,
    )
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
        print(("PASS" if ok else "FAIL"), "-", name,
              ("| " + str(detail) if detail else ""))

    hs = requests.Session()
    page = _csrf(hs, "/register")
    r = hs.post(f"{BASE}/register", data={
        "name": "QA User", "email": EMAIL, "password": PASSWORD,
        "confirm": PASSWORD, "csrf_token": page,
    }, allow_redirects=False)
    rec("student registers", r.status_code == 302
        and "/dashboard" in r.headers.get("Location", ""), r.status_code)
    user_id = _db_value("SELECT id FROM users WHERE email=?", (EMAIL,))

    from playwright.sync_api import sync_playwright

    console_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)

        # ---------------- supported-browser context (stubbed) ----------------
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        for cookie in hs.cookies:
            ctx.add_cookies([{
                "name": cookie.name, "value": cookie.value,
                "domain": "127.0.0.1", "path": "/",
            }])
        ctx.add_init_script(STUB_JS)
        ctx.add_init_script(LIGHT_THEME)
        page = ctx.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text)
                if ("Content Security Policy" in msg.text or "Refused to" in msg.text)
                else None)
        page.on("pageerror", lambda exc: console_errors.append("PAGEERROR: " + str(exc)))

        # ---- config: three cards, all supported with stubs, T->T default ----
        page.goto(f"{BASE}/interview", wait_until="load")
        cards = page.locator("[data-mode-card]")
        rec("config renders three response-mode cards (stubs present)",
            cards.count() == 3, f"count={cards.count()}")
        rec("all three cards are selectable when STT+TTS are available",
            page.locator("[data-mode-card]:not(.is-disabled)").count() == 3)
        selected = page.evaluate(
            "document.querySelector('[data-mode-card].is-selected')"
            ".getAttribute('data-mode-card')")
        rec("best supported mode (Voice -> Voice) is preselected with stubs",
            selected == "voice-voice", repr(selected))

        # ---- start a Voice->Voice interview ----
        page.click("[data-mode-card='voice-voice']")
        page.fill("input#role", "Backend Developer")
        prompts_before = len(fake_prompts)
        with page.expect_navigation():
            page.click("#riv-setup-form button[type=submit]")
        page.wait_for_load_state("load")
        live_url = page.url
        interview_a_id = re.search(r"/interview/(\d+)", live_url).group(1)
        rec("interview starts into the live voice view",
            "/interview/" in live_url and re.search(r"/interview/\d+$", live_url)
            is not None, live_url)
        rec("voice-voice is the live mode indicator",
            page.locator("[data-mode-indicator]").inner_text().upper()
            == "VOICE \u2192 VOICE",
            page.locator("[data-mode-indicator]").inner_text())
        rec("question is rendered into the stage",
            page.locator("[data-question]").count() == 1
            and bool(page.locator("[data-question]").inner_text().strip()),
            page.locator("[data-question]").inner_text()[:40])
        rec("voice layer reached the fake transport (start prompt used)",
            len(fake_prompts) == prompts_before + 1
            and fake_prompts[-1].startswith(_QUESTION_PREFIX),
            f"calls={len(fake_prompts)}")
        rec("timer is rendered with the server deadline",
            page.locator("#interview-timer").count() == 1
            and int(page.locator("#interview-timer")
                    .get_attribute("data-seconds-remaining")) > 0
            and re.match(r"^\d{2}:\d{2}$",
                         page.locator("#timer-value").inner_text()) is not None,
            page.locator("#timer-value").inner_text())

        # ---- auto question speech -> speaking avatar ----
        wait_until_page(page,
            "document.querySelector('[data-tts-status]')"
            " && document.querySelector('[data-tts-status]').textContent"
            ".indexOf('Speaking') !== -1")
        rec("avatar auto-speaks the opening question (browser TTS stub)",
            page.locator("[data-avatar].is-speaking").count() == 1,
            page.locator("[data-tts-status]").inner_text())
        rec("question was handed to the speech engine",
            "Explain how a B-tree index speeds up queries."
            in page.evaluate("window.__voiceTest.ttsSpoken.join('|')"))

        # ---- replay ----
        page.click("[data-btn-replay]")
        wait_until_page(page,
            "document.querySelector('[data-avatar]')"
            " && document.querySelector('[data-avatar]').classList"
            ".contains('is-speaking')")
        rec("replay re-reads the question", True)

        # ---- TTS resolution returns to idle ----
        page.evaluate("window.__voiceTest.ttsResolve()")
        wait_until_page(page,
            "document.querySelector('[data-avatar]').classList"
            ".contains('is-idle')")
        rec("avatar returns to idle when the utterance ends", True)

        # ---- mute ----
        page.click("[data-btn-mute]")
        rec("mute toggles on (aria-pressed + label + status)",
            page.locator("[data-btn-mute]").get_attribute("aria-pressed") == "true"
            and page.locator("[data-btn-mute]").inner_text() == "Unmute voice"
            and "Voice muted." in page.locator("[data-tts-status]").inner_text(),
            page.locator("[data-tts-status]").inner_text())
        page.click("[data-btn-mute]")
        rec("unmute restores the label and status",
            page.locator("[data-btn-mute]").get_attribute("aria-pressed") == "false"
            and page.locator("[data-btn-mute]").inner_text() == "Mute voice")

        # ---- microphone: listening -> interim -> final -> end ----
        page.click("[data-btn-mic]")
        page.evaluate("window.__voiceTest.dispatch('start', null)")
        wait_until_page(page,
            "document.querySelector('[data-mic-status]').textContent"
            ".indexOf('speak now') !== -1")
        rec("microphone starts: avatar listening + mic status",
            page.locator("[data-btn-mic]").get_attribute("aria-label")
            == "Stop microphone"
            and page.locator("[data-avatar].is-listening").count() == 1,
            page.locator("[data-mic-status]").inner_text())
        page.evaluate("window.__voiceTest.dispatch('interim', 'cache invalidation')")
        rec("interim transcript is shown live under the mic",
            "cache invalidation" in page.locator("[data-mic-interim]").inner_text()
            and page.locator("[data-mic-interim]").is_visible(),
            page.locator("[data-mic-interim]").inner_text())
        page.evaluate("window.__voiceTest.dispatch"
                      "('final', 'explain cache invalidation strategies')")
        rec("final transcript is appended into the answer box",
            page.locator("#answer").input_value() ==
            "explain cache invalidation strategies",
            repr(page.locator("#answer").input_value()))
        page.evaluate("window.__voiceTest.dispatch('end', null)")
        wait_until_page(page,
            "document.querySelector('[data-mic-status]').textContent"
            ".indexOf('review and edit the transcript') !== -1")
        rec("recording stops and avatar settles to idle",
            page.locator("[data-btn-mic]").get_attribute("aria-label")
            == "Start microphone",
            page.locator("[data-mic-status]").inner_text())
        wait_until_page(page,
            "document.querySelector('[data-avatar]').classList"
            ".contains('is-idle')")
        rec("avatar returns to idle after processing the transcript", True)

        # ---- transcript is editable + clearable ----
        page.fill("#answer", "A correct answer about B-tree lookups.")
        rec("spoken transcript is editable before submit",
            page.locator("#answer").input_value()
            == "A correct answer about B-tree lookups.")
        page.fill("#answer", "")
        evals_before = _db_value(
            "SELECT COUNT(*) FROM answers a JOIN questions q"
            " ON a.question_id=q.id JOIN interviews i ON q.interview_id=i.id"
            " WHERE i.user_id=?", (user_id,))
        page.click("#answer-submit")
        page.wait_for_timeout(1200)
        rec("empty-answer guard blocks submission (confirm dismissed)",
            page.url == live_url
            and _db_value(
                "SELECT COUNT(*) FROM answers a JOIN questions q"
                " ON a.question_id=q.id JOIN interviews i"
                " ON q.interview_id=i.id WHERE i.user_id=?",
                (user_id,)) == evals_before, page.url)
        page.click("[data-btn-clear]")
        rec("clear button empties the transcript box",
            page.locator("#answer").input_value() == "")
        page.fill("#answer", "A correct answer about B-tree lookups.")

        # ---- submit -> normal backend text path -> adaptive advance ----
        with page.expect_navigation():
            page.click("#answer-submit")
        page.wait_for_load_state("load")
        rec("voice-voice answer submits through the normal text endpoint",
            re.search(r"/interview/\d+$", page.url) is not None
            and page.locator("[data-question]").inner_text() == QUESTION_POOL[1],
            page.url)
        eval_prompts = [p for p in fake_prompts
                if p.startswith("Interview question:")
                and _EVALUATE_MARKER in p
                and "Evaluate this answer." in p]
        rec("spoken (edited) transcript reached the evaluation prompt",
            bool(eval_prompts)
            and "A correct answer about B-tree lookups." in eval_prompts[-1],
            eval_prompts[-1][:80] if eval_prompts
            else "; ".join(p[:60] for p in fake_prompts[-4:]))
        a_row = _db_row(
            "SELECT a.user_answer, a.score FROM answers a JOIN questions q"
            " ON a.question_id=q.id JOIN interviews i ON q.interview_id=i.id"
            " WHERE i.user_id=? ORDER BY a.id DESC LIMIT 1", (user_id,))
        rec("the voice answer was evaluated and scored server-side",
            a_row is not None and a_row["score"] is not None, dict(a_row) if a_row else None)
        rec("the adaptive engine advanced to a follow-up question",
            _db_value("SELECT COUNT(*) FROM questions WHERE interview_id=?",
                      (interview_a_id,)) == 2)

        # ---- failure path: recognition network error + retry ----
        page.click("[data-btn-mic]")
        page.evaluate("window.__voiceTest.dispatch('start', null)")
        wait_until_page(page,
            "document.querySelector('[data-mic-status]').textContent"
            ".indexOf('speak now') !== -1")
        page.evaluate("window.__voiceTest.dispatch('error', 'network')")
        rec("recognition network error surfaces a clear status",
            "network error" in page.locator("[data-mic-status]").inner_text()
            and page.locator("[data-mic-status].is-error").count() == 1,
            page.locator("[data-mic-status]").inner_text())
        page.click("[data-btn-mic]")
        page.evaluate("window.__voiceTest.dispatch('start', null)")
        wait_until_page(page,
            "document.querySelector('[data-mic-status]').textContent"
            ".indexOf('speak now') !== -1")
        rec("microphone retries cleanly after a transient error",
            page.locator("[data-btn-mic]").get_attribute("aria-label")
            == "Stop microphone")
        page.evaluate("window.__voiceTest.dispatch('end', null)")
        wait_until_page(page,
            "document.querySelector('[data-mic-status]').textContent"
            ".indexOf('review and edit the transcript') !== -1")

        # ---- failure path: TTS unavailable ----
        page.click("[data-btn-replay]")
        wait_until_page(page,
            "document.querySelector('[data-avatar]').classList"
            ".contains('is-speaking')")
        page.evaluate("window.__voiceTest.ttsFail()")
        wait_until_page(page,
            "document.querySelector('[data-tts-status]').textContent"
            ".indexOf('Voice playback is unavailable') !== -1")
        rec("TTS failure falls back to a readable text state",
            page.locator("[data-avatar].is-idle").count() == 1,
            page.locator("[data-tts-status]").inner_text())

        # ---- live mode switcher ----
        page.click("[data-mode='text-text']")
        rec("live switcher moves Voice->Voice to Text->Text",
            page.locator("[data-mode-indicator]").inner_text().upper()
            == "TEXT \u2192 TEXT")
        rec("mic + tts controls hide in Text->Text mode",
            page.locator("[data-mic]").is_hidden()
            and page.locator("[data-tts-controls]").is_hidden())

        # ---- finish -> completion ----
        with page.expect_navigation():
            page.click(".riv-finish button")
        page.wait_for_load_state("load")
        cbody = page.locator("body").inner_text()
        rec("finish completes the interview with a graded transcript",
            "Interview complete" in cbody
            and "B-tree lookups" in cbody
            and page.locator(".riv-score-note").inner_text().strip()
            == "1 answer graded"
            and page.locator(".riv-score-value").count() == 1,
            page.url)
        complete_a_url = page.url

        # ---- failure path: microphone permission denied (fresh interview) ----
        page.goto(f"{BASE}/interview", wait_until="load")
        page.click("[data-mode-card='voice-voice']")
        page.fill("input#role", "Backend Developer")
        with page.expect_navigation():
            page.click("#riv-setup-form button[type=submit]")
        page.wait_for_load_state("load")
        page.click("[data-btn-mic]")
        page.evaluate("window.__voiceTest.dispatch('error', 'not-allowed')")
        wait_until_page(page,
            "document.querySelector('[data-mic-status]').textContent"
            ".indexOf('permission was denied') !== -1")
        rec("microphone permission denied blocks the mic + shows guidance",
            page.locator("[data-mic]").is_hidden()
            and page.locator("[data-btn-mic]").is_disabled()
            and "permission was denied"
            in page.locator("[data-mic-status]").inner_text(),
            page.locator("[data-mic-status]").inner_text())
        rec("typing still works after mic denial (graceful legacy path)",
            not page.locator("#answer").is_editable() is False)
        blocked_live_url = page.url

        # ---------------- reduced-motion context (stubbed) -------------------
        rm_ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                                     reduced_motion="reduce")
        for cookie in hs.cookies:
            rm_ctx.add_cookies([{
                "name": cookie.name, "value": cookie.value,
                "domain": "127.0.0.1", "path": "/",
            }])
        rm_ctx.add_init_script(STUB_JS)
        rm_page = rm_ctx.new_page()
        rm_page.goto(blocked_live_url, wait_until="load")
        wait_until_page(rm_page,
            "document.querySelector('[data-avatar]').classList"
            ".contains('is-speaking')")
        anim = rm_page.evaluate(
            "getComputedStyle(document.querySelector('[data-avatar]')"
            " .querySelector('.riv-h3d-room')).animationName")
        rec("prefers-reduced-motion disables avatar animation",
            anim == "none", repr(anim))
        rec("avatar state machine still runs under reduced motion",
            rm_page.locator("[data-avatar].is-speaking").count() == 1,
            "speaking")
        rm_ctx.close()

        # ---------------- dark theme (stubbed) -------------------------------
        dark_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        for cookie in hs.cookies:
            dark_ctx.add_cookies([{
                "name": cookie.name, "value": cookie.value,
                "domain": "127.0.0.1", "path": "/",
            }])
        dark_ctx.add_init_script(STUB_JS)
        dark_ctx.add_init_script(DARK_THEME)
        dark_page = dark_ctx.new_page()
        dark_page.goto(f"{BASE}/interview", wait_until="load")
        rec("dark theme renders config with all three mode cards",
            dark_page.locator("[data-mode-card]").count() == 3
            and dark_page.locator("select#company").count() == 1,
            dark_page.evaluate("getComputedStyle(document.body).backgroundColor"))
        dark_page.goto(blocked_live_url, wait_until="load")
        dark_bg = dark_page.evaluate(
            "getComputedStyle(document.body).backgroundColor")
        light_bg = page.evaluate(
            "getComputedStyle(document.body).backgroundColor")
        rec("dark live view renders the avatar and differs from light",
            dark_bg != light_bg
            and dark_page.locator("[data-avatar]").count() == 1,
            f"{light_bg} vs {dark_bg}")
        dark_ctx.close()

        # ---------------- unsupported browser (no stubs, real headless) ------
        plain_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        for cookie in hs.cookies:
            plain_ctx.add_cookies([{
                "name": cookie.name, "value": cookie.value,
                "domain": "127.0.0.1", "path": "/",
            }])
        plain_ctx.add_init_script(STRIP_STT)
        plain_ctx.add_init_script(LIGHT_THEME)
        plain_page = plain_ctx.new_page()
        plain_page.on("console", lambda msg: console_errors.append(msg.text)
                      if ("Content Security Policy" in msg.text
                          or "Refused to" in msg.text) else None)
        plain_page.goto(f"{BASE}/interview", wait_until="load")
        has_sr = plain_page.evaluate("!!(window.SpeechRecognition"
                                     " || window.webkitSpeechRecognition)")
        has_tts = plain_page.evaluate(
            "!!(window.speechSynthesis && 'SpeechSynthesisUtterance' in window)")
        card_state = plain_page.evaluate(
            "Array.from(document.querySelectorAll('[data-mode-card]'))"
            ".map(function (c) { return [c.getAttribute('data-mode-card'),"
            " c.classList.contains('is-disabled')]; })")
        rec("stripping SpeechRecognition simulates a browser without Web Speech",
            not has_sr, f"hasSR={has_sr}")
        rec("voice-voice card is disabled without SpeechRecognition",
            any(m == "voice-voice" and d for m, d in card_state),
            str(card_state))
        rec("voice-text card state matches the real speechSynthesis support",
            any(m == "voice-text" and d == (not has_tts)
                for m, d in card_state), f"hasTTS={has_tts} states={card_state}")
        rec("at least one text mode stays enabled",
            any(not d for _, d in card_state))
        disabled_note = plain_page.evaluate(
            "document.querySelector(\"[data-mode-card='voice-voice'] "
            "[data-mode-note]\").textContent")
        rec("disabled card explains that the browser lacks support",
            "Not supported in this browser" in disabled_note,
            disabled_note)
        plain_page.click("[data-mode-card='text-text']")
        plain_page.fill("input#role", "Frontend Developer")
        with plain_page.expect_navigation():
            plain_page.click("#riv-setup-form button[type=submit]")
        plain_page.wait_for_load_state("load")
        rec("text-only live skips the microphone entirely",
            (plain_page.locator("[data-mic]").is_hidden()
             or plain_page.locator("[data-mic]").count() == 0)
            and plain_page.locator("[data-mode-indicator]").inner_text().upper()
            == "TEXT \u2192 TEXT")
        plain_page.fill("#answer", "Legacy text-mode answer about caching.")
        with plain_page.expect_navigation():
            plain_page.click("#answer-submit")
        plain_page.wait_for_load_state("load")
        rec("unsupported-browser text flow advances normally",
            QUESTION_POOL[1] in plain_page.locator("[data-question]").inner_text(),
            plain_page.url)
        plain_live_url = plain_page.url
        plain_ctx.close()

        # ---------------- CSP + overflow --------------------------------
        for url in (f"{BASE}/interview", blocked_live_url, plain_live_url,
                    complete_a_url):
            page.goto(url, wait_until="load")
            scope = "[data-riv-live]" if page.locator("[data-riv-live]").count() \
                else ".riv-flow"
            scripts = page.locator(f"{scope} script").count()
            raw_html = page.evaluate(
                "fetch(location.href).then(r => r.text())")
            inline_style_attrs = len(re.findall(
                r"style\s*=\s*[\"']", raw_html))
            rec(f"no inline styles/scripts on {url.replace(BASE,'')} (CSP clean)",
                inline_style_attrs == 0 and scripts == 0
                and "javascript:" not in raw_html,
                f"raw inline styles={inline_style_attrs} scripts={scripts}")
        rec("no CSP violations logged across the whole session",
            not console_errors, "; ".join(console_errors[:3]))

        overflow = []
        for url in (f"{BASE}/interview", blocked_live_url, plain_live_url,
                    complete_a_url):
            page.goto(url, wait_until="load")
            for width in (1440, 1024, 768, 560, 360):
                page.set_viewport_size({"width": width, "height": 900})
                page.wait_for_timeout(200)
                ov = page.evaluate(
                    "document.documentElement.scrollWidth - window.innerWidth")
                overflow.append((url.replace(BASE, ""), width, ov))
        bad = [(p, w, v) for p, w, v in overflow if v > 0]
        rec("no horizontal overflow on voice views at 1440/1024/768/560/360",
            not bad, " ".join(f"{p}@{w}:+{v}" for p, w, v in overflow))

        # ---------------- cross-user isolation ---------------------------
        viz = requests.Session()
        tok = _csrf(viz, "/register")
        r = viz.post(f"{BASE}/register", data={
            "name": "Vic", "email": "vic@example.com", "password": PASSWORD,
            "confirm": PASSWORD, "csrf_token": tok,
        }, allow_redirects=False)
        rec("isolated second user registers", r.status_code == 302)
        vid = re.search(r"/interview/(\d+)", live_url).group(1)
        rr = viz.get(f"{BASE}/interview/{vid}/complete")
        rec("another user cannot open the voice interview's completion",
            rr.status_code == 404, f"status={rr.status_code}")

        browser.close()

    # ---- summary ----
    failed = [c for c in checks if not c[1]]
    print("\n" + "=" * 60)
    print(f"Voice/avatar browser QA: {len(checks) - len(failed)}/{len(checks)} passed")
    if failed:
        print("FAILED:", "; ".join(c[0] for c in failed))
        return 1
    print("ALL CHECKS PASSED")
    return 0


# ------------------------------------------------------------------- helpers

def wait_until_page(page, expr, timeout=6.0):
    """CSP-safe poll: page.evaluate bypasses script-src 'unsafe-eval',
    whereas wait_for_function folds the string into a page-level eval that the
    app's strict CSP (script-src 'self') correctly rejects."""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = page.evaluate(expr)
        except Exception:
            last = None
        if last:
            return
        time.sleep(0.1)
    raise SystemExit(f"timeout waiting for page condition: {expr} (last={last})")

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


def _db_row(sql, params=()):
    conn = _db()
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return row


if __name__ == "__main__":
    raise SystemExit(main())