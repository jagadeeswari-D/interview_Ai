"""Offline tests for Stage 4: Intelligence Layer (blueprint K.7/K.8).

Covers the centralized AI Evaluation Engine (normalization, storage,
single-source dimensions), Weaknesses population, repeated-mistake
detection, Interview History list/detail views with ownership boundaries,
the InterviewMemory derived cache, and the Stage 4 additive schema
migration against a pre-existing database.

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
from interview_Ai.app import evaluation, interview

SECRET_TEST_KEY = "test-secret-key"

QUESTION_PAYLOAD = {
    "question": "Explain how database normalization reduces redundancy.",
    "question_type": "conceptual",
    "expected_concepts": ["1NF", "3NF"],
}

FOLLOW_UP_PAYLOAD = {
    "follow_up_question": (
        "You mentioned building a Flask REST API \u2014 how did you design "
        "its data layer?"
    ),
    "reasoning": "internal only",
}

ENTITIES_PAYLOAD = {
    "entities": [{"text": "Flask REST API", "kind": "project"}],
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

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')

# Prompt fingerprints used to route the fake transport (from prompts.py).
_EXTRACT_MARKER = "Extract the specific technologies"
_EVALUATE_MARKER = "Evaluate this answer"
_FOLLOW_UP_PREFIX = "Original question:"
_QUESTION_PREFIX = "Create one interview question"

DIMENSION_KEYS = (
    "technical_accuracy", "relevance", "completeness",
    "clarity", "communication",
)


def _csrf(html):
    match = CSRF_RE.search(html)
    if not match:
        raise AssertionError("No CSRF token found on page.")
    return match.group(1)


def _mean(scores):
    return round(sum(scores.values()) / len(scores), 1)


class EvaluationEngineUnitTests(unittest.TestCase):
    """Pure-function coverage of the centralized engine."""

    def test_normalize_scores_clamps_and_coerces(self):
        from interview_Ai.app.evaluation import normalize_scores

        normalized = normalize_scores({
            "technical_accuracy": 70,
            "relevance": 110,             # clamped high
            "completeness": -5,           # clamped low
            "clarity": 90,
            "communication": 50.4,
        })
        self.assertEqual(normalized["technical_accuracy"], 70.0)
        self.assertEqual(normalized["relevance"], 100.0)
        self.assertEqual(normalized["completeness"], 0.0)
        self.assertEqual(normalized["clarity"], 90.0)
        self.assertEqual(normalized["communication"], 50.4)

    def test_normalize_scores_rejects_bad_input(self):
        from interview_Ai.app.evaluation import normalize_scores

        with self.assertRaises(ValueError):
            normalize_scores({"technical_accuracy": 50})       # incomplete
        with self.assertRaises(ValueError):
            normalize_scores("not a dict")
        with self.assertRaises(ValueError):
            normalize_scores({key: 50 for key in DIMENSION_KEYS}
                             | {"clarity": True})              # bool rejected
        with self.assertRaises(ValueError):
            normalize_scores({key: 50 for key in DIMENSION_KEYS}
                             | {"clarity": "90"})              # non-numeric

    def test_mean_and_overall_normalization(self):
        from interview_Ai.app.evaluation import mean_score, overall_score

        self.assertIsNone(mean_score([]))
        self.assertIsNone(mean_score([None, None]))
        self.assertEqual(mean_score([70, None, 80]), 75.0)
        self.assertEqual(
            overall_score(EVALUATION_PAYLOAD["scores"]),
            _mean(EVALUATION_PAYLOAD["scores"]),
        )
        self.assertEqual(overall_score({k: 1 for k in DIMENSION_KEYS}), 1.0)

    def test_dimensions_are_single_sourced(self):
        """Routes must reuse the engine's rubric, not redefine their own."""
        from interview_Ai.app import practice

        self.assertIs(practice.SCORE_DIMENSIONS, evaluation.SCORE_DIMENSIONS)
        self.assertIs(interview.SCORE_DIMENSIONS, evaluation.SCORE_DIMENSIONS)


