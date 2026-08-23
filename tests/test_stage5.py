"""Offline tests for Stage 5: Personalization (blueprint K.9/K.10).

Covers Interview Replay (per-question navigation reconstructed purely from
stored Questions/Answers, ownership + in-progress guards, Real Mode
suppression) and the Personalized Learning Roadmap (weakness selection with
InterviewMemory prioritization, generate_roadmap contract reuse, persistence,
regeneration, progress toggling with CSRF, practice deep-links, isolation),
plus the additive/idempotent Stage 5 schema migration.

The Gemini transport is replaced with a fake, so these tests make NO real
API calls and do NOT need a GEMINI_API_KEY. All payloads are synthetic.

Run:  .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

import json
import os
import re
import sqlite3
import tempfile
import unittest

from interview_Ai.app.ai.errors import GeminiRateLimitError
from interview_Ai.app.ai.gemini import GeminiService
from interview_Ai.app.config import Config

SECRET_TEST_KEY = "test-secret-key"

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')

QUESTION_PAYLOAD = {
    "question": "Explain how database normalization reduces redundancy.",
    "question_type": "conceptual",
    "expected_concepts": ["1NF", "3NF"],
}

EVALUATION_PAYLOAD = {
    "scores": {
        "technical_accuracy": 80,
        "relevance": 70,
        "completeness": 60,
        "clarity": 90,
        "communication": 50,
    },
    "feedback": "Solid answer; you covered the basics well.",
    "missing_points": ["Mention update anomalies"],
    "model_answer": "Normalization structures tables to remove redundancy.",
}

WEAK_EVALUATION_PAYLOAD = {
    "scores": {
        "technical_accuracy": 45,
        "relevance": 55,
        "completeness": 40,
        "clarity": 35,
        "communication": 60,
    },
    "feedback": "This answer misses several fundamentals.",
    "missing_points": ["Define 2NF", "Give an anomaly example"],
    "model_answer": "Normalization removes redundancy step by step.",
}

ROADMAP_PAYLOAD_A = {
    "roadmap": [
        {"day": 2, "topic": "Second normal form",
         "practice_focus": "Spot partial dependencies."},
        {"day": 1, "topic": "Keys and candidate keys",
         "practice_focus": "Identify primary vs alternate keys."},
        {"day": 3, "topic": "Update anomalies",
         "practice_focus": "Rewrite tables to remove anomalies."},
    ],
}

ROADMAP_PAYLOAD_B = {
    "roadmap": [
        {"day": 1, "topic": "Index fundamentals",
         "practice_focus": "When does an index help?"},
        {"day": 2, "topic": "Query plans",
         "practice_focus": "Read a simple EXPLAIN output."},
    ],
}

ROADMAP_MARKER = "Build a day-by-day learning roadmap"
_QUESTION_PREFIX = "Create one interview question"
_EVALUATE_MARKER = "Evaluate this answer"

EXPECTED_WEAK_SKILLS = [
    "Clarity", "Completeness", "Database normalization",
    "Technical accuracy",
]


def _csrf(html):
    match = CSRF_RE.search(html)
    if not match:
        raise AssertionError("No CSRF token found on page.")
    return match.group(1)


class Stage5TestBase(unittest.TestCase):
    """Boots an isolated app with a fake Gemini service and a temp DB."""

    def setUp(self):
        os.environ.pop("GEMINI_API_KEY", None)
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "stage5_test.db")
        self.calls = []
        self.question_payload = dict(QUESTION_PAYLOAD)
        self.evaluation_payload = json.loads(json.dumps(EVALUATION_PAYLOAD))
        self.roadmap_payload = json.loads(json.dumps(ROADMAP_PAYLOAD_A))
        self.fail_roadmap_with = None

        from interview_Ai.app import create_app

        class TestConfig(Config):
            TESTING = True
            DATABASE_PATH = self.db_path

        self.app = create_app(TestConfig)
        self._install_service()
        self.client = self.app.test_client()

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Fake transport
    # ------------------------------------------------------------------

    def _fake_transport(self, system, user, temperature, max_output_tokens):
        self.calls.append(user)

        if ROADMAP_MARKER in user:
            if self.fail_roadmap_with is not None:
                raise self.fail_roadmap_with
            return json.dumps(self.roadmap_payload)

        if _EVALUATE_MARKER in user:
            return json.dumps(self.evaluation_payload)

        if user.startswith(_QUESTION_PREFIX):
            return json.dumps(self.question_payload)

        raise AssertionError(f"Unexpected prompt reached transport: {user[:80]}")

    def _install_service(self):
        service = GeminiService(
            api_key=SECRET_TEST_KEY, max_retries=0,
            transport=self._fake_transport,
        )
        self.app.extensions["gemini"] = service
        return service

    # ------------------------------------------------------------------
    # Client helpers
    # ------------------------------------------------------------------

    def _register_and_login(self, email="student@example.com"):
        page = self.client.get("/register")
        token = _csrf(page.get_data(as_text=True))
        self.client.post("/register", data={
            "name": "Test Student",
            "email": email,
            "password": "password1",
            "confirm": "password1",
            "csrf_token": token,
        })
        return email

    def _register_attacker(self, email="mallory@example.com"):
        attacker = self.app.test_client()
        page = attacker.get("/register")
        token = _csrf(page.get_data(as_text=True))
        attacker.post("/register", data={
            "name": "Mallory", "email": email,
            "password": "password1", "confirm": "password1",
            "csrf_token": token,
        })
        return attacker

    def _get_csrf(self, path):
        page = self.client.get(path)
        return _csrf(page.get_data(as_text=True))

    def _practice_ask(self, topic="Database normalization", difficulty="easy",
                      interview_id=None):
        token = self._get_csrf("/practice")
        data = {"topic": topic, "difficulty": difficulty,
                "csrf_token": token}
        if interview_id is not None:
            data["interview_id"] = str(interview_id)
        return self.client.post("/practice/question", data=data)

    def _practice_answer(self, question_id, answer="Normalization cuts duplication."):
        token = self._get_csrf(f"/practice/question/{question_id}")
        return self.client.post("/practice/answer", data={
            "question_id": str(question_id),
            "answer": answer,
            "csrf_token": token,
        })

    def _last_question_id(self):
        db = self._db()
        row = db.execute(
            "SELECT id FROM questions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        db.close()
        return row[0]

    def _first_interview_id(self):
        db = self._db()
        row = db.execute("SELECT MIN(id) FROM interviews").fetchone()
        db.close()
        return row[0]

    def _complete_practice_session(self, topic="Database normalization"):
        """One asked + answered practice question (session completes)."""
        self._practice_ask(topic=topic)
        return self._practice_answer(self._last_question_id())

    def _complete_weak_practice_session(self):
        """A session whose synthetic evaluation flags several weaknesses."""
        previous = self.evaluation_payload
        self.evaluation_payload = WEAK_EVALUATION_PAYLOAD
        try:
            self._complete_practice_session()
        finally:
            self.evaluation_payload = previous

    def _generate_roadmap(self):
        token = self._get_csrf("/roadmap")
        return self.client.post("/roadmap/generate", data={
            "csrf_token": token,
        })

    def _toggle_day(self, day_id, with_token=True):
        data = {}
        if with_token:
            data["csrf_token"] = self._get_csrf("/roadmap")
        return self.client.post(f"/roadmap/day/{day_id}/toggle", data=data)

    def _start_real(self):
        token = self._get_csrf("/interview")
        return self.client.post("/interview/start", data={
            "role": "Backend Developer", "type": "technical",
            "difficulty": "easy", "csrf_token": token,
        })

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def _db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _roadmap_rows(self):
        db = self._db()
        rows = db.execute(
            "SELECT * FROM roadmaps ORDER BY day_number ASC"
        ).fetchall()
        db.close()
        return [dict(row) for row in rows]

    def _roadmap_days_for_user(self, email):
        db = self._db()
        rows = db.execute(
            "SELECT r.* FROM roadmaps r JOIN users u ON u.id = r.user_id "
            "WHERE u.email = ? ORDER BY r.day_number ASC",
            (email,),
        ).fetchall()
        db.close()
        return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Item 9 — Interview Replay
# ---------------------------------------------------------------------------

class ReplayTests(Stage5TestBase):
    def setUp(self):
        super().setUp()
        self._register_and_login()

    def test_replay_requires_login(self):
        anonymous = self.app.test_client()
        response = anonymous.get("/replay")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_index_lists_completed_sessions_of_the_user(self):
        self._complete_practice_session(topic="Replayable topic")
        html = self.client.get("/replay").get_data(as_text=True)
        self.assertIn("Interview Replay", html)
        self.assertIn("Replayable topic", html)
        self.assertIn("Open replay", html)

    def test_walkthrough_reconstructs_stored_data_without_ai_calls(self):
        self._complete_practice_session()
        interview_id = self._first_interview_id()
        calls_before = list(self.calls)

        page = self.client.get(f"/replay/{interview_id}")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)

        # Everything comes from stored Questions/Answers...
        self.assertIn(QUESTION_PAYLOAD["question"], html)
        self.assertIn("Normalization cuts duplication.", html)
        self.assertIn(EVALUATION_PAYLOAD["feedback"], html)
        self.assertIn(EVALUATION_PAYLOAD["missing_points"][0], html)
        self.assertIn(EVALUATION_PAYLOAD["model_answer"], html)
        # ...and reconstruction makes ZERO new Gemini calls.
        self.assertEqual(self.calls, calls_before)

    def test_multi_question_navigation_prev_next_and_jump(self):
        self._practice_ask(topic="Navigation topic")
        first = self._last_question_id()
        interview_id = self._first_interview_id()
        self._practice_ask(topic="Navigation topic", interview_id=interview_id)
        second = self._last_question_id()
        self._practice_answer(first, "Answer to one.")
        self._practice_answer(second, "Answer to two.")

        page_q1 = self.client.get(f"/replay/{interview_id}")
        html_q1 = page_q1.get_data(as_text=True)
        self.assertIn("Question 1 of 2", html_q1)
        self.assertIn("Answer to one.", html_q1)
        self.assertNotIn("Answer to two.", html_q1)
        self.assertIn('rel="next"', html_q1)
        self.assertNotIn('rel="prev"', html_q1)

        page_q2 = self.client.get(f"/replay/{interview_id}?q=2")
        html_q2 = page_q2.get_data(as_text=True)
        self.assertIn("Question 2 of 2", html_q2)
        self.assertIn("Answer to two.", html_q2)
        self.assertIn('rel="prev"', html_q2)
        self.assertNotIn('rel="next"', html_q2)

        # Jump links let the user hop straight to any question.
        self.assertIn(f'href="/replay/{interview_id}?q=1"', html_q2)
        self.assertIn(f'href="/replay/{interview_id}?q=2"', html_q2)

    def test_out_of_range_position_clamps_to_the_nearest_end(self):
        self._complete_practice_session()
        interview_id = self._first_interview_id()

        low = self.client.get(f"/replay/{interview_id}?q=-5")
        self.assertIn("Question 1 of 1", low.get_data(as_text=True))
        high = self.client.get(f"/replay/{interview_id}?q=99")
        self.assertIn("Question 1 of 1", high.get_data(as_text=True))

    def test_ownership_protection(self):
        self._complete_practice_session()
        interview_id = self._first_interview_id()

        attacker = self._register_attacker()
        self.assertEqual(
            attacker.get(f"/replay/{interview_id}").status_code, 404
        )
        self.assertEqual(attacker.get("/replay").status_code, 200)
        self.assertNotIn(
            QUESTION_PAYLOAD["question"],
            attacker.get("/replay").get_data(as_text=True),
        )

    def test_in_progress_real_interview_redirects_to_live_view(self):
        self._start_real()
        interview_id = self._first_interview_id()

        response = self.client.get(f"/replay/{interview_id}")
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/interview/{interview_id}", response.headers["Location"])
        # Suppression intact: nothing about the hidden evaluation leaked.
        self.assertNotIn(
            EVALUATION_PAYLOAD["model_answer"],
            response.get_data(as_text=True),
        )

    def test_in_progress_practice_redirects_to_open_question(self):
        self._practice_ask(topic="Unfinished topic")
        interview_id = self._first_interview_id()
        question_id = self._last_question_id()

        response = self.client.get(f"/replay/{interview_id}")
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            f"/practice/question/{question_id}",
            response.headers["Location"],
        )

    def test_history_links_into_replay(self):
        self._complete_practice_session()
        interview_id = self._first_interview_id()
        html = self.client.get("/history").get_data(as_text=True)
        self.assertIn(f'href="/replay/{interview_id}"', html)


# ---------------------------------------------------------------------------
# Item 10 — Personalized Learning Roadmap
# ---------------------------------------------------------------------------

class RoadmapTests(Stage5TestBase):
    def setUp(self):
        super().setUp()
        self._register_and_login()

    def test_roadmap_requires_login(self):
        anonymous = self.app.test_client()
        response = anonymous.get("/roadmap")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_generate_without_weaknesses_makes_no_ai_call(self):
        response = self._generate_roadmap()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.calls, [])
        self.assertEqual(self._roadmap_rows(), [])

        html = self.client.get("/roadmap").get_data(as_text=True)
        self.assertIn("No weak spots detected yet", html)

    def test_generation_persists_contract_and_prioritizes_weaknesses(self):
        self._complete_weak_practice_session()
        self._generate_roadmap()

        rows = self._roadmap_rows()
        self.assertEqual(len(rows), 3)
        # Days arrive unsorted ({2, 1, 3}) and are normalized to 1..N in
        # the model's dependency order.
        self.assertEqual([row["day_number"] for row in rows], [1, 2, 3])
        self.assertEqual(rows[0]["topic"], "Keys and candidate keys")
        self.assertEqual(rows[1]["topic"], "Second normal form")
        self.assertEqual(rows[2]["topic"], "Update anomalies")
        for row in rows:
            self.assertEqual(row["status"], "pending")
            self.assertEqual(row["skill"], ", ".join(EXPECTED_WEAK_SKILLS))

        # The Section G contract was reused verbatim: the prompt carries the
        # weak_skills[] input worst-first (frequency DESC, then label ASC).
        roadmap_prompt = next(call for call in self.calls if ROADMAP_MARKER in call)
        self.assertIn(", ".join(EXPECTED_WEAK_SKILLS), roadmap_prompt)

        html = self.client.get("/roadmap").get_data(as_text=True)
        for topic in ("Keys and candidate keys", "Second normal form",
                      "Update anomalies"):
            self.assertIn(topic, html)
        for focus in ("Identify primary vs alternate keys.",
                      "Spot partial dependencies.",
                      "Rewrite tables to remove anomalies."):
            self.assertIn(focus, html)
        self.assertIn("Day 1", html)

    def test_repeated_mistakes_are_planned_first(self):
        from interview_Ai.app.memory import refresh_interview_memory
        from interview_Ai.app.models import record_weakness_events
        from interview_Ai.app import roadmap as roadmap_module

        with self.app.test_request_context():
            # "Frequent" out-ranks "Repeated" by raw frequency, but the
            # cross-session InterviewMemory signal must plan it first.
            record_weakness_events(1, {"Frequent": 5}, None)
            record_weakness_events(1, {"Repeated": 1}, None)
            record_weakness_events(1, {"Repeated": 1}, None)  # 2nd interview
            refresh_interview_memory(1)

            skills = roadmap_module._prioritized_weak_skills(1)

        self.assertEqual(skills[0], "Repeated")
        self.assertIn("Frequent", skills)

    def test_skill_selection_is_capped(self):
        from interview_Ai.app.models import record_weakness_events
        from interview_Ai.app import roadmap as roadmap_module

        with self.app.test_request_context():
            for index in range(8):
                record_weakness_events(1, {f"Skill {index}": 1}, None)
            skills = roadmap_module._prioritized_weak_skills(1)

        self.assertEqual(len(skills), roadmap_module.MAX_ROADMAP_SKILLS)

    def test_regeneration_replaces_plan_and_resets_progress(self):
        self._complete_weak_practice_session()
        self._generate_roadmap()
        first_day = self._roadmap_rows()[0]
        self._toggle_day(first_day["id"])
        self.assertEqual(
            self._roadmap_rows()[0]["status"], "completed"
        )

        previous = self.roadmap_payload
        self.roadmap_payload = ROADMAP_PAYLOAD_B
        try:
            self._generate_roadmap()
        finally:
            self.roadmap_payload = previous

        rows = self._roadmap_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["day_number"] for row in rows], [1, 2])
        self.assertTrue(all(row["status"] == "pending" for row in rows))
        html = self.client.get("/roadmap").get_data(as_text=True)
        self.assertIn("Index fundamentals", html)
        self.assertNotIn("Keys and candidate keys", html)

    def test_progress_toggle_round_trips(self):
        self._complete_weak_practice_session()
        self._generate_roadmap()
        day_id = self._roadmap_rows()[0]["id"]

        response = self._toggle_day(day_id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._roadmap_rows()[0]["status"], "completed")

        html = self.client.get("/roadmap").get_data(as_text=True)
        self.assertIn("Days completed", html)
        self.assertIn("1<span class=\"score-denominator\">/3</span>", html)

        response = self._toggle_day(day_id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._roadmap_rows()[0]["status"], "pending")

    def test_toggle_requires_csrf_token(self):
        self._complete_weak_practice_session()
        self._generate_roadmap()
        day_id = self._roadmap_rows()[0]["id"]

        response = self._toggle_day(day_id, with_token=False)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._roadmap_rows()[0]["status"], "pending")

    def test_toggle_isolation_between_users(self):
        self._complete_weak_practice_session()
        self._generate_roadmap()
        day_id = self._roadmap_rows()[0]["id"]

        attacker = self._register_attacker()

        # Toggle through the attacker's own client/session (fresh CSRF).
        attacker_page = attacker.get("/roadmap")
        token = _csrf(attacker_page.get_data(as_text=True))
        response = attacker.post(f"/roadmap/day/{day_id}/toggle",
                                 data={"csrf_token": token})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._roadmap_rows()[0]["status"], "pending")


class RoadmapErrorHandlingTests(Stage5TestBase):
    def setUp(self):
        super().setUp()
        self._register_and_login()
        self._complete_weak_practice_session()

    def test_unconfigured_service_shows_warning_and_persists_nothing(self):
        calls_before = len(self.calls)
        self.app.extensions["gemini"] = GeminiService(api_key="", max_retries=0)
        response = self._generate_roadmap()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._roadmap_rows(), [])
        # No new Gemini call was attempted for generation.
        self.assertEqual(len(self.calls), calls_before)
        self.assertIn(
            "GEMINI_API_KEY",
            self.client.get("/roadmap").get_data(as_text=True),
        )

    def test_rate_limited_generation_flashes_warning(self):
        self.fail_roadmap_with = GeminiRateLimitError(429, "slow down")
        response = self._generate_roadmap()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._roadmap_rows(), [])
        html = self.client.get("/roadmap").get_data(as_text=True)
        self.assertIn("rate limited", html)


class RoadmapToPracticeLinkTests(Stage5TestBase):
    def setUp(self):
        super().setUp()
        self._register_and_login()
        self._complete_weak_practice_session()
        self._generate_roadmap()

    def test_each_day_links_back_to_practice_with_topic_prefill(self):
        html = self.client.get("/roadmap").get_data(as_text=True)
        self.assertIn('href="/practice?topic=', html)

        row = self._roadmap_rows()[0]
        from urllib.parse import quote_plus
        self.assertIn(f"topic={quote_plus(row['topic'])}", html)

        picker = self.client.get(
            "/practice", query_string={"topic": row["topic"],
                                       "difficulty": "medium"}
        )
        self.assertEqual(picker.status_code, 200)
        picker_html = picker.get_data(as_text=True)
        self.assertIn(f'value="{row["topic"]}"', picker_html)
        self.assertIn("Practicing a roadmap topic", picker_html)
        self.assertIn('<option value="medium" selected>', picker_html)

    def test_invalid_prefill_falls_back_to_plain_picker(self):
        picker = self.client.get(
            "/practice", query_string={"topic": "x", "difficulty": "lunatic"}
        )
        html = picker.get_data(as_text=True)
        self.assertNotIn("Practicing a roadmap topic", html)
        self.assertIn('value=""', html)
        self.assertIn('<option value="medium" selected>', html)


# ---------------------------------------------------------------------------
# Cross-cutting: API-key hygiene
# ---------------------------------------------------------------------------

class KeyHygieneTests(Stage5TestBase):
    def test_api_key_never_appears_in_rendered_pages(self):
        self._register_and_login()
        self._complete_weak_practice_session()
        self._generate_roadmap()

        for path in ("/dashboard", "/history", "/replay", "/roadmap",
                     "/practice"):
            body = self.client.get(path).get_data(as_text=True)
            self.assertNotIn(SECRET_TEST_KEY, body)


# ---------------------------------------------------------------------------
# Stage 5 schema migration
# ---------------------------------------------------------------------------

class MigrationStage5Tests(unittest.TestCase):
    """Stage 4-era databases (no `roadmaps` table) migrate cleanly."""

    STAGE4_SCHEMA = """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL CHECK (length(trim(name)) > 0),
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            role TEXT NOT NULL DEFAULT '',
            skills TEXT NOT NULL DEFAULT '[]',
            resume_path TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE sessions (
            token TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_seen TEXT NOT NULL
        );
        CREATE TABLE interviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            mode TEXT NOT NULL CHECK (mode IN ('practice', 'real')),
            role TEXT NOT NULL DEFAULT '',
            difficulty TEXT NOT NULL DEFAULT '',
            type TEXT NOT NULL DEFAULT '',
            date TEXT NOT NULL DEFAULT (datetime('now')),
            overall_score REAL,
            status TEXT NOT NULL DEFAULT 'in_progress'
                       CHECK (status IN ('in_progress', 'completed')),
            question_limit INTEGER NOT NULL DEFAULT 0,
            duration_minutes INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            interview_id INTEGER NOT NULL REFERENCES interviews(id) ON DELETE CASCADE,
            question TEXT NOT NULL,
            question_type TEXT NOT NULL DEFAULT '',
            sequence_order INTEGER NOT NULL DEFAULT 1,
            expected_concepts TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL UNIQUE REFERENCES questions(id) ON DELETE CASCADE,
            user_answer TEXT NOT NULL,
            score REAL,
            scores TEXT NOT NULL DEFAULT '{}',
            feedback TEXT NOT NULL DEFAULT '',
            missing_points TEXT NOT NULL DEFAULT '[]',
            model_answer TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            skill TEXT NOT NULL,
            score REAL NOT NULL,
            date TEXT NOT NULL DEFAULT (datetime('now')),
            interview_id INTEGER REFERENCES interviews(id) ON DELETE CASCADE
        );
        CREATE TABLE weaknesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            skill TEXT NOT NULL,
            frequency INTEGER NOT NULL DEFAULT 0,
            interview_count INTEGER NOT NULL DEFAULT 0,
            last_interview_id INTEGER REFERENCES interviews(id) ON DELETE SET NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (user_id, skill)
        );
        CREATE TABLE interview_memory (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            strong_topics TEXT NOT NULL DEFAULT '[]',
            weak_topics TEXT NOT NULL DEFAULT '[]',
            repeated_mistakes TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """

    def setUp(self):
        os.environ.pop("GEMINI_API_KEY", None)
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "legacy_stage4.db")

        conn = sqlite3.connect(self.db_path)
        conn.executescript(self.STAGE4_SCHEMA)
        conn.execute(
            "INSERT INTO users (name, email, password_hash) "
            "VALUES ('Legacy User', 'legacy@example.com', 'x')"
        )
        conn.execute(
            "INSERT INTO interviews (user_id, mode, type, status) "
            "VALUES (1, 'practice', 'Legacy topic', 'completed')"
        )
        conn.execute(
            "INSERT INTO questions (interview_id, question) "
            "VALUES (1, 'Legacy question?')"
        )
        conn.execute(
            "INSERT INTO answers (question_id, user_answer, score) "
            "VALUES (1, 'Legacy answer', 62.5)"
        )
        conn.execute(
            "INSERT INTO performance (user_id, skill, score, interview_id) "
            "VALUES (1, 'Legacy topic', 62.5, 1)"
        )
        conn.execute(
            "INSERT INTO weaknesses (user_id, skill, frequency, "
            "interview_count) VALUES (1, 'Clarity', 3, 2)"
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def _create_app(self):
        from interview_Ai.app import create_app

        class LegacyConfig(Config):
            TESTING = True
            DATABASE_PATH = self.db_path

        return create_app(LegacyConfig)

    def test_migration_adds_roadmaps_and_preserves_all_stage14_data(self):
        self._create_app()   # migrates on startup

        conn = sqlite3.connect(self.db_path)
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(roadmaps)"
        )}
        counts = {
            table: conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in ("users", "interviews", "questions", "answers",
                          "performance", "weaknesses")
        }
        legacy_values = {
            "topic": conn.execute(
                "SELECT type FROM interviews WHERE id = 1"
            ).fetchone()[0],
            "answer_score": conn.execute(
                "SELECT score FROM answers WHERE id = 1"
            ).fetchone()[0],
            "weakness_frequency": conn.execute(
                "SELECT frequency FROM weaknesses WHERE id = 1"
            ).fetchone()[0],
        }
        conn.close()

        self.assertIn("roadmaps", tables)
        self.assertLessEqual(
            {"user_id", "skill", "day_number", "topic", "status",
             "created_at"}, columns,
        )
        self.assertEqual(counts, {
            "users": 1, "interviews": 1, "questions": 1, "answers": 1,
            "performance": 1, "weaknesses": 1,
        })
        self.assertEqual(legacy_values["topic"], "Legacy topic")       # untouched
        self.assertAlmostEqual(legacy_values["answer_score"], 62.5)   # untouched
        self.assertEqual(legacy_values["weakness_frequency"], 3)      # untouched

    def test_migration_is_idempotent_across_restarts(self):
        self._create_app()
        self._create_app()   # second boot on the same database

        conn = sqlite3.connect(self.db_path)
        columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(roadmaps)"
        )}
        day_count = conn.execute(
            "SELECT COUNT(*) FROM roadmaps"
        ).fetchone()[0]
        conn.close()

        self.assertIn("practice_focus", columns)
        self.assertEqual(day_count, 0)

    def test_migrated_database_serves_replay_and_roadmap_end_to_end(self):
        migrated_app = self._create_app()

        with migrated_app.test_client() as client:
            page = client.get("/register")
            token = _csrf(page.get_data(as_text=True))
            client.post("/register", data={
                "name": "Fresh User", "email": "fresh@example.com",
                "password": "password1", "confirm": "password1",
                "csrf_token": token,
            })
            self.assertEqual(client.get("/roadmap").status_code, 200)
            self.assertEqual(client.get("/replay").status_code, 200)
            self.assertEqual(client.get("/history").status_code, 200)

            # Sidebar entries activated for authenticated users.
            shell = client.get("/dashboard").get_data(as_text=True)
            self.assertIn('href="/replay"', shell)
            self.assertIn('href="/roadmap"', shell)


if __name__ == "__main__":
    unittest.main(verbosity=2)
