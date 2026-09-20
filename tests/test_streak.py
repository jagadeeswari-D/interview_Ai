"""Daily Challenge streak (Phase 9 / Stage 2) — focused coverage.

The streak is a *server-authoritative* engagement metric derived ONLY from
completed Daily Challenges (status = 'completed' rows in daily_challenges).
There is no streak storage: the completion record is the single source of
truth and every value is recomputed per request on the server.

Part 1 tests `models.compute_streak` as a pure function with deterministic
dates (no clock). Part 2 seeds controlled daily_challenges rows relative to
the real local date and exercises `streak_summary`, the dashboard, ownership,
idempotency and FK cascade end-to-end. Reuses the stage-4 app/DB fixture.
"""
import datetime
import sqlite3
import unittest

from tests.test_stage4 import Stage4TestBase, _csrf

# Fixed reference point for the pure-function tests (never the machine clock).
TODAY = "2026-09-19"
YESTERDAY = "2026-09-18"
D_MINUS_2 = "2026-09-17"
D_MINUS_3 = "2026-09-16"
D_MINUS_4 = "2026-09-15"


def _day(offset, base=TODAY):
    d = datetime.date.fromisoformat(base) + datetime.timedelta(days=offset)
    return d.isoformat()


def _seed_challenge(conn, user_id, day, status="completed"):
    conn.execute(
        "INSERT INTO daily_challenges "
        "(user_id, challenge_date, question, question_type, expected_concepts, "
        "status) VALUES (?, ?, 'Streak seed question', 'conceptual', '[]', ?)",
        (user_id, day, status),
    )


def _real_today():
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# Part 1 — pure streak mathematics (deterministic, no clock, no database)
# ---------------------------------------------------------------------------

class StreakMathTests(unittest.TestCase):
    def _calc(self, days, today=TODAY):
        from interview_Ai.app.models import compute_streak
        return compute_streak(days, today)

    def test_no_completed_challenge_is_zero(self):
        s = self._calc([])
        self.assertEqual(s["current_streak"], 0)
        self.assertEqual(s["longest_streak"], 0)
        self.assertFalse(s["today_completed"])
        self.assertFalse(s["yesterday_completed"])

    def test_first_completed_challenge_today(self):
        s = self._calc([TODAY])
        self.assertEqual(s["current_streak"], 1)
        self.assertEqual(s["longest_streak"], 1)
        self.assertTrue(s["today_completed"])
        self.assertFalse(s["yesterday_completed"])

    def test_first_completed_challenge_yesterday(self):
        s = self._calc([YESTERDAY])
        self.assertEqual(s["current_streak"], 1)
        self.assertEqual(s["longest_streak"], 1)
        self.assertFalse(s["today_completed"])
        self.assertTrue(s["yesterday_completed"])

    def test_two_consecutive_days(self):
        s = self._calc([YESTERDAY, TODAY])
        self.assertEqual(s["current_streak"], 2)
        self.assertEqual(s["longest_streak"], 2)
        self.assertTrue(s["today_completed"])

    def test_three_consecutive_days(self):
        s = self._calc([D_MINUS_2, YESTERDAY, TODAY])
        self.assertEqual(s["current_streak"], 3)
        self.assertEqual(s["longest_streak"], 3)

    def test_missing_day_breaks_current_streak(self):
        # Day 1 (D-2) done, Day 2 (yesterday) missed, Day 3 (today) done.
        s = self._calc([D_MINUS_2, TODAY])
        self.assertEqual(s["current_streak"], 1)
        self.assertEqual(s["longest_streak"], 1)

    def test_new_completion_after_gap_starts_new_streak(self):
        s = self._calc([D_MINUS_3, YESTERDAY])   # gap day D-2
        self.assertEqual(s["current_streak"], 1)
        self.assertEqual(s["longest_streak"], 1)

    def test_historical_longest_streak_is_preserved(self):
        # Past run of 5 (D-12..D-8), gap, then a 2-day run ending yesterday.
        past = [_day(i) for i in (-12, -11, -10, -9, -8)]
        recent = [_day(i) for i in (-2, -1)]
        s = self._calc(past + recent)
        self.assertEqual(s["current_streak"], 2)
        self.assertEqual(s["longest_streak"], 5)

    def test_current_can_be_shorter_than_longest(self):
        s = self._calc([_day(-4), _day(-3), YESTERDAY])
        self.assertEqual(s["current_streak"], 1)    # yesterday only, D-2 missing
        self.assertEqual(s["longest_streak"], 2)    # D-4 + D-3

    def test_same_day_duplicates_do_not_inflate(self):
        s = self._calc([YESTERDAY, YESTERDAY, TODAY, TODAY])
        self.assertEqual(s["current_streak"], 2)
        self.assertEqual(s["longest_streak"], 2)

    def test_future_dated_rows_never_count(self):
        s = self._calc([TODAY, _day(1), _day(2)])
        self.assertEqual(s["current_streak"], 1)
        self.assertEqual(s["longest_streak"], 1)
        self.assertTrue(s["today_completed"])
        s_future_only = self._calc([_day(1), _day(2)])
        self.assertEqual(s_future_only["current_streak"], 0)
        self.assertEqual(s_future_only["longest_streak"], 0)

    def test_yesterday_continuity_when_today_missing(self):
        s = self._calc([D_MINUS_2, YESTERDAY])
        self.assertEqual(s["current_streak"], 2)
        self.assertFalse(s["today_completed"])
        self.assertTrue(s["yesterday_completed"])

    def test_month_boundary_consecutive(self):
        s = self._calc(["2026-09-30"], today="2026-10-01")
        self.assertEqual(s["current_streak"], 1)
        self.assertTrue(s["yesterday_completed"])

    def test_out_of_order_input_is_normalized(self):
        s = self._calc([TODAY, D_MINUS_2, YESTERDAY, YESTERDAY])
        self.assertEqual(s["current_streak"], 3)

    def test_no_strength_from_unrelated_interview_dates(self):
        # Interview activity alone (no completed challenge) contributes nothing.
        s = self._calc([], today=TODAY)
        self.assertEqual(s["current_streak"], 0)