class Stage4TestBase(unittest.TestCase):
    """Boots an isolated app with a fake Gemini service and a temp DB."""

    def setUp(self):
        os.environ.pop("GEMINI_API_KEY", None)
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "stage4_test.db")
        self.calls = []
        self.question_payload = dict(QUESTION_PAYLOAD)
        self.evaluation_payload = json.loads(json.dumps(EVALUATION_PAYLOAD))

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

        if _EXTRACT_MARKER in user:
            return json.dumps(ENTITIES_PAYLOAD)

        if _EVALUATE_MARKER in user:
            return json.dumps(self.evaluation_payload)

        if user.startswith(_FOLLOW_UP_PREFIX):
            return json.dumps(FOLLOW_UP_PAYLOAD)

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

    def _get_csrf(self, path):
        page = self.client.get(path)
        return _csrf(page.get_data(as_text=True))

    def _practice_ask(self, topic="Database normalization", difficulty="easy"):
        token = self._get_csrf("/practice")
        return self.client.post("/practice/question", data={
            "topic": topic, "difficulty": difficulty, "csrf_token": token,
        })

    def _practice_answer(self, question_id, answer):
        token = self._get_csrf(f"/practice/question/{question_id}")
        return self.client.post("/practice/answer", data={
            "question_id": str(question_id),
            "answer": answer,
            "csrf_token": token,
        })

    def _practice_question_id(self):
        db = self._db()
        row = db.execute(
            "SELECT id FROM questions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        db.close()
        return row[0]

    def _start_real(self):
        token = self._get_csrf("/interview")
        return self.client.post("/interview/start", data={
            "role": "Backend Developer", "type": "technical",
            "difficulty": "easy", "csrf_token": token,
        })

    def _submit_real_answer(self, interview_id, answer):
        token = self._get_csrf(f"/interview/{interview_id}")
        return self.client.post(f"/interview/{interview_id}/answer", data={
            "answer": answer, "csrf_token": token,
        })

    def _first_interview_id(self):
        db = self._db()
        row = db.execute("SELECT MIN(id) FROM interviews").fetchone()
        db.close()
        return row[0]

    def _backdate(self, minutes):
        db = self._db()
        db.execute(
            "UPDATE interviews SET date = datetime('now', ?)",
            (f"-{minutes} minutes",),
        )
        db.commit()
        db.close()

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def _db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _memory(self):
        """The decoded InterviewMemory cache row for the first user."""
        db = self._db()
        row = db.execute(
            "SELECT m.* FROM interview_memory m "
            "JOIN users u ON u.id = m.user_id ORDER BY u.id LIMIT 1"
        ).fetchone()
        db.close()
        return self._decode_memory(row)

    def _memory_by_user(self, user_id):
        db = self._db()
        row = db.execute(
            "SELECT * FROM interview_memory WHERE user_id = ?", (user_id,)
        ).fetchone()
        db.close()
        return self._decode_memory(row)

    @staticmethod
    def _decode_memory(row):
        if row is None:
            return None
        memory = dict(row)
        memory["strong_topics"] = json.loads(memory["strong_topics"])
        memory["weak_topics"] = json.loads(memory["weak_topics"])
        memory["repeated_mistakes"] = json.loads(memory["repeated_mistakes"])
        return memory


class CentralizedEvaluationTests(Stage4TestBase):
    def test_practice_answer_stores_engine_result(self):
        self._register_and_login()
        self._practice_ask()
        response = self._practice_answer(
            self._practice_question_id(), "Normal forms reduce duplication."
        )
        self.assertEqual(response.status_code, 302)

        db = self._db()
        answer = db.execute("SELECT * FROM answers").fetchone()
        performance = db.execute("SELECT * FROM performance").fetchone()
        db.close()

        expected = _mean(self.evaluation_payload["scores"])
        self.assertAlmostEqual(answer["score"], expected)
        stored = json.loads(answer["scores"])
        self.assertEqual(sorted(stored), sorted(DIMENSION_KEYS))
        self.assertTrue(all(isinstance(v, float) for v in stored.values()))
        self.assertEqual(answer["feedback"],
                         self.evaluation_payload["feedback"])
        self.assertEqual(json.loads(answer["missing_points"]),
                         self.evaluation_payload["missing_points"])
        self.assertEqual(performance["skill"], "Database normalization")
        self.assertAlmostEqual(performance["score"], expected)

    def test_interview_answer_uses_the_same_engine(self):
        self._register_and_login()
        self._start_real()
        interview_id = self._first_interview_id()

        response = self._submit_real_answer(interview_id, "My best attempt.")
        self.assertEqual(response.status_code, 302)

        db = self._db()
        answer = db.execute("SELECT * FROM answers").fetchone()
        performance = db.execute("SELECT * FROM performance").fetchone()
        db.close()

        self.assertAlmostEqual(answer["score"],
                               _mean(self.evaluation_payload["scores"]))
        self.assertEqual(sorted(json.loads(answer["scores"]).keys()),
                         sorted(DIMENSION_KEYS))
        self.assertEqual(performance["skill"], "Technical")
        self.assertEqual(performance["interview_id"], interview_id)

    def test_concepts_travel_only_for_practice_prompts(self):
        self._register_and_login()
        self._practice_ask(topic="SQL joins & indexes")
        self._practice_answer(self._practice_question_id(),
                              "Indexes speed lookups.")

        evaluate_calls = [c for c in self.calls if _EVALUATE_MARKER in c]
        self.assertEqual(len(evaluate_calls), 1)
        self.assertIn("1NF", evaluate_calls[0])   # stored concepts included

        self.calls.clear()
        self._start_real()
        db = self._db()
        real_id = db.execute("SELECT MAX(id) FROM interviews").fetchone()[0]
        db.close()
        self._submit_real_answer(real_id, "Answering without hints.")

        evaluate_calls = [c for c in self.calls if _EVALUATE_MARKER in c]
        self.assertEqual(len(evaluate_calls), 1)
        self.assertNotIn("1NF", evaluate_calls[0])  # suppressed in real mode


class WeaknessDetectionTests(Stage4TestBase):
    def _weaks(self):
        db = self._db()
        rows = db.execute(
            "SELECT * FROM weaknesses ORDER BY skill"
        ).fetchall()
        db.close()
        return rows

    def test_low_dimension_creates_weakness_row(self):
        self.evaluation_payload["scores"]["relevance"] = 40
        self._register_and_login()
        self._practice_ask()
        self._practice_answer(self._practice_question_id(),
                              "Off-target answer.")

        rows = self._weaks()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["skill"], "Relevance")     # dimension label
        self.assertEqual(rows[0]["frequency"], 1)
        self.assertEqual(rows[0]["interview_count"], 1)
        self.assertIsNotNone(rows[0]["last_interview_id"])

    def test_low_overall_also_flags_topic_context(self):
        self.evaluation_payload["scores"] = {
            "technical_accuracy": 30, "relevance": 40, "completeness": 20,
            "clarity": 45, "communication": 35,
        }
        self._register_and_login()
        self._practice_ask(topic="REST API design")
        self._practice_answer(self._practice_question_id(), "No idea.")

        rows = self._weaks()
        skills = {row["skill"]: dict(row) for row in rows}

        # Every dimension below 50 plus the topic context itself.
        self.assertEqual(len(rows), 6)
        self.assertIn("REST API design", skills)
        self.assertIn("Technical accuracy", skills)
        self.assertIn("Completeness", skills)
        self.assertEqual(skills["REST API design"]["frequency"], 1)

    def test_strong_answers_leave_no_weaknesses(self):
        self.evaluation_payload["scores"] = {
            "technical_accuracy": 95, "relevance": 90, "completeness": 85,
            "clarity": 92, "communication": 88,
        }
        self._register_and_login()
        self._practice_ask()
        self._practice_answer(self._practice_question_id(), "Great answer.")

        self.assertEqual(len(self._weaks()), 0)
        memory = self._memory()
        self.assertIsNotNone(memory)
        self.assertEqual(memory["weak_topics"], [])
        self.assertEqual(memory["repeated_mistakes"], [])
        self.assertEqual(memory["strong_topics"], ["Database normalization"])

    def test_real_interview_completion_feeds_the_same_pipeline(self):
        self.evaluation_payload["scores"]["communication"] = 10
        self._register_and_login()
        self._start_real()
        interview_id = self._first_interview_id()
        self._submit_real_answer(interview_id, "Mumbled everything.")

        # Real Mode aggregates weaknesses when the session closes (the
        # evaluation stays hidden until then), so end it explicitly.
        token = self._get_csrf(f"/interview/{interview_id}")
        self.client.post(f"/interview/{interview_id}/finish",
                         data={"csrf_token": token})

        db = self._db()
        row = db.execute(
            "SELECT * FROM weaknesses WHERE skill = 'Communication'"
        ).fetchone()
        db.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["last_interview_id"], interview_id)
        self.assertIn("Communication", self._memory()["weak_topics"])


