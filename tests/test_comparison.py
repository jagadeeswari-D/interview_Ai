"""Performance Comparison (Phase 8, Stage 4).

Exercises the comparability-aware comparison on the History detail page: the
previous session is selected only when it is the user's own, completed,
graded, and matches mode/role/difficulty/type; deltas are computed from
stored scores; unrelated sessions are never selected; and no comparison is
shown for the first session on a given setup.

Reuses the stage-4 app/DB fixture (fake Gemini, temp SQLite).
"""
import unittest

from tests.test_stage4 import DIMENSION_KEYS, Stage4TestBase


class ComparisonTests(Stage4TestBase):
    def setUp(self):
        super().setUp()
        self._register_and_login()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_scores(self, value):
        self.evaluation_payload["scores"] = {
            key: value for key in DIMENSION_KEYS
        }

    def _latest_interview_id(self):
        db = self._db()
        row = db.execute("SELECT MAX(id) FROM interviews").fetchone()
        db.close()
        return row[0]

    def _practice_session(self, topic, difficulty, score):
        """Create + complete one practice session with a uniform score."""
        self._set_scores(score)
        self._practice_ask(topic=topic, difficulty=difficulty)
        question_id = self._practice_question_id()
        self._practice_answer(question_id, f"Answer scored {score}.")
        return self._latest_interview_id()

    def _real_session(self, role="Backend Developer", difficulty="easy",
                      score=60):
        """Create + finalize one real-mode session with a uniform score."""
        self._set_scores(score)
        token = self._get_csrf("/interview")
        self.client.post("/interview/start", data={
            "role": role, "type": "technical", "difficulty": difficulty,
            "csrf_token": token,
        })
        interview_id = self._latest_interview_id()
        self._submit_real_answer(interview_id, f"Live answer scored {score}.")
        self._backdate(13)  # expire the timer so GET finalizes the session
        self.client.get(f"/interview/{interview_id}")
        return interview_id

    def _detail(self, interview_id):
        response = self.client.get(f"/history/{interview_id}")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    # ------------------------------------------------------------------
    # Presence / absence
    # ------------------------------------------------------------------

    def test_first_session_shows_no_comparison(self):
        interview_id = self._practice_session("SQL joins", "easy", 60)
        html = self._detail(interview_id)
        self.assertIn("Performance comparison", html)
        self.assertIn("No earlier completed session matches", html)
        self.assertNotIn("Previous session", html)

    def test_ungraded_session_has_no_comparison_panel(self):
        self._practice_session("SQL joins", "easy", 40)
        db = self._db()
        cur = db.execute(
            "INSERT INTO interviews (user_id, mode, role, difficulty, type, "
            "status) VALUES (1, 'practice', 'Software Engineer', 'easy', "
            "'SQL joins', 'completed')"
        )
        interview_id = cur.lastrowid
        db.execute(
            "INSERT INTO questions (interview_id, question, question_type, "
            "sequence_order) VALUES (?, 'Ungraded?', 'conceptual', 1)",
            (interview_id,),
        )
        db.commit()
        db.close()

        html = self._detail(interview_id)
        self.assertNotIn("Performance comparison", html)

    # ------------------------------------------------------------------
    # Deltas
    # ------------------------------------------------------------------

    def test_improvement_shows_positive_delta(self):
        self._practice_session("SQL joins", "easy", 40)
        second = self._practice_session("SQL joins", "easy", 80)
        html = self._detail(second)
        self.assertIn("Previous session", html)
        self.assertIn("+40.0", html)
        self.assertIn("is-up", html)

    def test_decline_shows_negative_delta(self):
        self._practice_session("SQL joins", "easy", 90)
        second = self._practice_session("SQL joins", "easy", 50)
        html = self._detail(second)
        self.assertIn("-40.0", html)
        self.assertIn("is-down", html)

    def test_equal_scores_show_flat_delta(self):
        self._practice_session("SQL joins", "easy", 70)
        second = self._practice_session("SQL joins", "easy", 70)
        html = self._detail(second)
        self.assertIn("is-flat", html)
        self.assertIn(">0.0<", html.replace(" ", "").replace("\n", ""))

    # ------------------------------------------------------------------
    # Comparable-session selection
    # ------------------------------------------------------------------

    def test_most_recent_comparable_session_is_used(self):
        self._practice_session("SQL joins", "easy", 40)
        self._practice_session("SQL joins", "easy", 60)
        third = self._practice_session("SQL joins", "easy", 80)
        html = self._detail(third)
        self.assertIn("+20.0", html)      # compared with 60, not 40
        self.assertNotIn("+40.0", html)

    def test_different_difficulty_is_not_comparable(self):
        self._practice_session("SQL joins", "easy", 40)
        second = self._practice_session("SQL joins", "hard", 80)
        html = self._detail(second)
        self.assertIn("No earlier completed session matches", html)

    def test_different_topic_is_not_comparable(self):
        self._practice_session("SQL joins", "easy", 40)
        second = self._practice_session("Data structures", "easy", 80)
        html = self._detail(second)
        self.assertIn("No earlier completed session matches", html)

    def test_more_recent_unrelated_session_is_skipped(self):
        self._practice_session("SQL joins", "easy", 40)
        self._practice_session("Data structures", "easy", 95)  # unrelated
        second = self._practice_session("SQL joins", "easy", 80)
        html = self._detail(second)
        self.assertIn("+40.0", html)       # SQL 40 -> 80
        self.assertNotIn("-15.0", html)    # would be the unrelated 95 session

    def test_real_mode_comparison_uses_matching_setup(self):
        self._real_session(role="Backend Developer", difficulty="easy",
                           score=50)
        same = self._real_session(role="Backend Developer", difficulty="easy",
                                  score=70)
        html = self._detail(same)
        self.assertIn("+20.0", html)

        other = self._real_session(role="Frontend Developer", difficulty="easy",
                                   score=90)
        other_html = self._detail(other)
        self.assertIn("No earlier completed session matches", other_html)

    # ------------------------------------------------------------------
    # Security / ownership
    # ------------------------------------------------------------------

    def test_other_users_session_is_never_selected(self):
        self._practice_session("SQL joins", "easy", 40)
        second = self._practice_session("SQL joins", "easy", 80)

        db = self._db()
        db.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            ("Other User", "other@example.com", "x"),
        )
        other_id = db.execute(
            "SELECT id FROM users WHERE email = 'other@example.com'"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO interviews (user_id, mode, role, difficulty, type, "
            "overall_score, status) VALUES (?, 'practice', "
            "'Software Engineer', 'easy', 'SQL joins', 100, 'completed')",
            (other_id,),
        )
        db.commit()
        db.close()

        html = self._detail(second)
        self.assertIn("+40.0", html)        # the user's own previous (40 -> 80)
        self.assertNotIn("-20.0", html)     # never the other user's 100


if __name__ == "__main__":
    unittest.main()
