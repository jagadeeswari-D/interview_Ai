"""Achievements / Badges (Phase 9 / Stage 3) — focused coverage.

The achievement feature is server-authoritative: every value is recomputed on
read from the activity tables (completed Daily Challenge days + completed &
graded interviews), there is no XP / points / leaderboard anywhere, and an
unlock is persisted exactly once (INSERT OR IGNORE on the UNIQUE
(user_id, achievement_key) constraint). The suite mirrors test_streak.py:

  * activity is seeded at controlled offsets relative to the real local day
    (future-dated rows must never count),
  * summary/evaluate calls pass an explicit `today` so nothing depends on the
    machine clock beyond the seeding offset,
  * the HTTP pages (/achievements gallery + dashboard panel) render the same
    `achievement_summary` structure and require login.

Achievement set under test (app/achievements.py, static definitions):
  first_challenge (1 day, flag) · streak_3 (3 days, flame) ·
  streak_7 (7 days, trophy) · challenge_10 (10 days, target) ·
  interviews_5 (5 graded interviews, briefcase)
"""
import datetime
import sqlite3
import re
import unittest

from tests.test_stage4 import Stage4TestBase, _csrf

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Achievement keys in the static definition order (order the gallery renders).
KEYS = [
    "first_challenge", "streak_3", "streak_7",
    "challenge_10", "interviews_5",
]
BY_KEY = {key: i for i, key in enumerate(KEYS)}


def _day(offset, base):
    d = datetime.date.fromisoformat(base) + datetime.timedelta(days=offset)
    return d.isoformat()


def _real_today():
    return datetime.date.today().isoformat()


def _seed_challenge(conn, user_id, day, status="completed"):
    conn.execute(
        "INSERT INTO daily_challenges "
        "(user_id, challenge_date, question, question_type, expected_concepts, "
        "status) VALUES (?, ?, 'Achievement seed question', 'conceptual', '[]', ?)",
        (user_id, day, status),
    )


def _seed_interview(conn, user_id, graded=True, mode="practice"):
    conn.execute(
        "INSERT INTO interviews (user_id, mode, role, difficulty, type, "
        "overall_score, status, question_limit, duration_minutes) "
        "VALUES (?, ?, 'Backend Developer', 'easy', 'technical', ?, ?, 3, 12)",
        (user_id, mode, 70.0 if graded else None,
         "completed" if graded else "in_progress"),
    )


class AchievementsBase(Stage4TestBase):
    def setUp(self):
        super().setUp()
        self._register_and_login()
        db = self._db()
        try:
            user = db.execute("SELECT id FROM users LIMIT 1").fetchone()
        finally:
            db.close()
        self.user_id = user["id"]

    # -- seeding helpers ---------------------------------------------------

    def _seed_days(self, offsets, status="completed"):
        """Seed completed challenge days at offsets (int) from real today."""
        conn = self._db()
        for offset in offsets:
            _seed_challenge(conn, self.user_id, _day(offset, _real_today()), status)
        conn.commit()
        conn.close()

    def _seed_interviews(self, count, graded=True, mode="practice"):
        conn = self._db()
        for _ in range(count):
            _seed_interview(conn, self.user_id, graded=graded, mode=mode)
        conn.commit()
        conn.close()

    # -- call helpers ------------------------------------------------------

    def _summary(self, user_id=None, today=None):
        from interview_Ai.app.achievements import achievement_summary
        with self.app.app_context():
            return achievement_summary(
                user_id or self.user_id, today or _real_today()
            )

    def _evaluate(self, user_id=None, today=None):
        from interview_Ai.app.achievements import evaluate_achievements
        with self.app.app_context():
            return evaluate_achievements(
                user_id or self.user_id, today or _real_today()
            )

    def _by_key(self, summary):
        return {entry["key"]: entry for entry in summary}

    def _unlocked_keys(self, summary):
        return [entry["key"] for entry in summary if entry["unlocked"]]

    def _persisted_rows(self):
        conn = self._db()
        try:
            rows = conn.execute(
                "SELECT achievement_key, unlocked_at FROM user_achievements "
                "WHERE user_id = ?", (self.user_id,)
            ).fetchall()
        finally:
            conn.close()
        return rows

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