class RepeatedMistakeTests(Stage4TestBase):
    def test_two_sessions_flag_repeated_mistake(self):
        self.evaluation_payload["scores"]["clarity"] = 30
        self._register_and_login()

        self._practice_ask(topic="OOP principles")
        self._practice_answer(self._practice_question_id(), "Vague first try.")

        self._practice_ask(topic="Git workflows")
        self._practice_answer(self._practice_question_id(),
                              "Vague second try.")

        db = self._db()
        row = db.execute(
            "SELECT * FROM weaknesses WHERE skill = 'Clarity'"
        ).fetchone()
        db.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["frequency"], 2)
        self.assertEqual(row["interview_count"], 2)   # distinct sessions

        memory = self._memory()
        self.assertIn("Clarity", memory["repeated_mistakes"])
        self.assertIn("Clarity", memory["weak_topics"])

    def test_repeat_within_one_session_is_not_a_repeated_mistake(self):
        self.evaluation_payload["scores"]["relevance"] = 20
        self._register_and_login()

        self._practice_ask(topic="Time complexity")
        question_id = self._practice_question_id()
        self._practice_answer(question_id, "Drifting answer one.")

        # Retry inside the SAME practice session: the result view posts the
        # hidden interview_id back to /practice/question.
        interview_id = self._first_interview_id()
        token = self._get_csrf(f"/practice/question/{question_id}")
        self.client.post("/practice/question", data={
            "topic": "Time complexity", "difficulty": "easy",
            "interview_id": str(interview_id), "csrf_token": token,
        })
        self._practice_answer(self._practice_question_id(),
                              "Drifting answer two.")

        db = self._db()
        row = db.execute(
            "SELECT * FROM weaknesses WHERE skill = 'Relevance'"
        ).fetchone()
        db.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["frequency"], 2)         # both occurrences kept
        self.assertEqual(row["interview_count"], 1)   # one session only

        memory = self._memory()
        self.assertNotIn("Relevance", memory["repeated_mistakes"])
        self.assertIn("Relevance", memory["weak_topics"])

    def test_memory_cache_is_recomputed_not_accumulated(self):
        """Re-running the refresh never duplicates cache rows."""
        from interview_Ai.app.memory import refresh_interview_memory

        self.evaluation_payload["scores"]["completeness"] = 10
        self._register_and_login()
        self._practice_ask()
        self._practice_answer(self._practice_question_id(), "Incomplete.")

        db = self._db()
        user_id = db.execute("SELECT MIN(id) FROM users").fetchone()[0]
        db.close()

        with self.app.app_context():
            refresh_interview_memory(user_id)
            refresh_interview_memory(user_id)

        db = self._db()
        rows = db.execute("SELECT COUNT(*) FROM interview_memory").fetchone()[0]
        db.close()
        self.assertEqual(rows, 1)

        memory = self._memory_by_user(user_id)
        self.assertEqual(memory["weak_topics"].count("Completeness"), 1)


