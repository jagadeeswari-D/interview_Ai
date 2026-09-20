"""Stage 12 — final capstone QA regression tests.

Each test reproduces a concrete defect found during the Stage 12 full-product
audit and pins the minimal fix so it can never regress silently:

1. Smart Practice routes must reject Real Interview questions (a live
   interview's hints/evaluation would otherwise leak and a timed session
   could be completed early through the practice path).
2. The PDF report must escape `question_type`, the one dynamic value that
   skipped the escaping discipline applied to every other field.
3. Answer storage is idempotent: a duplicate/raced submission neither
   500s (IntegrityError) nor double-counts Performance/aggregation rows.
4. GET /ai/health (a real Gemini call) is covered by the per-user AI rate
   limit like every other AI-backed route.
5. Changing a password enforces the same strength rule as registration.
6. Daily Challenge completion is idempotent at the DB level.
7. An expired interview stuck on the recovery screen finalizes server-side.

Run:  .venv\\Scripts\\python.exe -m pytest interview_Ai/tests/test_stage12.py -q
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
from reportlab.platypus import Paragraph

SECRET_TEST_KEY = "test-secret-key"

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

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _csrf(html):
    match = CSRF_RE.search(html)
    if not match:
        raise AssertionError("No CSRF token found on page.")
    return match.group(1)


class Stage12TestBase(unittest.TestCase):
    """Isolated app with a fake Gemini transport and a temp SQLite DB."""

    def setUp(self):
        os.environ.pop("GEMINI_API_KEY", None)
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "stage12.db")
        self.calls = []
        self.fail_extract_once = False

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
        if "Connectivity check" in user:
            return json.dumps({"ok": True})
        if user.startswith("Create one interview question"):
            return json.dumps(dict(QUESTION_PAYLOAD))
        if "Extract the specific technologies" in user:
            if self.fail_extract_once:
                self.fail_extract_once = False
                raise GeminiRateLimitError(
                    429, "Gemini API error (HTTP 429): quota exceeded"
                )
            return json.dumps({
                "entities": [{"text": "Flask REST API", "kind": "project"}]
            })
        if "Evaluate this answer" in user:
            return json.dumps(dict(EVALUATION_PAYLOAD))
        if user.startswith("Original question:"):
            return json.dumps({
                "follow_up_question": "How did you design its data layer?",
                "reasoning": "internal only",
            })
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

    def _register_and_login(self, email="student@example.com",
                            password="password1"):
        page = self.client.get("/register")
        token = _csrf(page.get_data(as_text=True))
        self.client.post("/register", data={
            "name": "Test Student",
            "email": email,
            "password": password,
            "confirm": password,
            "csrf_token": token,
        })
        return email

    def _user_id(self):
        db = self._db()
        row = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        db.close()
        return row[0]

    def _start_real_interview(self):
        page = self.client.get("/interview")
        token = _csrf(page.get_data(as_text=True))
        return self.client.post("/interview/start", data={
            "role": "Backend Developer",
            "type": "technical",
            "difficulty": "easy",
            "csrf_token": token,
        })

    def _first_question_id(self):
        db = self._db()
        row = db.execute("SELECT MIN(id) FROM questions").fetchone()
        db.close()
        return row[0]

    def _db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


class PracticeRealIsolationTests(Stage12TestBase):
    """Smart Practice endpoints must not operate on Real Interview questions."""

    def test_live_real_question_is_rejected_from_practice_paths(self):
        self._register_and_login()
        response = self._start_real_interview()
        self.assertEqual(response.status_code, 302)
        question_id = self._first_question_id()

        view = self.client.get(f"/practice/question/{question_id}")
        self.assertEqual(view.status_code, 404)

        token = _csrf(self.client.get(
            "/practice").get_data(as_text=True))
        submit = self.client.post("/practice/answer", data={
            "question_id": str(question_id),
            "answer": "This must not be evaluated as practice.",
            "csrf_token": token,
        })
        self.assertEqual(submit.status_code, 404)

        db = self._db()
        interview = db.execute(
            "SELECT * FROM interviews ORDER BY id LIMIT 1").fetchone()
        answers = db.execute("SELECT COUNT(*) AS n FROM answers").fetchone()["n"]
        performance = db.execute(
            "SELECT COUNT(*) AS n FROM performance").fetchone()["n"]
        db.close()

        # The real interview is untouched: still live, ungraded, unanswered.
        self.assertEqual(interview["mode"], "real")
        self.assertEqual(interview["status"], "in_progress")
        self.assertEqual(answers, 0)
        self.assertEqual(performance, 0)

    def test_practice_question_flow_still_works_after_gate(self):
        self._register_and_login()
        page = self.client.get("/practice")
        token = _csrf(page.get_data(as_text=True))
        self.client.post("/practice/question", data={
            "topic": "SQL joins", "difficulty": "easy", "csrf_token": token,
        })
        question_id = self._first_question_id()

        view = self.client.get(f"/practice/question/{question_id}")
        self.assertEqual(view.status_code, 200)

        token = _csrf(self.client.get(
            f"/practice/question/{question_id}").get_data(as_text=True))
        submit = self.client.post("/practice/answer", data={
            "question_id": str(question_id),
            "answer": "Sorted keys enable binary search.",
            "csrf_token": token,
        })
        self.assertEqual(submit.status_code, 302)

        db = self._db()
        interview = db.execute(
            "SELECT * FROM interviews ORDER BY id LIMIT 1").fetchone()
        db.close()
        self.assertEqual(interview["mode"], "practice")
        self.assertEqual(interview["status"], "completed")


class ReportEscapingTests(unittest.TestCase):
    """The PDF report escapes question_type like every other dynamic value."""

    def test_question_type_is_escaped_in_paragraph_markup(self):
        from interview_Ai.app.reports import _escape, _styles

        question_type = "practical <b>bold</b> & notes"
        paragraph = Paragraph(
            f"Question 1 ({_escape(question_type)})", _styles()["question"]
        )
        plain = paragraph.getPlainText()
        # The markup characters survive as literal text instead of being
        # interpreted as bold/entity markup.
        self.assertIn("<b>bold</b> & notes", plain)

    def test_report_pdf_builds_with_hostile_question_type(self):
        from interview_Ai.app.reports import build_report_pdf

        transcript = [{
            "question": "Explain B-trees.",
            "question_type": "practical <b>bold</b> &",
            "user_answer": "Sorted keys.",
            "score": 70.0,
            "scores": {
                "technical_accuracy": 80, "relevance": 70, "completeness": 60,
                "clarity": 90, "communication": 50,
            },
            "feedback": "Good.",
            "missing_points": ["Mention page splits"],
            "model_answer": "Sorted keys enable O(log n) lookups.",
        }]
        interview = {
            "id": 1, "role": "Software Engineer", "mode": "practice",
            "type": "SQL joins", "date": "2026-01-01 10:00:00",
            "overall_score": 70.0,
        }
        pdf = build_report_pdf(
            interview, "Candidate", "SQL joins", transcript,
            {"technical_accuracy": 80.0}, None,
        )
        self.assertIsInstance(pdf, bytes)
        self.assertGreater(len(pdf), 1000)


class AnswerStorageIdempotencyTests(Stage12TestBase):
    """A duplicate/raced answer submission must not 500 or double-count."""

    def _answer_for(self, answer_text):
        return {
            "scores": dict(EVALUATION_PAYLOAD["scores"]),
            "feedback": "Feedback.",
            "missing_points": ["Mention splits"],
            "model_answer": "Model answer.",
        }

    def test_second_store_is_a_no_op_and_does_not_double_count(self):
        self._register_and_login()
        page = self.client.get("/practice")
        token = _csrf(page.get_data(as_text=True))
        self.client.post("/practice/question", data={
            "topic": "SQL joins", "difficulty": "easy", "csrf_token": token,
        })
        question_id = self._first_question_id()
        user_id = self._user_id()

        db = self._db()
        interview_id = db.execute(
            "SELECT id FROM interviews ORDER BY id LIMIT 1").fetchone()["id"]
        db.close()

        from interview_Ai.app.evaluation import store_evaluation

        with self.app.app_context():
            first = store_evaluation(
                question_id, "First attempt.", self._answer_for("First"),
                user_id=user_id, skill_label="SQL joins",
                interview_id=interview_id,
            )
            second = store_evaluation(
                question_id, "Second attempt.", self._answer_for("Second"),
                user_id=user_id, skill_label="SQL joins",
                interview_id=interview_id,
            )

        self.assertTrue(first[0])
        self.assertFalse(second[0])

        db = self._db()
        answers = db.execute("SELECT * FROM answers").fetchall()
        performance = db.execute("SELECT * FROM performance").fetchall()
        db.close()

        self.assertEqual(len(answers), 1)
        self.assertEqual(len(performance), 1)
        self.assertEqual(answers[0]["user_answer"], "First attempt.")

    def test_double_route_submission_does_not_re_evaluate(self):
        self._register_and_login()
        page = self.client.get("/practice")
        token = _csrf(page.get_data(as_text=True))
        self.client.post("/practice/question", data={
            "topic": "SQL joins", "difficulty": "easy", "csrf_token": token,
        })
        question_id = self._first_question_id()

        def submit():
            token = _csrf(self.client.get(
                f"/practice/question/{question_id}").get_data(as_text=True))
            return self.client.post("/practice/answer", data={
                "question_id": str(question_id),
                "answer": "Sorted keys enable binary search.",
                "csrf_token": token,
            })

        self.assertEqual(submit().status_code, 302)
        calls_after_first = len(self.calls)
        self.assertEqual(submit().status_code, 302)

        db = self._db()
        answers = db.execute("SELECT COUNT(*) AS n FROM answers").fetchone()["n"]
        db.close()
        self.assertEqual(answers, 1)
        self.assertEqual(len(self.calls), calls_after_first)


class HealthRateLimitTests(Stage12TestBase):
    """GET /ai/health consumes per-user AI limit tokens like other AI routes."""

    def setUp(self):
        super().setUp()
        from interview_Ai.app import ratelimit

        ratelimit._reset_for_tests()
        self.ratelimit = ratelimit
        self.app.config.update({
            "RATELIMIT_ENABLED": True,
            "RATELIMIT_AUTH_LIMIT": 5,
            "RATELIMIT_AUTH_WINDOW": 60,
            "RATELIMIT_GEMINI_LIMIT": 2,
            "RATELIMIT_GEMINI_WINDOW": 60,
        })

    def tearDown(self):
        self.ratelimit._reset_for_tests()
        super().tearDown()

    def test_health_hits_ai_rate_limit(self):
        self._register_and_login()
        self.assertEqual(self.client.get("/ai/health").status_code, 200)
        self.assertEqual(self.client.get("/ai/health").status_code, 200)
        third = self.client.get("/ai/health")
        self.assertEqual(third.status_code, 429)
        self.assertIn("Too many requests", third.get_data(as_text=True))


class ChangePasswordStrengthTests(Stage12TestBase):
    """A password change enforces the same rule as registration."""

    def _change_password(self, current, new_password, confirm):
        page = self.client.get("/settings")
        token = _csrf(page.get_data(as_text=True))
        return self.client.post("/settings/password", data={
            "current_password": current,
            "new_password": new_password,
            "confirm_password": confirm,
            "csrf_token": token,
        }, follow_redirects=True)

    def test_weak_new_password_is_rejected(self):
        self._register_and_login(password="password1")
        html = self._change_password(
            "password1", "onlyletters", "onlyletters"
        ).get_data(as_text=True)
        self.assertIn("at least one letter and one number", html)

        # Old password still works because nothing changed.
        body = self._login("student@example.com", "password1")
        self.assertIn("Welcome back", body)

    def test_strong_new_password_is_applied(self):
        self._register_and_login(password="password1")
        html = self._change_password(
            "password1", "newpass1", "newpass1"
        ).get_data(as_text=True)
        self.assertIn("Password updated", html)

        self.assertIn("Welcome back",
                      self._login("student@example.com", "newpass1"))
        self.assertIn("Invalid email or password",
                      self._login("student@example.com", "password1"))

    def _login(self, email, password):
        """Authenticate with a fresh client (isolated session)."""
        client = self.app.test_client()
        page = client.get("/login")
        token = _csrf(page.get_data(as_text=True))
        return client.post("/login", data={
            "email": email, "password": password, "csrf_token": token,
        }, follow_redirects=True).get_data(as_text=True)


class ChallengeDbIdempotencyTests(Stage12TestBase):
    """DB-level completion guard: the first stored answer always wins."""

    def test_completion_cannot_overwrite_an_existing_answer(self):
        self._register_and_login()
        user_id = self._user_id()

        from interview_Ai.app.models import (
            complete_daily_challenge,
            create_daily_challenge,
            get_daily_challenge_by_id,
        )

        with self.app.app_context():
            challenge = create_daily_challenge(
                user_id, "2026-01-02", "What is a hash index?",
                "conceptual", ["hash table", "O(1) lookup"],
            )
            complete_daily_challenge(challenge["id"], "First answer.")
            complete_daily_challenge(challenge["id"], "Second answer.")
            stored = get_daily_challenge_by_id(challenge["id"])

        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["answer"], "First answer.")


class ExpiredPendingInterviewTests(Stage12TestBase):
    """An expired session on the recovery screen finalizes server-side."""

    def test_expired_pending_interview_is_finalized(self):
        self._register_and_login()
        response = self._start_real_interview()
        self.assertEqual(response.status_code, 302)
        interview_id = self._db().execute(
            "SELECT MIN(id) AS iid FROM interviews").fetchone()["iid"]

        # The adaptive step fails once (rate limited) while answering Q1,
        # which leaves the interview on the recovery screen with no open
        # question.
        self.fail_extract_once = True
        page = self.client.get(f"/interview/{interview_id}")
        token = _csrf(page.get_data(as_text=True))
        answer = self.client.post(f"/interview/{interview_id}/answer", data={
            "answer": "Normalization removes redundancy.",
            "csrf_token": token,
        })
        self.assertEqual(answer.status_code, 200)  # pending recovery screen
        self.assertIn("retry below", answer.get_data(as_text=True).lower())

        db = self._db()
        row = db.execute(
            "SELECT status FROM interviews WHERE id = ?",
            (interview_id,),
        ).fetchone()
        db.close()
        self.assertEqual(row["status"], "in_progress")

        # Let the whole easy-interview window pass.
        db = self._db()
        db.execute(
            "UPDATE interviews SET date = datetime('now', '-30 minutes') "
            "WHERE id = ?", (interview_id,)
        )
        db.commit()
        db.close()

        response = self.client.get(f"/interview/{interview_id}")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.headers["Location"].endswith(
                f"/interview/{interview_id}/complete")
        )

        db = self._db()
        row = db.execute(
            "SELECT * FROM interviews WHERE id = ?", (interview_id,)
        ).fetchone()
        db.close()
        self.assertEqual(row["status"], "completed")
        self.assertIsNotNone(row["overall_score"])


if __name__ == "__main__":
    unittest.main()