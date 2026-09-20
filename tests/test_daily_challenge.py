"""Daily Challenge (Phase 9 / Stage 1) — focused coverage.

Exercises the server-authoritative challenge lifecycle: today's challenge is
created exactly once and stays stable across refreshes, completion is
persisted and idempotent, prior days never leak into today, cross-user access
is blocked (404), CSRF is enforced, invalid references fail safely, and the
DB constraints (one row per user per day, UNIQUE index, user FK) hold.

Reuses the stage-4 app/DB fixture (fake Gemini, temp SQLite) and the project
test conventions from test_bookmarks.py / test_stage4.py.
"""
import datetime
import json
import sqlite3
import unittest

from tests.test_stage4 import Stage4TestBase, _csrf


def _today():
    return datetime.date.today().isoformat()


def _yesterday():
    return (datetime.date.today() - datetime.timedelta(days=1)).isoformat()


class DailyChallengeTests(Stage4TestBase):
    """One logged-in user answering their own daily challenge."""

    def setUp(self):
        super().setUp()
        self._register_and_login()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _challenge_row(self, email="student@example.com"):
        db = self._db()
        rows = db.execute(
            "SELECT c.* FROM daily_challenges c "
            "JOIN users u ON u.id = c.user_id WHERE u.email = ?",
            (email,),
        ).fetchall()
        db.close()
        return [dict(row) for row in rows]

    def _open_challenge(self):
        """GET /challenge (creating the row) and return today's stored row."""
        self.client.get("/challenge")
        return self._challenge_row()[0]

    def _today_rows(self):
        now = _today()
        db = self._db()
        rows = db.execute(
            "SELECT COUNT(*) FROM daily_challenges WHERE challenge_date = ?",
            (now,),
        ).fetchone()[0]
        db.close()
        return rows

    def _answer(self, challenge_id, answer, client=None, with_token=True):
        data = {"challenge_id": str(challenge_id), "answer": answer}
        target = client or self.client
        if with_token:
            token = self._get_csrf("/challenge")
            data["csrf_token"] = token
        return target.post("/challenge/answer", data=data)

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

    @staticmethod
    def _csrf_from(html):
        return _csrf(html)

    # ------------------------------------------------------------------
    # Creation / stability / one-per-day
    # ------------------------------------------------------------------

    def test_challenge_requires_login(self):
        anonymous = self.app.test_client()
        response = anonymous.get("/challenge")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_page_creates_and_renders_today_challenge(self):
        response = self.client.get("/challenge")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Today's Challenge", html)
        self.assertIn("Open", html)
        self.assertIn(f'value="{self._challenge_row()[0]["id"]}"', html)
        self.assertIn('name="answer"', html)

        from interview_Ai.app.challenge import CHALLENGE_POOL
        questions = [q["question"] for q in CHALLENGE_POOL]
        self.assertIn(self._challenge_row()[0]["question"], questions)

    def test_refresh_returns_same_challenge_no_duplicate_row(self):
        first = self.client.get("/challenge").get_data(as_text=True)
        second = self.client.get("/challenge").get_data(as_text=True)
        rows = self._challenge_row()
        self.assertEqual(len(rows), 1)
        first_body = first.split("<p class=\"ch-question-text\">", 1)[1]
        second_body = second.split("<p class=\"ch-question-text\">", 1)[1]
        self.assertEqual(first_body, second_body)

    def test_one_challenge_per_user_per_day(self):
        for _ in range(5):
            self.client.get("/challenge")
        self.assertEqual(self._today_rows(), 1)
        self.assertEqual(len(self._challenge_row()), 1)

    def test_challenge_date_is_local_calendar_day(self):
        row = self._open_challenge()
        self.assertEqual(row["challenge_date"], _today())
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["answer"])
        self.assertIsNone(row["completed_at"])

    # ------------------------------------------------------------------
    # Completion lifecycle
    # ------------------------------------------------------------------

    def test_answer_completes_and_persists(self):
        challenge_id = self._open_challenge()["id"]
        response = self._answer(challenge_id, "Indexes trade write speed for faster reads.")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/challenge", response.headers["Location"])

        rows = self._challenge_row()
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(
            rows[0]["answer"], "Indexes trade write speed for faster reads."
        )
        self.assertIsNotNone(rows[0]["completed_at"])

        html = self.client.get("/challenge").get_data(as_text=True)
        self.assertIn("Challenge completed", html)
        self.assertIn("Indexes trade write speed for faster reads.", html)
        self.assertNotIn('name="answer"', html)

    def test_completed_state_survives_refresh(self):
        challenge_id = self._open_challenge()["id"]
        self._answer(challenge_id, "A stable answer.")
        before = self.client.get("/challenge").get_data(as_text=True)
        after = self.client.get("/challenge").get_data(as_text=True)
        self.assertIn("Challenge completed", before)
        self.assertIn("Challenge completed", after)
        self.assertIn("A stable answer.", after)

    def test_repeated_submission_is_idempotent(self):
        challenge_id = self._open_challenge()["id"]
        self._answer(challenge_id, "Original answer.")
        self._answer(challenge_id, "A second, different answer.")

        rows = self._challenge_row()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(rows[0]["answer"], "Original answer.")
        self.assertEqual(self._today_rows(), 1)

    def test_empty_answer_rejected_and_challenge_stays_pending(self):
        challenge_id = self._open_challenge()["id"]
        response = self._answer(challenge_id, "   ")
        self.assertEqual(response.status_code, 302)
        rows = self._challenge_row()
        self.assertEqual(rows[0]["status"], "pending")
        self.assertIsNone(rows[0]["answer"])

    def test_too_long_answer_rejected(self):
        challenge_id = self._open_challenge()["id"]
        response = self._answer(challenge_id, "x" * 5001)
        self.assertEqual(response.status_code, 302)
        rows = self._challenge_row()
        self.assertEqual(rows[0]["status"], "pending")
        self.assertIsNone(rows[0]["answer"])

    # ------------------------------------------------------------------
    # Date isolation
    # ------------------------------------------------------------------

    def test_previous_day_challenge_is_not_today_active(self):
        user_id = self._open_challenge()["user_id"]
        db = self._db()
        db.execute(
            "INSERT INTO daily_challenges "
            "(user_id, challenge_date, question, question_type, expected_concepts) "
            "VALUES (?, ?, 'Yesterday only question', 'conceptual', '[]')",
            (user_id, _yesterday()),
        )
        db.commit()
        db.close()

        html = self.client.get("/challenge").get_data(as_text=True)
        self.assertNotIn("Yesterday only question", html)

        rows = self._challenge_row()
        self.assertEqual(len(rows), 2)                 # yesterday + today
        today = next(r for r in rows if r["challenge_date"] == _today())
        self.assertIsNotNone(today)
        self.assertEqual(today["status"], "pending")

    def test_previous_day_challenge_cannot_be_submitted_today(self):
        user_id = self._open_challenge()["user_id"]
        db = self._db()
        db.execute(
            "INSERT INTO daily_challenges "
            "(user_id, challenge_date, question, question_type, expected_concepts) "
            "VALUES (?, ?, 'Yesterday question', 'conceptual', '[]')",
            (user_id, _yesterday()),
        )
        old_id = db.execute(
            "SELECT id FROM daily_challenges WHERE challenge_date = ?",
            (_yesterday(),),
        ).fetchone()[0]
        db.commit()
        db.close()

        response = self._answer(old_id, "Late answer.")
        self.assertEqual(response.status_code, 404)
        rows = self._challenge_row()
        stale = next(r for r in rows if r["challenge_date"] == _yesterday())
        self.assertEqual(stale["status"], "pending")

    # ------------------------------------------------------------------
    # Security / ownership
    # ------------------------------------------------------------------

    def test_invalid_and_missing_challenge_id_rejected(self):
        token = self.client.get("/challenge").get_data(as_text=True)
        token = self._csrf_from(token)
        missing = self.client.post("/challenge/answer", data={
            "challenge_id": "424242", "answer": "x", "csrf_token": token,
        })
        self.assertEqual(missing.status_code, 404)

        no_id = self.client.post("/challenge/answer", data={
            "answer": "x", "csrf_token": token,
        })
        self.assertEqual(no_id.status_code, 404)

        non_numeric = self.client.post("/challenge/answer", data={
            "challenge_id": "abc", "answer": "x", "csrf_token": token,
        })
        self.assertEqual(non_numeric.status_code, 404)

    def test_another_user_cannot_access_or_complete_my_challenge(self):
        challenge_id = self._open_challenge()["id"]
        attacker = self._register_attacker()

        attacker_page = attacker.get("/challenge").get_data(as_text=True)
        attacker_already_opened = self._csrf_from(attacker_page)
        # The attacker's own page must show only their own challenge.
        self.assertIn("Today's Challenge", attacker_page)

        token = self._csrf_from(attacker.get("/challenge").get_data(as_text=True))
        blocked = attacker.post("/challenge/answer", data={
            "challenge_id": str(challenge_id), "answer": "intrusion",
            "csrf_token": token,
        })
        self.assertEqual(blocked.status_code, 404)

        rows = self._challenge_row()
        self.assertEqual(rows[0]["status"], "pending")
        self.assertIsNone(rows[0]["answer"])

    def test_missing_csrf_token_rejected(self):
        challenge_id = self._open_challenge()["id"]
        response = self._answer(challenge_id, "No token.", with_token=False)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._challenge_row()[0]["status"], "pending")

    # ------------------------------------------------------------------
    # Schema / dashboard / nav
    # ------------------------------------------------------------------

    def test_schema_constraints_hold(self):
        db = self._db()
        try:
            columns = {
                row[1] for row in db.execute("PRAGMA table_info(daily_challenges)")
            }
            unique = {
                row[1] for row in
                db.execute("PRAGMA index_list(daily_challenges)")
                if row[2] == 1
            }
            fks = {
                (row[2], row[3]) for row in
                db.execute("PRAGMA foreign_key_list(daily_challenges)")
            }
        finally:
            db.close()
        self.assertEqual(
            columns,
            {"id", "user_id", "challenge_date", "question", "question_type",
             "expected_concepts", "status", "answer", "completed_at"},
        )
        self.assertEqual(
            unique,
            {"sqlite_autoindex_daily_challenges_1"},
            "UNIQUE(user_id, challenge_date) index must exist",
        )
        self.assertEqual(fks, {("users", "user_id")})

    def test_unique_constraint_rejects_duplicate_row_for_same_day(self):
        challenge_id = self._open_challenge()["id"]
        db = self._db()
        try:
            # Same user AND same challenge_date -> UNIQUE(user_id, challenge_date)
            # is violated at statement time, before any commit.
            db.execute(
                "INSERT INTO daily_challenges "
                "(user_id, challenge_date, question, question_type, "
                "expected_concepts) SELECT user_id, challenge_date, question, "
                "question_type, expected_concepts FROM daily_challenges WHERE id = ?",
                (challenge_id,),
            )
            self.fail("UNIQUE constraint should have rejected the duplicate row")
        except sqlite3.IntegrityError:
            pass
        finally:
            db.close()

    def test_sidebar_and_dashboard_integrate_challenge(self):
        shell = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn('href="/challenge"', shell)
        self.assertIn("Daily Challenge", shell)
        self.assertIn("No challenge opened yet today", shell)

        challenge_id = self._open_challenge()["id"]
        self._answer(challenge_id, "Dashboard reflects completion.")
        shell = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("iq-challenge-status is-done", shell)
        self.assertIn("View today", shell)
        self.assertNotIn("iq-challenge-status is-open", shell)

    def test_expected_concepts_round_trip_via_json(self):
        row = self._open_challenge()
        concepts = json.loads(row["expected_concepts"])
        self.assertIsInstance(concepts, list)
        self.assertTrue(concepts)
        html = self.client.get("/challenge").get_data(as_text=True)
        self.assertIn("Need a hint?", html)
        self.assertIn(f"<ul class=\"ch-concept-list\">", html)


if __name__ == "__main__":
    unittest.main()