class HistoryTests(Stage4TestBase):
    def _complete_practice_session(self, topic="Database normalization"):
        self._practice_ask(topic=topic)
        self._practice_answer(self._practice_question_id(), "My answer.")

    def test_history_requires_login(self):
        response = self.client.get("/history")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].startswith("/login"))

    def test_empty_list_renders_guidance(self):
        self._register_and_login()
        response = self.client.get("/history")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("No completed sessions", html)
        self.assertIn("Interview memory", html)

    def test_completed_sessions_are_listed_with_scores(self):
        self._register_and_login()
        self._complete_practice_session(topic="SQL joins")

        html = self.client.get("/history").get_data(as_text=True)
        self.assertIn("SQL joins", html)
        self.assertIn("Practice", html)
        self.assertIn("70/100", html)                       # mean of fixture
        self.assertIn(f'href="/history/{self._first_interview_id()}"', html)
        self.assertNotIn(SECRET_TEST_KEY, html)

    def test_mode_filter_works(self):
        self._register_and_login()
        self._complete_practice_session(topic="SQL joins")

        html = self.client.get("/history?mode=real").get_data(as_text=True)
        self.assertIn("No completed sessions", html)
        html = self.client.get("/history?mode=practice").get_data(as_text=True)
        self.assertIn("SQL joins", html)
        html = self.client.get("/history?mode=bogus").get_data(as_text=True)
        self.assertIn("SQL joins", html)                    # invalid -> all

    def test_detail_renders_full_evaluation_for_completed_session(self):
        self._register_and_login()
        self._complete_practice_session()
        interview_id = self._first_interview_id()

        response = self.client.get(f"/history/{interview_id}")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(QUESTION_PAYLOAD["question"], html)
        self.assertIn("My answer.", html)
        self.assertIn(EVALUATION_PAYLOAD["feedback"], html)
        self.assertIn("Mention update anomalies", html)
        self.assertIn(EVALUATION_PAYLOAD["model_answer"], html)
        self.assertIn("Technical accuracy", html)
        self.assertNotIn(SECRET_TEST_KEY, html)

    def test_in_progress_sessions_redirect_instead_of_showing_results(self):
        self._register_and_login()
        self._practice_ask()                      # open question, no answer
        interview_id = self._first_interview_id()

        response = self.client.get(f"/history/{interview_id}")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/practice/question/", response.headers["Location"])

        # Real mode: an unfinished timed session redirects to the live view.
        self._start_real()
        db = self._db()
        real_id = db.execute(
            "SELECT MAX(id) FROM interviews"
        ).fetchone()[0]
        db.close()
        response = self.client.get(f"/history/{real_id}")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.headers["Location"].endswith(f"/interview/{real_id}")
        )

    def test_completed_real_interview_appears_after_expiry_finalize(self):
        self._register_and_login()
        self._start_real()
        interview_id = self._first_interview_id()
        self._submit_real_answer(interview_id, "Buzzer-beater answer.")
        self._backdate(13)                        # duration is 12 minutes

        response = self.client.get(f"/interview/{interview_id}")  # finalize
        self.assertEqual(response.status_code, 302)

        html = self.client.get("/history").get_data(as_text=True)
        self.assertIn("Real interview", html)
        self.assertIn("Backend Developer", html)

    def test_ownership_boundaries(self):
        self._register_and_login(email="alice@example.com")
        self._complete_practice_session(topic="Alice private topic")
        interview_id = self._first_interview_id()

        attacker = self.app.test_client()
        page = attacker.get("/register")
        token = _csrf(page.get_data(as_text=True))
        attacker.post("/register", data={
            "name": "Mallory", "email": "mallory@example.com",
            "password": "password1", "confirm": "password1",
            "csrf_token": token,
        })

        self.assertEqual(
            attacker.get(f"/history/{interview_id}").status_code, 404
        )
        attacker_html = attacker.get("/history").get_data(as_text=True)
        self.assertNotIn("Alice private topic", attacker_html)
        self.assertIn("No completed sessions", attacker_html)

        owner_html = self.client.get("/history").get_data(as_text=True)
        self.assertIn("Alice private topic", owner_html)


