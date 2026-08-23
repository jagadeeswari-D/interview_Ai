"""Offline tests for Smart Practice Mode (Stage 2 Task 4).

The Gemini service bound to the app is replaced with a fake-transport
service, so these tests make NO real API calls and do NOT need a
GEMINI_API_KEY. All payloads are synthetic fixtures.

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

QUESTION_PAYLOAD = {
    "question": "Explain how a B-tree index speeds up queries.",
    "question_type": "conceptual",
    "expected_concepts": ["B-tree structure", "query plans"],
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
    "missing_points": ["Mention page splits", "Compare with hash indexes"],
    "model_answer": "A B-tree keeps keys sorted so lookups are O(log n).",
}

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _csrf(html):
    match = CSRF_RE.search(html)
    if not match:
        raise AssertionError("No CSRF token found on page.")
    return match.group(1)


def _mean(scores):
    return round(sum(scores.values()) / len(scores), 1)


class PracticeTestBase(unittest.TestCase):
    """Boots an isolated app with a fake Gemini service and a temp DB."""

    def setUp(self):
        os.environ.pop("GEMINI_API_KEY", None)
        self.tmp = tempfile.mkdtemp()
        self.calls = []
        self.transport_exception = None
        self.question_payload = dict(QUESTION_PAYLOAD)

        from interview_Ai.app import create_app

        class TestConfig(Config):
            TESTING = True
            DATABASE_PATH = os.path.join(self.tmp, "practice_test.db")

        self.db_path = TestConfig.DATABASE_PATH
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
        if self.transport_exception is not None:
            raise self.transport_exception
        if user.startswith("Create one interview question"):
            return json.dumps(self.question_payload)
        if "Evaluate this answer" in user:
            return json.dumps(EVALUATION_PAYLOAD)
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

    def _generate_question(self, topic="SQL joins", difficulty="easy"):
        page = self.client.get("/practice")
        token = _csrf(page.get_data(as_text=True))
        response = self.client.post("/practice/question", data={
            "topic": topic,
            "difficulty": difficulty,
            "csrf_token": token,
        })
        return response

    def _submit_answer(self, question_id, answer="It keeps keys sorted."):
        page = self.client.get(f"/practice/question/{question_id}")
        token = _csrf(page.get_data(as_text=True))
        return self.client.post("/practice/answer", data={
            "question_id": str(question_id),
            "answer": answer,
            "csrf_token": token,
        })

    def _first_question_id(self):
        db = sqlite3.connect(self.db_path)
        row = db.execute("SELECT MIN(id) FROM questions").fetchone()
        db.close()
        return row[0]

    def _db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


class AuthAndAccessTests(PracticeTestBase):
    def test_practice_requires_login(self):
        response = self.client.get("/practice")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].startswith("/login"))

    def test_picker_renders_with_role_fallback(self):
        self._register_and_login()
        page = self.client.get("/practice")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Choose a topic", html)
        self.assertIn("Software Engineer", html)


class QuestionGenerationTests(PracticeTestBase):
    def test_generate_question_persists_and_renders(self):
        self._register_and_login()
        response = self._generate_question(topic="SQL joins", difficulty="easy")
        self.assertEqual(response.status_code, 302)

        question_id = self._first_question_id()
        page = self.client.get(f"/practice/question/{question_id}")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn(QUESTION_PAYLOAD["question"], html)
        self.assertIn("B-tree structure", html)  # hint reveals concepts
        self.assertNotIn(SECRET_TEST_KEY, html)  # key never reaches the client

        db = self._db()
        interview = db.execute("SELECT * FROM interviews").fetchone()
        question = db.execute("SELECT * FROM questions").fetchone()
        db.close()

        self.assertIsNotNone(interview)
        self.assertEqual(interview["mode"], "practice")
        self.assertEqual(interview["difficulty"], "easy")
        self.assertEqual(interview["type"], "SQL joins")
        self.assertEqual(interview["status"], "in_progress")
        self.assertEqual(interview["role"], "Software Engineer")
        self.assertEqual(question["interview_id"], interview["id"])
        self.assertEqual(question["sequence_order"], 1)
        self.assertEqual(
            json.loads(question["expected_concepts"]),
            QUESTION_PAYLOAD["expected_concepts"],
        )
        # Prompt contract: topic and difficulty travel in the user turn.
        self.assertEqual(len(self.calls), 1)
        self.assertIn("SQL joins", self.calls[0])
        self.assertIn("easy difficulty", self.calls[0])

    def test_invalid_topic_makes_no_ai_call(self):
        self._register_and_login()
        page = self.client.get("/practice")
        token = _csrf(page.get_data(as_text=True))
        response = self.client.post("/practice/question", data={
            "topic": "S", "difficulty": "medium", "csrf_token": token,
        }, follow_redirects=True)
        self.assertIn("Topic must be between", response.get_data(as_text=True))

        response = self.client.post("/practice/question", data={
            "topic": "Valid topic", "difficulty": "impossible",
            "csrf_token": token,
        }, follow_redirects=True)
        self.assertIn("valid difficulty", response.get_data(as_text=True))

        db = self._db()
        count = db.execute("SELECT COUNT(*) FROM interviews").fetchone()[0]
        db.close()
        self.assertEqual(count, 0)
        self.assertEqual(len(self.calls), 0)

    def test_unconfigured_service_shows_warning(self):
        self._register_and_login()
        self.app.extensions["gemini"] = GeminiService(api_key="")
        response = self._generate_question()
        self.assertEqual(response.status_code, 302)

        page = self.client.get("/practice")
        self.assertIn("not configured", page.get_data(as_text=True))

        db = self._db()
        count = db.execute("SELECT COUNT(*) FROM interviews").fetchone()[0]
        db.close()
        self.assertEqual(count, 0)


class AnswerEvaluationTests(PracticeTestBase):
    def _flow_to_answered(self):
        self._register_and_login()
        self._generate_question(topic="REST API design")
        question_id = self._first_question_id()
        response = self._submit_answer(
            question_id, answer="Resources map to nouns and verbs to actions."
        )
        return question_id, response

    def test_evaluation_saves_result_everywhere(self):
        question_id, response = self._flow_to_answered()
        page = self.client.get(f"/practice/question/{question_id}")
        html = page.get_data(as_text=True)

        expected_overall = _mean(EVALUATION_PAYLOAD["scores"])
        self.assertEqual(page.status_code, 200)
        self.assertIn("Coach feedback", html)
        self.assertIn(EVALUATION_PAYLOAD["feedback"], html)
        self.assertIn(EVALUATION_PAYLOAD["model_answer"], html)
        self.assertIn("Mention page splits", html)
        self.assertIn(f">{int(expected_overall)}<", html)

        db = self._db()
        answer_row = db.execute("SELECT * FROM answers").fetchone()
        performance = db.execute("SELECT * FROM performance").fetchone()
        interview = db.execute("SELECT * FROM interviews").fetchone()
        db.close()

        self.assertAlmostEqual(answer_row["score"], expected_overall)
        self.assertEqual(
            json.loads(answer_row["missing_points"]),
            EVALUATION_PAYLOAD["missing_points"],
        )
        self.assertEqual(answer_row["user_answer"],
                         "Resources map to nouns and verbs to actions.")
        self.assertEqual(performance["skill"], "REST API design")
        self.assertAlmostEqual(performance["score"], expected_overall)
        self.assertEqual(performance["interview_id"], interview["id"])
        self.assertEqual(interview["status"], "completed")
        self.assertAlmostEqual(interview["overall_score"], expected_overall)

        # Second AI call evaluated the stored question + concepts.
        self.assertEqual(len(self.calls), 2)
        self.assertIn("Resources map to nouns", self.calls[1])
        self.assertIn("query plans", self.calls[1])

    def test_empty_answer_is_rejected_without_ai_call(self):
        self._register_and_login()
        self._generate_question()
        question_id = self._first_question_id()
        response = self._submit_answer(question_id, answer="")
        self.assertEqual(response.status_code, 302)

        page = self.client.get(f"/practice/question/{question_id}", follow_redirects=False)
        db = self._db()
        answers = db.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        db.close()
        self.assertEqual(answers, 0)
        self.assertEqual(len(self.calls), 1)  # only the generation call

    def test_double_submission_does_not_re_evaluate(self):
        question_id, _ = self._flow_to_answered()
        before = len(self.calls)
        response = self._submit_answer(question_id, answer="Second attempt.")
        self.assertEqual(response.status_code, 302)

        db = self._db()
        answers = db.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        db.close()
        self.assertEqual(answers, 1)
        self.assertEqual(len(self.calls), before)

    def test_rate_limit_on_evaluation_keeps_answer_safe_state(self):
        self._register_and_login()
        self._generate_question()
        question_id = self._first_question_id()
        self.transport_exception = GeminiRateLimitError(
            429, "Gemini API error (HTTP 429): quota exceeded"
        )
        response = self._submit_answer(question_id)
        self.assertEqual(response.status_code, 302)

        page = self.client.get(f"/practice/question/{question_id}")
        html = page.get_data(as_text=True)
        self.assertIn("rate limited", html)
        self.assertIn("Your answer</label>", html)  # still unanswered view

        db = self._db()
        answers = db.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        db.close()
        self.assertEqual(answers, 0)


class RetryTests(PracticeTestBase):
    def test_retry_appends_question_to_same_interview(self):
        self._register_and_login()
        self._generate_question(topic="OOP principles", difficulty="medium")
        question_id = self._first_question_id()
        self._submit_answer(question_id)

        db = self._db()
        interview_id = db.execute(
            "SELECT id FROM interviews"
        ).fetchone()[0]
        db.close()

        page = self.client.get(f"/practice/question/{question_id}")
        token = _csrf(page.get_data(as_text=True))
        response = self.client.post("/practice/question", data={
            "interview_id": str(interview_id),
            "topic": "OOP principles",
            "difficulty": "medium",
            "csrf_token": token,
        })
        self.assertEqual(response.status_code, 302)

        db = self._db()
        interviews = db.execute("SELECT COUNT(*) FROM interviews").fetchone()[0]
        questions = db.execute(
            "SELECT * FROM questions ORDER BY sequence_order"
        ).fetchall()
        status = db.execute(
            "SELECT status FROM interviews WHERE id = ?", (interview_id,)
        ).fetchone()[0]
        db.close()

        self.assertEqual(interviews, 1)
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[1]["sequence_order"], 2)
        self.assertEqual(status, "in_progress")


class OwnershipTests(PracticeTestBase):
    def test_other_user_cannot_access_question(self):
        self._register_and_login(email="alice@example.com")
        self._generate_question()
        question_id = self._first_question_id()

        db = self._db()
        interview_id = db.execute(
            "SELECT id FROM interviews"
        ).fetchone()[0]
        db.close()

        attacker = self.app.test_client()
        page = attacker.get("/register")
        token = _csrf(page.get_data(as_text=True))
        attacker.post("/register", data={
            "name": "Mallory",
            "email": "mallory@example.com",
            "password": "password1",
            "confirm": "password1",
            "csrf_token": token,
        })

        self.assertEqual(
            attacker.get(f"/practice/question/{question_id}").status_code, 404
        )

        page = attacker.get("/practice")
        token = _csrf(page.get_data(as_text=True))
        self.assertEqual(attacker.post("/practice/answer", data={
            "question_id": str(question_id),
            "answer": "hijack",
            "csrf_token": token,
        }).status_code, 404)

        self.assertEqual(attacker.post("/practice/question", data={
            "interview_id": str(interview_id),
            "topic": "Anything at all",
            "difficulty": "easy",
            "csrf_token": token,
        }).status_code, 404)

        db = self._db()
        answers = db.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        interviews = db.execute("SELECT COUNT(*) FROM interviews").fetchone()[0]
        db.close()
        self.assertEqual(answers, 0)
        self.assertEqual(interviews, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
