"""Offline tests for AI Real Interview Mode (Stage 3).

Covers the timed session lifecycle (config -> opening question -> sequential
Q&A -> completion screen), hint/model-answer suppression, the adaptive
follow-up chain (entity extraction, prioritization, depth adjustment, session
context), server-enforced timer expiry with auto-submit semantics, failure
recovery, ownership, and the Stage 3 schema migration.

The Gemini transport is replaced with a fake, so these tests make NO real API
calls and do NOT need a GEMINI_API_KEY. All payloads are synthetic fixtures.

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
    "question": "Explain how database normalization reduces redundancy.",
    "question_type": "conceptual",
    "expected_concepts": ["1NF", "3NF"],
}

FOLLOW_UP_PAYLOAD = {
    "follow_up_question": (
        "You mentioned building a Flask REST API — how did you design its "
        "data layer?"
    ),
    "reasoning": "internal only",
}

ENTITIES_PAYLOAD = {
    "entities": [
        {"text": "Flask REST API", "kind": "project"},
        {"text": "Python", "kind": "technology"},
        {"text": "improved performance a lot", "kind": "claim"},
    ]
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


def _csrf(html):
    match = CSRF_RE.search(html)
    if not match:
        raise AssertionError("No CSRF token found on page.")
    return match.group(1)


def _mean(scores):
    return round(sum(scores.values()) / len(scores), 1)


class InterviewTestBase(unittest.TestCase):
    """Boots an isolated app with a fake Gemini service and a temp DB."""

    def setUp(self):
        os.environ.pop("GEMINI_API_KEY", None)
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "interview_test.db")
        self.calls = []
        self.question_payload = dict(QUESTION_PAYLOAD)
        self.follow_up_payload = dict(FOLLOW_UP_PAYLOAD)
        self.entities_payload = json.loads(json.dumps(ENTITIES_PAYLOAD))
        self.evaluation_payload = json.loads(json.dumps(EVALUATION_PAYLOAD))
        self.fail_extract_once = False
        self.fail_evaluate_once = False

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
            if self.fail_extract_once:
                self.fail_extract_once = False
                raise GeminiRateLimitError(
                    429, "Gemini API error (HTTP 429): quota exceeded"
                )
            return json.dumps(self.entities_payload)

        if _EVALUATE_MARKER in user:
            if self.fail_evaluate_once:
                self.fail_evaluate_once = False
                raise GeminiRateLimitError(
                    429, "Gemini API error (HTTP 429): quota exceeded"
                )
            return json.dumps(self.evaluation_payload)

        if user.startswith(_FOLLOW_UP_PREFIX):
            return json.dumps(self.follow_up_payload)

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

    def _start(self, difficulty="easy", itype="technical",
               role="Backend Developer"):
        """Start a real interview; returns the response."""
        page = self.client.get("/interview")
        token = _csrf(page.get_data(as_text=True))
        return self.client.post("/interview/start", data={
            "role": role,
            "type": itype,
            "difficulty": difficulty,
            "csrf_token": token,
        })

    def _submit_answer(self, interview_id, answer):
        page = self.client.get(f"/interview/{interview_id}")
        token = _csrf(page.get_data(as_text=True))
        return self._answer_post(interview_id, answer, token)

    def _answer_post(self, interview_id, answer, token):
        """POST an answer with a pre-fetched token (needed once the live
        view stops rendering because the server-enforced timer expired)."""
        return self.client.post(f"/interview/{interview_id}/answer", data={
            "answer": answer,
            "csrf_token": token,
        })

    def _token_for(self, interview_id):
        page = self.client.get(f"/interview/{interview_id}")
        return _csrf(page.get_data(as_text=True))

    def _interview_id(self):
        db = self._db()
        row = db.execute("SELECT MIN(id) FROM interviews").fetchone()
        db.close()
        return row[0]

    def _db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _backdate(self, *modifiers):
        """Shift the interview start time into the past to simulate expiry."""
        db = self._db()
        args = list(modifiers) + [f"WHERE id = {self._interview_id()}"]
        db.execute(
            "UPDATE interviews SET date = datetime('now', "
            + ", ".join(f"'{m}'" for m in modifiers) + ") "
            "WHERE id = ?",
            (self._interview_id(),),
        )
        db.commit()
        db.close()


class ConfigAndAccessTests(InterviewTestBase):
    def test_interview_requires_login(self):
        response = self.client.get("/interview")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].startswith("/login"))

    def test_config_renders_with_role_fallback_and_budgets(self):
        self._register_and_login()
        page = self.client.get("/interview")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Configure your interview", html)
        self.assertIn("Software Engineer", html)
        self.assertIn("Behavioral", html)
        self.assertIn("4 questions", html)      # easy budget preview
        self.assertIn("No hints", html)

    def test_invalid_config_makes_no_ai_call(self):
        self._register_and_login()
        page = self.client.get("/interview")
        token = _csrf(page.get_data(as_text=True))

        response = self.client.post("/interview/start", data={
            "role": "Backend Developer", "type": "technical",
            "difficulty": "impossible", "csrf_token": token,
        }, follow_redirects=True)
        self.assertIn("valid difficulty", response.get_data(as_text=True))

        response = self.client.post("/interview/start", data={
            "role": "Backend Developer", "type": "speed-dating",
            "difficulty": "medium", "csrf_token": token,
        }, follow_redirects=True)
        self.assertIn("valid interview type", response.get_data(as_text=True))

        db = self._db()
        rows = db.execute("SELECT COUNT(*) FROM interviews").fetchone()[0]
        db.close()
        self.assertEqual(rows, 0)
        self.assertEqual(len(self.calls), 0)


class StartTests(InterviewTestBase):
    def test_start_creates_timed_session_with_opening_question(self):
        self._register_and_login()
        response = self._start(difficulty="easy", itype="technical")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].startswith("/interview/"))

        interview_id = self._interview_id()
        page = self.client.get(f"/interview/{interview_id}")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn(QUESTION_PAYLOAD["question"], html)
        self.assertIn("Question 1 of 4", html)
        self.assertIn('id="interview-timer"', html)
        self.assertRegex(html, r'data-seconds-remaining="\d+"')
        self.assertNotIn(SECRET_TEST_KEY, html)

        db = self._db()
        interview = db.execute("SELECT * FROM interviews").fetchone()
        question = db.execute("SELECT * FROM questions").fetchone()
        db.close()

        self.assertEqual(interview["mode"], "real")
        self.assertEqual(interview["role"], "Backend Developer")
        self.assertEqual(interview["difficulty"], "easy")
        self.assertEqual(interview["type"], "technical")
        self.assertEqual(interview["question_limit"], 4)
        self.assertEqual(interview["duration_minutes"], 12)
        self.assertEqual(interview["status"], "in_progress")
        self.assertEqual(question["interview_id"], interview["id"])
        self.assertEqual(question["sequence_order"], 1)

        # Prompt contract: role, topic seed, difficulty travel in user turn.
        self.assertEqual(len(self.calls), 1)
        self.assertIn("Backend Developer", self.calls[0])
        self.assertIn("technical fundamentals", self.calls[0])
        self.assertIn("easy difficulty", self.calls[0])

    def test_start_without_key_shows_warning_and_creates_nothing(self):
        self._register_and_login()
        self.app.extensions["gemini"] = GeminiService(api_key="")
        response = self._start()
        self.assertEqual(response.status_code, 302)

        page = self.client.get("/interview")
        self.assertIn("not configured", page.get_data(as_text=True))

        db = self._db()
        rows = db.execute("SELECT COUNT(*) FROM interviews").fetchone()[0]
        db.close()
        self.assertEqual(rows, 0)


class SuppressionTests(InterviewTestBase):
    def test_live_view_hides_concepts_feedback_and_model_answers(self):
        self._register_and_login()
        self._start()
        interview_id = self._interview_id()
        html = self.client.get(f"/interview/{interview_id}").get_data(as_text=True)

        # Stored concepts never render during a real interview...
        self.assertNotIn("1NF", html)
        self.assertNotIn("Need a hint", html)
        self.assertNotIn("<details", html)
        # ...and nothing evaluated exists yet anyway.
        self.assertNotIn(EVALUATION_PAYLOAD["feedback"], html)
        self.assertNotIn(EVALUATION_PAYLOAD["model_answer"], html)

    def test_completed_results_unlock_everything(self):
        self._register_and_login()
        self._start()
        interview_id = self._interview_id()
        self._submit_answer(interview_id, "I designed it around normal forms.")
        db = self._db()
        db.execute(
            "UPDATE interviews SET status = 'completed', overall_score = 70 "
            "WHERE id = ?", (interview_id,)
        )
        db.commit()
        db.close()

        html = self.client.get(
            f"/interview/{interview_id}/complete"
        ).get_data(as_text=True)
        self.assertIn(EVALUATION_PAYLOAD["feedback"], html)
        self.assertIn(EVALUATION_PAYLOAD["model_answer"], html)
        self.assertIn("Mention update anomalies", html)


class AdaptiveFlowTests(InterviewTestBase):
    def _flow_to_second_question(self):
        self._register_and_login()
        self._start()
        interview_id = self._interview_id()
        response = self._submit_answer(
            interview_id, "I built a Flask REST API with Python."
        )
        return interview_id, response

    def test_answer_triggers_extraction_selection_and_follow_up(self):
        interview_id, response = self._flow_to_second_question()
        self.assertEqual(response.status_code, 302)

        # Call order: opening question -> evaluation -> extraction ->
        # depth-adjusted follow-up.
        self.assertEqual(len(self.calls), 4)
        self.assertIn(_EVALUATE_MARKER, self.calls[1])
        self.assertIn("I built a Flask REST API", self.calls[1])
        self.assertIn(_EXTRACT_MARKER, self.calls[2])
        self.assertIn("Flask REST API", self.calls[3])   # selected entity
        self.assertIn(_FOLLOW_UP_PREFIX, self.calls[3])

        db = self._db()
        questions = db.execute(
            "SELECT * FROM questions ORDER BY sequence_order"
        ).fetchall()
        answer = db.execute("SELECT * FROM answers").fetchone()
        performance = db.execute("SELECT * FROM performance").fetchone()
        interview = db.execute("SELECT * FROM interviews").fetchone()
        db.close()

        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[1]["sequence_order"], 2)
        self.assertEqual(questions[1]["question_type"], "follow-up")
        self.assertEqual(
            questions[1]["question"], FOLLOW_UP_PAYLOAD["follow_up_question"]
        )
        expected_overall = _mean(EVALUATION_PAYLOAD["scores"])
        self.assertAlmostEqual(answer["score"], expected_overall)
        self.assertEqual(performance["skill"], "Technical")
        self.assertEqual(performance["interview_id"], interview["id"])
        self.assertEqual(interview["status"], "in_progress")

        page = self.client.get(f"/interview/{interview_id}")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Question 2 of 4", html)
        self.assertIn(FOLLOW_UP_PAYLOAD["follow_up_question"], html)
        # Previous evaluation stays hidden mid-interview.
        self.assertNotIn(EVALUATION_PAYLOAD["feedback"], html)

    def test_project_mentions_outrank_claims(self):
        self.entities_payload = {
            "entities": [
                {"text": "led a team of three", "kind": "claim"},
                {"text": "built a recommendation engine", "kind": "project"},
                {"text": "Docker", "kind": "technology"},
            ]
        }
        self._flow_to_second_question()
        self.assertIn("built a recommendation engine", self.calls[3])
        self.assertNotIn("led a team of three", self.calls[3])

    def test_low_technical_score_probes_foundational(self):
        self.evaluation_payload["scores"]["technical_accuracy"] = 30
        self._flow_to_second_question()
        self.assertIn("more foundational", self.calls[3])

    def test_high_technical_score_probes_deeper(self):
        self.evaluation_payload["scores"]["technical_accuracy"] = 90
        self._flow_to_second_question()
        self.assertIn("deeper, edge-case", self.calls[3])

    def test_mid_technical_score_keeps_standard_depth(self):
        self.evaluation_payload["scores"]["technical_accuracy"] = 60
        self._flow_to_second_question()
        self.assertIn("ask a natural follow-up", self.calls[3])
        self.assertNotIn("more foundational", self.calls[3])

    def test_session_context_travels_in_follow_up_prompt(self):
        self._register_and_login()
        self._start()
        interview_id = self._interview_id()
        self._submit_answer(interview_id, "First answer about normal forms.")
        self._submit_answer(interview_id, "Second answer about indexes.")

        second_follow_up = self.calls[-1]
        self.assertIn("Earlier in this same interview", second_follow_up)
        self.assertIn(QUESTION_PAYLOAD["question"], second_follow_up)
        self.assertIn("First answer about normal forms.", second_follow_up)

    def test_entityless_answer_falls_back_to_fresh_question(self):
        self.entities_payload = {"entities": []}
        self._register_and_login()
        self._start()
        interview_id = self._interview_id()
        response = self._submit_answer(interview_id, "Nothing to probe here.")
        self.assertEqual(response.status_code, 302)

        self.assertEqual(len(self.calls), 4)
        self.assertTrue(self.calls[3].startswith(_QUESTION_PREFIX))

        db = self._db()
        second = db.execute(
            "SELECT * FROM questions WHERE sequence_order = 2"
        ).fetchone()
        db.close()
        self.assertIsNotNone(second)
        self.assertEqual(second["question_type"],
                         QUESTION_PAYLOAD["question_type"])
        self.assertNotEqual(second["question_type"], "follow-up")

    def test_empty_answer_rejected_without_ai_call(self):
        self._register_and_login()
        self._start()
        interview_id = self._interview_id()
        response = self._submit_answer(interview_id, "")
        self.assertEqual(response.status_code, 302)

        page = self.client.get(f"/interview/{interview_id}")
        self.assertIn("Write your answer", page.get_data(as_text=True))

        db = self._db()
        answers = db.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        db.close()
        self.assertEqual(answers, 0)
        self.assertEqual(len(self.calls), 1)  # only the opening generation


class EvaluationFailureTests(InterviewTestBase):
    def test_rate_limited_evaluation_keeps_candidate_text(self):
        self._register_and_login()
        self._start()
        interview_id = self._interview_id()

        self.fail_evaluate_once = True
        page = self.client.post(
            f"/interview/{interview_id}/answer",
            data={"answer": "My kept draft answer.",
                  "csrf_token": self._token_for(interview_id)},
        )
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("rate limited", html)
        self.assertIn("My kept draft answer.", html)  # textarea preserved

        db = self._db()
        answers = db.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        db.close()
        self.assertEqual(answers, 0)
        # Opening generation + the failed evaluation attempt only.
        self.assertEqual(len(self.calls), 2)
        self.assertIn(_EVALUATE_MARKER, self.calls[1])


class RecoveryTests(InterviewTestBase):
    def test_adaptive_failure_shows_pending_screen_then_continue_works(self):
        self._register_and_login()
        self._start()
        interview_id = self._interview_id()

        self.fail_extract_once = True
        page = self._submit_answer(interview_id, "Answer one.")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Interview paused", html)
        self.assertIn("Try to continue", html)

        db = self._db()
        answers = db.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        status = db.execute(
            "SELECT status FROM interviews WHERE id = ?", (interview_id,)
        ).fetchone()[0]
        questions = db.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        db.close()
        self.assertEqual(answers, 1)          # the evaluation was saved
        self.assertEqual(status, "in_progress")
        self.assertEqual(questions, 1)        # no next question yet

        # Retry succeeds once the rate limit clears.
        page = self.client.get(f"/interview/{interview_id}")
        token = _csrf(page.get_data(as_text=True))
        response = self.client.post(f"/interview/{interview_id}/continue",
                                    data={"csrf_token": token})
        self.assertEqual(response.status_code, 302)

        page = self.client.get(f"/interview/{interview_id}")
        html = page.get_data(as_text=True)
        self.assertIn("Question 2 of 4", html)
        self.assertIn(FOLLOW_UP_PAYLOAD["follow_up_question"], html)

        db = self._db()
        questions = db.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        db.close()
        self.assertEqual(questions, 2)

    def test_finish_from_pending_completes_with_partial_results(self):
        self._register_and_login()
        self._start()
        interview_id = self._interview_id()

        self.fail_extract_once = True
        self._submit_answer(interview_id, "Answer one.")

        page = self.client.get(f"/interview/{interview_id}")
        token = _csrf(page.get_data(as_text=True))
        response = self.client.post(f"/interview/{interview_id}/finish",
                                    data={"csrf_token": token})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.headers["Location"].endswith("/complete")
        )

        db = self._db()
        row = db.execute("SELECT * FROM interviews WHERE id = ?",
                         (interview_id,)).fetchone()
        db.close()
        self.assertEqual(row["status"], "completed")
        self.assertAlmostEqual(row["overall_score"],
                               _mean(EVALUATION_PAYLOAD["scores"]))

    def test_finish_early_grades_only_answered_questions(self):
        self._register_and_login()
        self._start()
        interview_id = self._interview_id()
        self._submit_answer(interview_id, "Answer one.")

        page = self.client.get(f"/interview/{interview_id}")
        token = _csrf(page.get_data(as_text=True))
        response = self.client.post(f"/interview/{interview_id}/finish",
                                    data={"csrf_token": token})
        self.assertEqual(response.status_code, 302)

        html = self.client.get(
            f"/interview/{interview_id}/complete"
        ).get_data(as_text=True)
        self.assertIn("Interview complete", html)
        self.assertIn("Not answered", html)   # skipped question is labeled
        self.assertIn(EVALUATION_PAYLOAD["feedback"], html)


class TimerExpiryTests(InterviewTestBase):
    def test_expired_live_view_finalizes_interview(self):
        self._register_and_login()
        self._start()
        interview_id = self._interview_id()

        self._backdate("-13 minutes")  # duration is 12 minutes
        response = self.client.get(f"/interview/{interview_id}")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/complete"))

        db = self._db()
        row = db.execute("SELECT * FROM interviews WHERE id = ?",
                         (interview_id,)).fetchone()
        db.close()
        self.assertEqual(row["status"], "completed")
        self.assertIsNone(row["overall_score"])

    def test_auto_submit_within_grace_is_graded_then_finalized(self):
        self._register_and_login()
        self._start()
        interview_id = self._interview_id()

        token = self._token_for(interview_id)
        # ~40 seconds past the deadline: inside the auto-submit grace window.
        self._backdate("-12 minutes", "-40 seconds")
        response = self._answer_post(
            interview_id, "Auto-submitted as-is right at the buzzer.", token
        )
        self.assertEqual(response.status_code, 302)

        # Evaluated, stored, and finalized without generating a follow-up.
        self.assertEqual(len(self.calls), 2)
        self.assertIn(_EVALUATE_MARKER, self.calls[1])

        db = self._db()
        answers = db.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        row = db.execute("SELECT * FROM interviews WHERE id = ?",
                         (interview_id,)).fetchone()
        db.close()
        self.assertEqual(answers, 1)
        self.assertEqual(row["status"], "completed")

    def test_answer_beyond_grace_is_rejected(self):
        self._register_and_login()
        self._start()
        interview_id = self._interview_id()

        token = self._token_for(interview_id)
        self._backdate("-15 minutes")
        page = self._answer_post(interview_id, "Way too late.", token)
        self.assertEqual(page.status_code, 302)

        final = self.client.get(f"/interview/{interview_id}/complete")
        self.assertIn("too late", final.get_data(as_text=True))

        db = self._db()
        answers = db.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        row = db.execute("SELECT status FROM interviews WHERE id = ?",
                         (interview_id,)).fetchone()[0]
        db.close()
        self.assertEqual(answers, 0)
        self.assertEqual(row, "completed")

    def test_empty_auto_submit_at_expiry_finalizes_without_grading(self):
        self._register_and_login()
        self._start()
        interview_id = self._interview_id()

        token = self._token_for(interview_id)
        self._backdate("-13 minutes")
        response = self._answer_post(interview_id, "", token)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/complete"))

        db = self._db()
        answers = db.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        row = db.execute("SELECT * FROM interviews WHERE id = ?",
                         (interview_id,)).fetchone()
        db.close()
        self.assertEqual(answers, 0)
        self.assertEqual(row["status"], "completed")
        self.assertIsNone(row["overall_score"])


class CompletionTests(InterviewTestBase):
    def test_completes_at_question_limit_with_full_evaluation(self):
        self._register_and_login()
        self._start()
        interview_id = self._interview_id()

        for index in range(4):  # easy budget = 4 questions
            response = self._submit_answer(
                interview_id, f"Answer number {index + 1}."
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/complete"))

        db = self._db()
        row = db.execute("SELECT * FROM interviews WHERE id = ?",
                         (interview_id,)).fetchone()
        questions = db.execute(
            "SELECT COUNT(*) FROM questions WHERE interview_id = ?",
            (interview_id,),
        ).fetchone()[0]
        performances = db.execute(
            "SELECT COUNT(*) FROM performance WHERE interview_id = ?",
            (interview_id,),
        ).fetchone()[0]
        db.close()

        self.assertEqual(row["status"], "completed")
        self.assertAlmostEqual(row["overall_score"],
                               _mean(EVALUATION_PAYLOAD["scores"]))
        self.assertEqual(questions, 4)
        self.assertEqual(performances, 4)
        # Last call was an evaluation: nothing generated beyond the limit.
        self.assertIn(_EVALUATE_MARKER, self.calls[-1])

        html = self.client.get(
            f"/interview/{interview_id}/complete"
        ).get_data(as_text=True)
        self.assertIn("Interview complete", html)
        self.assertIn("Overall score", html)
        self.assertIn(EVALUATION_PAYLOAD["model_answer"], html)
        self.assertIn("Technical accuracy", html)

    def test_complete_redirects_to_live_while_in_progress(self):
        self._register_and_login()
        self._start()
        interview_id = self._interview_id()
        response = self.client.get(f"/interview/{interview_id}/complete")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            response.headers["Location"].endswith("/complete")
        )


class OwnershipTests(InterviewTestBase):
    def test_other_user_cannot_access_interview_routes(self):
        self._register_and_login(email="alice@example.com")
        self._start()
        interview_id = self._interview_id()

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
            attacker.get(f"/interview/{interview_id}").status_code, 404
        )
        self.assertEqual(
            attacker.get(f"/interview/{interview_id}/complete").status_code, 404
        )

        attacker_page = attacker.get("/practice")
        attacker_token = _csrf(attacker_page.get_data(as_text=True))
        self.assertEqual(attacker.post(
            f"/interview/{interview_id}/answer",
            data={"answer": "hijack", "csrf_token": attacker_token},
        ).status_code, 404)
        self.assertEqual(attacker.post(
            f"/interview/{interview_id}/continue",
            data={"csrf_token": attacker_token},
        ).status_code, 404)
        self.assertEqual(attacker.post(
            f"/interview/{interview_id}/finish",
            data={"csrf_token": attacker_token},
        ).status_code, 404)

        db = self._db()
        answers = db.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        db.close()
        self.assertEqual(answers, 0)

    def test_practice_interview_is_not_reachable_via_real_routes(self):
        self._register_and_login()
        page = self.client.get("/practice")
        token = _csrf(page.get_data(as_text=True))
        self.client.post("/practice/question", data={
            "topic": "SQL joins", "difficulty": "easy", "csrf_token": token,
        })
        practice_interview_id = self._interview_id()

        self.assertEqual(
            self.client.get(f"/interview/{practice_interview_id}").status_code,
            404,
        )


class MigrationTests(InterviewTestBase):
    def test_stage3_columns_added_to_pre_existing_database(self):
        from interview_Ai.app import create_app

        old_db_path = os.path.join(self.tmp, "legacy.db")
        conn = sqlite3.connect(old_db_path)
        conn.executescript(
            """
            CREATE TABLE interviews (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id),
                mode       TEXT NOT NULL,
                role       TEXT NOT NULL DEFAULT '',
                difficulty TEXT NOT NULL DEFAULT '',
                type       TEXT NOT NULL DEFAULT '',
                date       TEXT NOT NULL DEFAULT (datetime('now')),
                overall_score REAL,
                status     TEXT NOT NULL DEFAULT 'in_progress'
                           CHECK (status IN ('in_progress', 'completed'))
            );
            INSERT INTO interviews (user_id, mode) VALUES (1, 'practice');
            """
        )
        conn.commit()
        conn.close()

        class LegacyConfig(Config):
            TESTING = True
            DATABASE_PATH = old_db_path

        legacy_app = create_app(LegacyConfig)
        conn = sqlite3.connect(old_db_path)
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(interviews)")
        }
        conn.close()
        self.assertIn("question_limit", columns)
        self.assertIn("duration_minutes", columns)

        # The migrated database still serves the full Stage 3 flow.
        with legacy_app.test_client() as client:
            page = client.get("/register")
            token = _csrf(page.get_data(as_text=True))
            client.post("/register", data={
                "name": "Legacy User", "email": "legacy@example.com",
                "password": "password1", "confirm": "password1",
                "csrf_token": token,
            })
            page = client.get("/interview")
            token = _csrf(page.get_data(as_text=True))
            response = client.post("/interview/start", data={
                "role": "Data Analyst", "type": "behavioral",
                "difficulty": "medium", "csrf_token": token,
            })
            self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main(verbosity=2)