class InterviewMemoryIsolationTests(Stage4TestBase):
    def test_caches_are_per_user(self):
        self._register_and_login(email="carol@example.com")
        self._practice_ask()
        self._practice_answer(self._practice_question_id(), "Carol answer.")

        attacker = self.app.test_client()
        page = attacker.get("/register")
        token = _csrf(page.get_data(as_text=True))
        attacker.post("/register", data={
            "name": "Dave", "email": "dave@example.com",
            "password": "password1", "confirm": "password1",
            "csrf_token": token,
        })

        dave_html = attacker.get("/history").get_data(as_text=True)
        self.assertIn("Nothing analyzed yet", dave_html)

        db = self._db()
        dave_row = db.execute(
            "SELECT m.* FROM interview_memory m JOIN users u ON u.id = m.user_id "
            "WHERE u.email = 'dave@example.com'"
        ).fetchone()
        carol_rows = db.execute(
            "SELECT COUNT(*) FROM weaknesses w JOIN users u ON u.id = w.user_id "
            "WHERE u.email = 'dave@example.com'"
        ).fetchone()[0]
        db.close()

        # Dave's cache either doesn't exist yet or is empty; he sees none of
        # Carol's weaknesses either way.
        self.assertTrue(dave_row is None or (
            json.loads(dave_row["weak_topics"]) == []
            and json.loads(dave_row["repeated_mistakes"]) == []
        ))
        self.assertEqual(carol_rows, 0)