class AchievementSummaryTests(AchievementsBase):
    """Progress / unlock semantics against seeded activity."""

    def test_gallery_defines_all_five_achievements(self):
        summary = self._summary()
        self.assertEqual(len(summary), 5)
        self.assertEqual([entry["key"] for entry in summary], KEYS)
        self.assertTrue(all(entry["unlocked"] is False for entry in summary))
        self.assertTrue(all(entry["progress"] == 0 for entry in summary))
        self.assertTrue(all(entry["unlocked_at"] is None for entry in summary))

    def test_thresholds_and_labels_are_as_documented(self):
        summary = self._by_key(self._summary())
        self.assertEqual(summary["first_challenge"]["threshold"], 1)
        self.assertEqual(summary["first_challenge"]["category"], "Challenge")
        self.assertEqual(summary["first_challenge"]["icon"], "flag")
        self.assertEqual(summary["streak_3"]["threshold"], 3)
        self.assertEqual(summary["streak_3"]["category"], "Streak")
        self.assertEqual(summary["streak_3"]["icon"], "flame")
        self.assertEqual(summary["streak_7"]["threshold"], 7)
        self.assertEqual(summary["streak_7"]["icon"], "trophy")
        self.assertEqual(summary["challenge_10"]["threshold"], 10)
        self.assertEqual(summary["challenge_10"]["icon"], "target")
        self.assertEqual(summary["interviews_5"]["threshold"], 5)
        self.assertEqual(summary["interviews_5"]["category"], "Practice")
        self.assertEqual(summary["interviews_5"]["icon"], "briefcase")

    def test_first_challenge_marks_unlocked_without_persisted_row(self):
        self._seed_days([0])
        summary = self._by_key(self._summary())
        # Reached the threshold, so the UI unlocks it even if no boundary
        # evaluation has run yet (server-authoritative read).
        self.assertTrue(summary["first_challenge"]["unlocked"])
        self.assertEqual(summary["first_challenge"]["progress"], 1)
        self.assertEqual(summary["first_challenge"]["percent"], 100)
        self.assertIsNone(summary["first_challenge"]["unlocked_at"])
        self.assertEqual(self._persisted_rows(), [])     # no eval yet

    def test_opening_the_challenge_page_alone_unlocks_nothing(self):
        self.client.get("/challenge")                    # creates a pending row
        summary = self._summary()
        self.assertTrue(all(entry["unlocked"] is False for entry in summary))
        self.assertTrue(all(entry["progress"] == 0 for entry in summary))

    def test_streak_thresholds_track_longest_run(self):
        self._seed_days([-3, -2, -1])                    # 3 consecutive, not today
        summary = self._by_key(self._summary())
        self.assertTrue(summary["streak_3"]["unlocked"])
        self.assertFalse(summary["streak_7"]["unlocked"])
        self.assertEqual(summary["streak_3"]["progress"], 3)
        self.assertEqual(summary["streak_7"]["progress"], 3)

    def test_seven_consecutive_days_unlocks_both_streak_badges(self):
        self._seed_days(list(range(-6, 1)))              # today + 6 back
        summary = self._by_key(self._summary())
        self.assertTrue(summary["streak_3"]["unlocked"])
        self.assertTrue(summary["streak_7"]["unlocked"])
        self.assertEqual(summary["streak_7"]["progress"], 7)
        self.assertEqual(summary["streak_7"]["percent"], 100)

    def test_ten_completed_days_unlocks_challenge_10(self):
        self._seed_days(list(range(-14, 1)))             # 15 days total
        summary = self._by_key(self._summary())
        self.assertTrue(summary["challenge_10"]["unlocked"])
        self.assertEqual(summary["challenge_10"]["percent"], 100)

    def test_interviews_require_completed_and_graded_sessions(self):
        self._seed_interviews(5)
        summary = self._by_key(self._summary())
        self.assertTrue(summary["interviews_5"]["unlocked"])
        self.assertEqual(summary["interviews_5"]["progress"], 5)
        self.assertEqual(summary["interviews_5"]["percent"], 100)

    def test_partial_interview_progress_stays_locked(self):
        self._seed_interviews(4)
        summary = self._by_key(self._summary())
        self.assertFalse(summary["interviews_5"]["unlocked"])
        self.assertEqual(summary["interviews_5"]["progress"], 4)
        self.assertEqual(summary["interviews_5"]["percent"], 80)

    def test_ungraded_or_in_progress_interviews_never_count(self):
        self._seed_interviews(3, graded=True)
        self._seed_interviews(2, graded=False)           # NULL score / in_progress
        summary = self._by_key(self._summary())
        self.assertFalse(summary["interviews_5"]["unlocked"])
        self.assertEqual(summary["interviews_5"]["progress"], 3)

    def test_pending_challenges_and_future_days_never_count(self):
        self._seed_days([0, -1])
        self._seed_days([-2], status="pending")
        self._seed_days([1, 2])                          # future-dated
        summary = self._by_key(self._summary())
        self.assertEqual(summary["challenge_10"]["progress"], 2)
        self.assertEqual(summary["first_challenge"]["progress"], 1)
        # Only today + yesterday are real: the 2-day streak must NOT reach
        # streak_3, and future/pending rows must not feed either counter.
        self.assertFalse(summary["streak_3"]["unlocked"])
        self.assertEqual(summary["streak_3"]["progress"], 2)
        self.assertEqual(summary["streak_7"]["progress"], 2)

    def test_same_day_duplicate_completions_do_not_inflate(self):
        # The schema enforces UNIQUE(user_id, challenge_date), so a second
        # same-day completion cannot even be stored; the counter therefore can
        # never double-count a day.
        conn = self._db()
        _seed_challenge(conn, self.user_id, _day(0, _real_today()))
        _seed_challenge(conn, self.user_id, _day(-1, _real_today()))
        conn.commit()
        try:
            _seed_challenge(conn, self.user_id, _day(0, _real_today()))
            self.fail("duplicate same-day completion should be rejected")
        except sqlite3.IntegrityError:
            pass
        conn.rollback()
        conn.close()
        summary = self._by_key(self._summary())
        self.assertEqual(summary["challenge_10"]["progress"], 2)
        self.assertEqual(summary["first_challenge"]["progress"], 1)

    def test_progress_is_capped_at_the_threshold(self):
        self._seed_days(list(range(-20, 1)))             # 21 completed days
        summary = self._by_key(self._summary())
        self.assertEqual(summary["challenge_10"]["progress"], 10)
        self.assertEqual(summary["challenge_10"]["percent"], 100)
        self.assertEqual(summary["first_challenge"]["progress"], 1)

    def test_interview_mode_is_irrelevant_to_the_counter(self):
        self._seed_interviews(3, mode="practice")
        self._seed_interviews(2, mode="real")
        summary = self._by_key(self._summary())
        self.assertTrue(summary["interviews_5"]["unlocked"])
        self.assertEqual(summary["interviews_5"]["progress"], 5)

    def test_other_users_activity_never_touches_my_badges(self):
        self._register_attacker()
        conn = self._db()
        other_id = conn.execute(
            "SELECT id FROM users WHERE email = ?", ("mallory@example.com",)
        ).fetchone()[0]
        for offset in range(-20, 1):
            _seed_challenge(conn, other_id, _day(offset, _real_today()))
        for _ in range(9):
            _seed_interview(conn, other_id)
        conn.commit()
        conn.close()

        mine = self._unlocked_keys(self._summary())
        theirs = self._unlocked_keys(self._summary(other_id))
        self.assertEqual(mine, [])
        self.assertEqual(theirs, KEYS)