# ---------------------------------------------------------------------------
# Part 2 — database + dashboard integration (controlled rows, real local day)
# ---------------------------------------------------------------------------

class StreakDbTests(Stage4TestBase):
    def setUp(self):
        super().setUp()
        self._register_and_login()
        db = self._db()
        try:
            user = db.execute("SELECT id FROM users LIMIT 1").fetchone()
        finally:
            db.close()
        self.user_id = user["id"]

    # -- helpers -----------------------------------------------------------

    def _seed(self, offsets, status="completed"):
        """Seed daily_challenges rows for the signed-in user at offsets
        (int days from real today); offset 0 means the current local day."""
        conn = self._db()
        for offset in offsets:
            _seed_challenge(conn, self.user_id, _day(offset, _real_today()), status)
        conn.commit()
        conn.close()

    def _answer_today(self, answer="A solid daily answer."):
        self.client.get("/challenge")
        db = self._db()
        challenge_id = db.execute(
            "SELECT id FROM daily_challenges WHERE user_id = ? "
            "AND challenge_date = ?",
            (self.user_id, _real_today()),
        ).fetchone()[0]
        db.close()
        token = self._get_csrf("/challenge")
        return self.client.post("/challenge/answer", data={
            "challenge_id": str(challenge_id),
            "answer": answer,
            "csrf_token": token,
        })

    def _streak_tile(self, html):
        start = html.find('data-icon="streak"')
        end = html.find("</article>", start)
        return html[start:end]

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

    def _completed_dates(self, user_id):
        from interview_Ai.app.models import completed_challenge_dates
        with self.app.app_context():
            return completed_challenge_dates(user_id)

    def _streak_summary(self, user_id):
        from interview_Ai.app.models import streak_summary
        with self.app.app_context():
            return streak_summary(user_id)

    # -- query / summary ---------------------------------------------------

    def test_completed_challenge_dates_excludes_pending_rows(self):
        self._seed([0, -1], status="completed")
        self._seed([-2], status="pending")
        from interview_Ai.app.models import completed_challenge_dates
        dates = self._completed_dates(self.user_id)
        self.assertEqual(dates, [_real_today(), _day(-1, _real_today())])

    def test_completed_challenge_dates_are_user_scoped(self):
        other = self._register_attacker()  # registers a second user
        self._seed([0, -1])
        conn = self._db()
        other_id = conn.execute(
            "SELECT id FROM users WHERE email = ?", ("mallory@example.com",)
        ).fetchone()[0]
        _seed_challenge(conn, other_id, _day(0, _real_today()))
        conn.commit()
        conn.close()
        from interview_Ai.app.models import completed_challenge_dates
        self.assertEqual(len(self._completed_dates(other_id)), 1)
        self.assertEqual(len(self._completed_dates(self.user_id)), 2)

    def test_streak_summary_three_day_run_ending_yesterday(self):
        self._seed([-1, -2, -3])
        from interview_Ai.app.models import streak_summary
        s = self._streak_summary(self.user_id)
        self.assertEqual(s["current_streak"], 3)
        self.assertEqual(s["longest_streak"], 3)
        self.assertFalse(s["today_completed"])
        self.assertTrue(s["yesterday_completed"])

    def test_streak_summary_includes_today_when_completed(self):
        self._seed([0, -1, -2])
        from interview_Ai.app.models import streak_summary
        s = self._streak_summary(self.user_id)
        self.assertEqual(s["current_streak"], 3)
        self.assertTrue(s["today_completed"])

    # -- dashboard ---------------------------------------------------------

    def test_dashboard_zero_streak_without_any_completion(self):
        self.client.get("/challenge")            # opening counts for nothing
        html = self.client.get("/dashboard").get_data(as_text=True)
        tile = self._streak_tile(html)
        self.assertIn("data-count=\"0\"", tile)
        self.assertIn("Answer today's challenge to start a streak", tile)

    def test_dashboard_shows_current_and_best_streak(self):
        self._seed([-1, -2] + list(range(-12, -7)))   # recent 2 + past 5
        html = self.client.get("/dashboard").get_data(as_text=True)
        tile = self._streak_tile(html)
        self.assertIn("data-count=\"2\"", tile)
        self.assertIn("2 days in a row", tile)
        self.assertIn("best 5", tile)

    def test_dashboard_singular_day_wording(self):
        self._seed([-1])
        html = self.client.get("/dashboard").get_data(as_text=True)
        tile = self._streak_tile(html)
        self.assertIn("1 day in a row", tile)
        self.assertNotIn("1 days", tile)

    def test_completing_todays_challenge_updates_dashboard_to_one(self):
        self._seed([-1])                          # yesterday gives continuity
        response = self._answer_today()
        self.assertEqual(response.status_code, 302)
        html = self.client.get("/dashboard").get_data(as_text=True)
        tile = self._streak_tile(html)
        self.assertIn("data-count=\"2\"", tile)
        self.assertIn("2 days in a row", tile)

    def test_same_day_repeat_completion_does_not_inflate(self):
        self._seed([-1])
        self._answer_today()
        self._answer_today("A second submission.")
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("data-count=\"2\"", self._streak_tile(html))
        conn = self._db()
        count = conn.execute(
            "SELECT COUNT(*) FROM daily_challenges WHERE user_id = ? "
            "AND status = 'completed'", (self.user_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(count, 2)                # yesterday + today only

    def test_dashboard_broken_streak(self):
        self._seed([0, -2, -3])                   # yesterday missed
        html = self.client.get("/dashboard").get_data(as_text=True)
        tile = self._streak_tile(html)
        self.assertIn("data-count=\"1\"", tile)
        self.assertIn("best 2", tile)

    def test_dashboard_values_are_server_authoritative(self):
        # A future-dated completed row must not inflate the shown streak.
        self._seed([0, -1])
        conn = self._db()
        _seed_challenge(conn, self.user_id, _day(1, _real_today()))
        conn.commit()
        conn.close()
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("data-count=\"2\"", self._streak_tile(html))

    # -- ownership / cascade ----------------------------------------------

    def test_another_user_does_not_affect_my_streak(self):
        self._register_attacker()
        self._seed([0, -1, -2, -3])
        conn = self._db()
        other_id = conn.execute(
            "SELECT id FROM users WHERE email = ?", ("mallory@example.com",)
        ).fetchone()[0]
        for offset in range(-10, 0):
            _seed_challenge(conn, other_id, _day(offset, _real_today()))
        conn.commit()
        conn.close()

        from interview_Ai.app.models import streak_summary
        mine = self._streak_summary(self.user_id)
        self.assertEqual(mine["current_streak"], 4)
        theirs = self._streak_summary(other_id)
        self.assertEqual(theirs["current_streak"], 10)

        # And the OTHER user's dashboard shows their own numbers (0), not mine.
        attacker = self.app.test_client()
        page = attacker.get("/login")
        token = self._csrf_from(page.get_data(as_text=True))
        attacker.post("/login", data={
            "email": "mallory@example.com", "password": "password1",
            "csrf_token": token,
        })
        html = attacker.get("/dashboard").get_data(as_text=True)
        start = html.find('data-icon="streak"')
        end = html.find("</article>", start)
        self.assertIn("data-count=\"10\"", html[start:end])
        self.assertIn("10 days in a row", html[start:end])

    def test_deleting_user_cascades_challenges_and_zeroes_streak(self):
        self._seed([0, -1, -2])
        conn = self._db()
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM users WHERE id = ?", (self.user_id,))
        conn.commit()
        conn.close()
        from interview_Ai.app.models import streak_summary
        self.assertEqual(self._streak_summary(self.user_id)["current_streak"], 0)
        self.assertEqual(self._completed_dates(self.user_id), [])

    # -- reminders / settings untouched ------------------------------------

    def test_settings_reminder_preferences_still_render(self):
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertIn('name="interview_reminders"', html)
        self.assertIn('name="progress_updates"', html)
        self.assertIn('name="practice_reminders"', html)


if __name__ == "__main__":
    unittest.main()