class MigrationTests(unittest.TestCase):
    """Stage 4 additions must apply cleanly to pre-existing databases."""

    LEGACY_SCHEMA = """
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
    """

    def setUp(self):
        os.environ.pop("GEMINI_API_KEY", None)
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "legacy_stage3.db")

        conn = sqlite3.connect(self.db_path)
        conn.executescript(self.LEGACY_SCHEMA)
        conn.execute(
            "INSERT INTO users (name, email, password_hash) "
            "VALUES ('Legacy User', 'legacy@example.com', 'x')"
        )
        conn.execute(
            "INSERT INTO interviews (user_id, mode, type, status) "
            "VALUES (1, 'practice', 'Legacy topic', 'completed')"
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

    def test_migration_adds_stage4_tables_and_preserves_data(self):
        self._create_app()   # migrates on startup

        conn = sqlite3.connect(self.db_path)
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        interviews = conn.execute(
            "SELECT COUNT(*) FROM interviews"
        ).fetchone()[0]
        legacy_topic = conn.execute(
            "SELECT type FROM interviews WHERE id = 1"
        ).fetchone()[0]
        conn.close()

        self.assertIn("weaknesses", tables)
        self.assertIn("interview_memory", tables)
        self.assertEqual(users, 1)
        self.assertEqual(interviews, 1)
        self.assertEqual(legacy_topic, "Legacy topic")   # untouched

    def test_migration_is_idempotent_across_restarts(self):
        self._create_app()
        self._create_app()   # second boot on the same database

        conn = sqlite3.connect(self.db_path)
        columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(weaknesses)"
        )}
        conn.close()
        self.assertIn("interview_count", columns)

    def test_migrated_database_serves_history_end_to_end(self):
        migrated_app = self._create_app()

        with migrated_app.test_client() as client:
            page = client.get("/register")
            token = _csrf(page.get_data(as_text=True))
            client.post("/register", data={
                "name": "Fresh User", "email": "fresh@example.com",
                "password": "password1", "confirm": "password1",
                "csrf_token": token,
            })
            page = client.get("/practice")
            token = _csrf(page.get_data(as_text=True))
            client.post("/practice/question", data={
                "topic": "HTTP & networking", "difficulty": "easy",
                "csrf_token": token,
            })
            page = client.get("/history")
            self.assertEqual(page.status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