class AchievementPersistenceTests(AchievementsBase):
    """evaluate_achievements idempotency, ordering and persistence."""

    def test_evaluation_persists_exactly_one_row_per_badge(self):
        self._seed_days([-2, -1, 0])
        new = self._evaluate()
        self.assertEqual(sorted(new), ["first_challenge", "streak_3"])
        self.assertEqual(sorted(self._unlocked_keys(self._summary())),
                         ["first_challenge", "streak_3"])

        rows = self._persisted_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(r["achievement_key"] for r in rows),
                         ["first_challenge", "streak_3"])
        for row in rows:
            self.assertIsNotNone(row["unlocked_at"])
            self.assertTrue(DATE_RE.match(row["unlocked_at"][:10]))

    def test_re_evaluation_is_idempotent_and_adds_no_rows(self):
        self._seed_days([0])
        self._evaluate()                                 # first_challenge
        rows_before = len(self._persisted_rows())
        out = self._evaluate()                           # same activity again
        self.assertEqual(sorted(out), ["first_challenge"])   # reports the badge
        self.assertEqual(len(self._persisted_rows()), rows_before)  # no dup row

    def test_unlock_date_never_rewrites_on_re_evaluation(self):
        self._seed_days([0, -1, -2])
        self._evaluate()
        stamp_before = {r["achievement_key"]: r["unlocked_at"]
                        for r in self._persisted_rows()}
        self._evaluate()                                 # same day, same activity
        stamp_after = {r["achievement_key"]: r["unlocked_at"]
                       for r in self._persisted_rows()}
        self.assertEqual(stamp_before, stamp_after)

    def test_monotonic_persistence_across_growing_activity(self):
        # Unlock order is a fixed sequence: once persisted it stays put.
        self._seed_days(list(range(-6, 1)))              # 7-day run
        self._evaluate()
        rows = self._persisted_rows()
        self.assertEqual(sorted(r["achievement_key"] for r in rows),
                         ["first_challenge", "streak_3", "streak_7"])
        for row in self._persisted_rows():               # nothing drifted
            self.assertIsNotNone(row["unlocked_at"])

    def test_summary_uses_persisted_unlock_date_when_row_exists(self):
        self._seed_days([0])
        self._evaluate()
        summary = self._by_key(self._summary())
        self.assertIsNotNone(summary["first_challenge"]["unlocked_at"])
        self.assertTrue(DATE_RE.match(summary["first_challenge"]["unlocked_at"]))

    def test_deleting_user_cascades_achievement_rows(self):
        self._seed_days([0, -1, -2])
        self._evaluate()
        conn = self._db()
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM users WHERE id = ?", (self.user_id,))
        conn.commit()
        left = conn.execute(
            "SELECT COUNT(*) FROM user_achievements WHERE user_id = ?",
            (self.user_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(left, 0)
        self.assertEqual(self._unlocked_keys(self._summary()), [])


class AchievementPageTests(AchievementsBase):
    """The /achievements gallery, the dashboard panel and the sidebar."""

    def test_achievements_page_requires_login(self):
        guest = self.app.test_client()
        response = guest.get("/achievements")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].startswith("/login"))

    def test_empty_gallery_renders_all_badges_locked(self):
        html = self.client.get("/achievements").get_data(as_text=True)
        self.assertEqual(self.client.get("/achievements").status_code, 200)
        self.assertIn("Your badges", html)
        self.assertIn("0 of 5 unlocked", html)
        for title in ("First Challenge", "3-Day Streak", "7-Day Streak",
                      "10 Challenges", "5 Interviews"):
            self.assertIn(title, html)
        self.assertEqual(html.count("ach-card is-locked"), 5)
        self.assertEqual(html.count("ach-card is-unlocked"), 0)

    def test_gallery_shows_unlock_dates_and_locked_progress(self):
        self._seed_days([0])
        self._seed_interviews(2)
        self._evaluate()
        html = self.client.get("/achievements").get_data(as_text=True)
        self.assertIn("1 of 5 unlocked", html)
        self.assertEqual(html.count("ach-card is-unlocked"), 1)
        self.assertEqual(html.count("ach-card is-locked"), 4)
        self.assertIn("Unlocked &middot; ", html)        # persisted date badge
        self.assertIn("2 / 5", html)                     # interviews_5 progress
        self.assertIn('data-w="40"', html)               # 2/5 -> 40%

    def test_sidebar_shows_achievements_nav_and_active_state(self):
        html = self.client.get("/achievements").get_data(as_text=True)
        self.assertIn('href="/achievements"', html)
        self.assertIn('class="nav-link active"', html)
        dashboard = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn('href="/achievements"', dashboard)  # dashboard panel link

    def test_dashboard_panel_renders_icon_macro_and_progress(self):
        self._seed_days([-1, -2] + list(range(-12, -7)))  # streak 5 + recent 2
        self._seed_interviews(3)
        self._evaluate()
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Achievements", html)
        self.assertIn("iq-achievement", html)
        self.assertIn("iq-achievement-icon", html)
        self.assertIn("iq-achievement-svg", html)         # icon macro rendered
        self.assertIn("iq-achievement-fill", html)
        self.assertIn('data-w="100"', html)               # reached badges full
        self.assertIn("Unlocked", html)
        self.assertNotIn("internal_error", html)

    def test_logged_out_dashboard_redirects(self):
        guest = self.app.test_client()
        response = guest.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].startswith("/login"))


if __name__ == "__main__":
    unittest.main()