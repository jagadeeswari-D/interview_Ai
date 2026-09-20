"""XP (Stage 14) — focused coverage.

Golden contract (exact award amounts):
    practice question graded         10 XP
    real interview completed         50 XP  once per interview, graded only
    daily challenge completed        25 XP
    first_challenge                  +25
    streak_3                         +25
    streak_7                         +50
    challenge_10                    +100
    interviews_5                    +100

Coverage: the three completion boundaries (practice / interview / challenge),
each achievement bonus, DB-enforced duplicate prevention, race safety via the
UNIQUE(user_id, source, event_key) ledger boundary, cross-user isolation,
regression boundaries (no practice XP from real-mode answers, one 50 XP award
per whole interview, unchanged challenge/achievement behavior) and
transaction/failure safety.
"""
import datetime
import threading
import unittest

from interview_Ai.app.xp import (
    SRC_ACHIEVEMENT,
    SRC_DAILY_CHALLENGE,
    SRC_PRACTICE_ANSWER,
    SRC_REAL_INTERVIEW,
    XP_DAILY_CHALLENGE,
    XP_PRACTICE_ANSWER,
    XP_REAL_INTERVIEW,
)
from tests.test_achievements import AchievementsBase, _seed_challenge, _seed_interview
from tests.test_stage4 import Stage4TestBase, _csrf


def _day(offset, base):
    d = datetime.date.fromisoformat(base) + datetime.timedelta(days=offset)
    return d.isoformat()


def _real_today():
    return datetime.date.today().isoformat()


class XPHelpers(object):
    """Ledger helpers shared by the XP suites."""

    def _ledger_rows(self, user_id):
        conn = self._db()
        try:
            rows = conn.execute(
                "SELECT * FROM xp_ledger WHERE user_id = ? ORDER BY id",
                (user_id,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def _total(self, user_id):
        conn = self._db()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(xp), 0) FROM xp_ledger WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        finally:
            conn.close()
        return int(row[0])

    def _grant(self, user_id=None, today=None):
        """evaluate_achievements then award bonuses (boundary sequence)."""
        from interview_Ai.app.xp import award_achievement_bonuses

        keys = self._evaluate(user_id, today)
        with self.app.app_context():
            award_achievement_bonuses(user_id or self.user_id)
        return keys

    def _answer_daily_challenge(self, text="Indexes trade write speed for reads."):
        self.client.get("/challenge")
        conn = self._db()
        challenge_id = conn.execute(
            "SELECT id FROM daily_challenges WHERE user_id = ?", (self.user_id,)
        ).fetchone()[0]
        conn.close()
        token = self._get_csrf("/challenge")
        return self.client.post("/challenge/answer", data={
            "challenge_id": str(challenge_id),
            "answer": text,
            "csrf_token": token,
        })

    def _finish_real_interview(self, interview_id):
        """Answer every question of an easy (4-question) real interview until
        the session completes and is finalized. Returns the number of answers
        submitted."""
        answered = 0
        while True:
            conn = self._db()
            open_q = conn.execute(
                "SELECT id FROM questions WHERE interview_id = ? AND id NOT IN "
                "(SELECT question_id FROM answers) "
                "ORDER BY sequence_order DESC LIMIT 1",
                (interview_id,),
            ).fetchone()
            conn.close()
            if open_q is None:
                break
            token = self._get_csrf(f"/interview/{interview_id}")
            response = self.client.post(
                f"/interview/{interview_id}/answer",
                data={"answer": f"Answer {answered + 1}", "csrf_token": token},
            )
            self.assertEqual(response.status_code, 302)
            answered += 1
            conn = self._db()
            status = conn.execute(
                "SELECT status FROM interviews WHERE id = ?", (interview_id,)
            ).fetchone()[0]
            conn.close()
            if status == "completed":
                break
        return answered


class XPAwardTests(AchievementsBase, XPHelpers):
    """The three completion boundaries award their exact XP amounts."""

    def test_practice_completion_awards_10(self):
        self._practice_ask()
        question_id = self._practice_question_id()
        response = self._practice_answer(
            question_id, "Normal forms remove redundancy."
        )
        self.assertEqual(response.status_code, 302)

        rows = self._ledger_rows(self.user_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], SRC_PRACTICE_ANSWER)
        self.assertEqual(rows[0]["event_key"], str(question_id))
        self.assertEqual(rows[0]["xp"], XP_PRACTICE_ANSWER)
        self.assertEqual(self._total(self.user_id), 10)

    def test_real_interview_completion_awards_50_once(self):
        self._start_real()
        interview_id = self._first_interview_id()
        sections = self._finish_real_interview(interview_id)
        self.assertEqual(sections, 4)  # easy budget: four questions

        rows = self._ledger_rows(self.user_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], SRC_REAL_INTERVIEW)
        self.assertEqual(rows[0]["event_key"], str(interview_id))
        self.assertEqual(rows[0]["xp"], XP_REAL_INTERVIEW)
        self.assertEqual(self._total(self.user_id), 50)

    def test_daily_challenge_completion_awards_25(self):
        self._answer_daily_challenge()

        rows = self._ledger_rows(self.user_id)
        challenge_rows = [
            row for row in rows if row["source"] == SRC_DAILY_CHALLENGE
        ]
        self.assertEqual(len(challenge_rows), 1)
        self.assertEqual(challenge_rows[0]["xp"], XP_DAILY_CHALLENGE)
        # Challenge XP 25 + first_challenge bonus 25
        self.assertEqual(self._total(self.user_id), 50)

    def test_challenge_completion_also_pays_first_challenge_bonus(self):
        self._answer_daily_challenge()
        # 25 (challenge) + 25 (first_challenge bonus) = 50
        self.assertEqual(self._total(self.user_id), 50)
        sources = sorted(row["source"] for row in self._ledger_rows(self.user_id))
        self.assertEqual(sources, [SRC_ACHIEVEMENT, SRC_DAILY_CHALLENGE])

    def test_bonus_map_matches_golden_contract(self):
        from interview_Ai.app.xp import ACHIEVEMENT_XP

        self.assertEqual(ACHIEVEMENT_XP, {
            "first_challenge": 25,
            "streak_3": 25,
            "streak_7": 50,
            "challenge_10": 100,
            "interviews_5": 100,
        })

    def test_dashboard_renders_total_xp(self):
        self._practice_ask()
        self._practice_answer(
            self._practice_question_id(), "A solid answer."
        )
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Total XP", html)
        self.assertIn('data-count="10"', html)


class XPAchievementBonusTests(AchievementsBase, XPHelpers):
    """Each achievement awards exactly its mapped bonus when unlocked first."""

    def test_first_challenge_bonus_25(self):
        self._seed_days([0])
        self._grant()
        self.assertEqual(self._total(self.user_id), 25)

    def test_streak_3_bonus_25(self):
        self._seed_days([-2, -1, 0])
        self._grant()
        # first_challenge (25) + streak_3 (25)
        self.assertEqual(self._total(self.user_id), 50)

    def test_streak_7_bonus_50(self):
        self._seed_days([-6, -5, -4, -3, -2, -1, 0])
        self._grant()
        # first_challenge 25 + streak_3 25 + streak_7 50
        self.assertEqual(self._total(self.user_id), 100)

    def test_challenge_10_bonus_100(self):
        self._seed_days([-9, -8, -7, -6, -5, -4, -3, -2, -1, 0])
        self._grant()
        # 25 + 25 + 50 + 100
        self.assertEqual(self._total(self.user_id), 200)

    def test_interviews_5_bonus_100(self):
        self._seed_interviews(5)
        self._grant()
        # only interviews_5 (100) — practice days/interviews seeded don't pay
        self.assertEqual(self._total(self.user_id), 100)

    def test_repeated_evaluation_does_not_re_award_bonus(self):
        self._seed_interviews(5)
        self._grant()
        first_total = self._total(self.user_id)
        self._grant()
        self.assertEqual(self._total(self.user_id), first_total)
        self.assertEqual(len(self._ledger_rows(self.user_id)), 1)


class XPDuplicateTests(AchievementsBase, XPHelpers):
    """The same logical event can never award XP twice."""

    def test_award_is_idempotent_at_db_boundary(self):
        from interview_Ai.app.xp import award

        with self.app.app_context():
            first = award(self.user_id, SRC_PRACTICE_ANSWER, "q100", 10)
            second = award(self.user_id, SRC_PRACTICE_ANSWER, "q100", 10)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(self._total(self.user_id), 10)
        self.assertEqual(len(self._ledger_rows(self.user_id)), 1)

    def test_duplicate_practice_submission_awards_once(self):
        self._practice_ask()
        question_id = self._practice_question_id()
        self._practice_answer(question_id, "First attempt.")
        # Re-submit the same question (browser retry / refresh).
        self._practice_answer(question_id, "First attempt.")
        self.assertEqual(self._total(self.user_id), 10)
        self.assertEqual(len(self._ledger_rows(self.user_id)), 1)

    def test_duplicate_real_interview_finalize_awards_once(self):
        self._start_real()
        interview_id = self._first_interview_id()
        self._finish_real_interview(interview_id)

        from interview_Ai.app.interview import _finalize
        from interview_Ai.app.models import get_interview

        with self.app.app_context():
            again = get_interview(interview_id)
            _finalize(again)  # a second finalize on the same interview
        self.assertEqual(self._total(self.user_id), 50)
        self.assertEqual(len(self._ledger_rows(self.user_id)), 1)

    def test_duplicate_challenge_completion_awards_once(self):
        self.client.get("/challenge")
        conn = self._db()
        challenge_id = conn.execute(
            "SELECT id FROM daily_challenges WHERE user_id = ?", (self.user_id,)
        ).fetchone()[0]
        conn.close()
        token = self._get_csrf("/challenge")
        self.client.post("/challenge/answer", data={
            "challenge_id": str(challenge_id),
            "answer": "First answer.",
            "csrf_token": token,
        })
        self.client.post("/challenge/answer", data={
            "challenge_id": str(challenge_id),
            "answer": "A second, different answer.",
            "csrf_token": token,
        })
        self.assertEqual(self._total(self.user_id), 50)  # 25 + 25 bonus
        self.assertEqual(len(self._ledger_rows(self.user_id)), 2)


class XPRaceTests(AchievementsBase, XPHelpers):
    """Concurrent awards of the same logical event produce exactly one row."""

    def test_concurrent_duplicate_awards_single_row(self):
        from interview_Ai.app.xp import award

        results = {}

        def worker():
            with self.app.app_context():
                results[threading.get_ident()] = award(
                    self.user_id, SRC_REAL_INTERVIEW, "99", 50
                )

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(list(results.values()).count(True), 1)
        self.assertEqual(self._total(self.user_id), 50)
        self.assertEqual(len(self._ledger_rows(self.user_id)), 1)

    def test_separate_events_award_separately(self):
        from interview_Ai.app.xp import award

        with self.app.app_context():
            award(self.user_id, SRC_REAL_INTERVIEW, "1", 50)
            award(self.user_id, SRC_REAL_INTERVIEW, "2", 50)
        self.assertEqual(self._total(self.user_id), 100)
        self.assertEqual(len(self._ledger_rows(self.user_id)), 2)


class XPIsolationTests(AchievementsBase, XPHelpers):
    """One user's XP can never leak into or be affected by another's."""

    def test_users_are_isolated(self):
        self._answer_daily_challenge()
        self.assertEqual(self._total(self.user_id), 50)

        attacker = self.app.test_client()
        page = attacker.get("/register")
        token = _csrf(page.get_data(as_text=True))
        attacker.post("/register", data={
            "name": "Mallory", "email": "mallory@example.com",
            "password": "password1", "confirm": "password1",
            "csrf_token": token,
        })
        conn = self._db()
        attacker_id = conn.execute(
            "SELECT id FROM users WHERE email = ?", ("mallory@example.com",)
        ).fetchone()[0]
        conn.close()

        self.assertEqual(self._total(attacker_id), 0)
        self.assertEqual(len(self._ledger_rows(attacker_id)), 0)
        self.assertEqual(self._total(self.user_id), 50)  # untouched

    def test_attacker_cannot_complete_others_challenge_for_xp(self):
        self.client.get("/challenge")
        conn = self._db()
        victim_challenge_id = conn.execute(
            "SELECT id FROM daily_challenges WHERE user_id = ?", (self.user_id,)
        ).fetchone()[0]
        conn.close()

        attacker = self.app.test_client()
        page = attacker.get("/register")
        token = _csrf(page.get_data(as_text=True))
        attacker.post("/register", data={
            "name": "Mallory", "email": "mallory@example.com",
            "password": "password1", "confirm": "password1",
            "csrf_token": token,
        })
        attacker_page = attacker.get("/challenge").get_data(as_text=True)
        token = _csrf(attacker_page)
        blocked = attacker.post("/challenge/answer", data={
            "challenge_id": str(victim_challenge_id),
            "answer": "intrusion",
            "csrf_token": token,
        })
        self.assertEqual(blocked.status_code, 404)

        self.assertEqual(self._total(self.user_id), 0)  # still no XP
        rows = self._ledger_rows(self.user_id)
        self.assertEqual(len(rows), 0)


class XPRegressionTests(AchievementsBase, XPHelpers):
    """Regression boundaries required by the stage contract."""

    def test_no_practice_xp_for_real_interview_answers(self):
        self._start_real()
        interview_id = self._first_interview_id()
        # First answer: interview not complete yet -> absolutely no XP.
        self._submit_real_answer(interview_id, "An in-progress answer.")
        self.assertEqual(self._total(self.user_id), 0)

    def test_whole_interview_is_one_50_award_not_per_answer(self):
        self._start_real()
        interview_id = self._first_interview_id()
        sections = self._finish_real_interview(interview_id)
        self.assertEqual(sections, 4)

        # Any further submission on the finished session changes nothing.
        token = self._get_csrf("/practice")
        response = self.client.post(f"/interview/{interview_id}/answer", data={
            "answer": "Too late to matter.", "csrf_token": token,
        })
        self.assertEqual(response.status_code, 302)

        self.assertEqual(self._total(self.user_id), 50)
        real_rows = [
            row for row in self._ledger_rows(self.user_id)
            if row["source"] == SRC_REAL_INTERVIEW
        ]
        self.assertEqual(len(real_rows), 1)

    def test_empty_expired_interview_awards_nothing(self):
        self._start_real()
        interview_id = self._first_interview_id()
        self._backdate(30)
        response = self.client.get(f"/interview/{interview_id}")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._total(self.user_id), 0)

    def test_challenge_behavior_unchanged(self):
        # The route still records exactly one completion with the first answer.
        self.client.get("/challenge")
        conn = self._db()
        challenge_id = conn.execute(
            "SELECT id FROM daily_challenges WHERE user_id = ?", (self.user_id,)
        ).fetchone()[0]
        conn.close()

        token = self._get_csrf("/challenge")
        self.client.post("/challenge/answer", data={
            "challenge_id": str(challenge_id),
            "answer": "Original answer.",
            "csrf_token": token,
        })
        self.client.post("/challenge/answer", data={
            "challenge_id": str(challenge_id),
            "answer": "A second answer.",
            "csrf_token": token,
        })

        conn = self._db()
        rows = conn.execute(
            "SELECT * FROM daily_challenges WHERE id = ?", (challenge_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(rows["status"], "completed")
        self.assertEqual(rows["answer"], "Original answer.")

    def test_achievement_behavior_unchanged(self):
        self._answer_daily_challenge()
        from interview_Ai.app.achievements import achievement_summary
        with self.app.app_context():
            summary = achievement_summary(self.user_id)
        merged = {entry["key"]: entry for entry in summary}
        self.assertTrue(merged["first_challenge"]["unlocked"])
        self.assertEqual(len(merged), 5)


class XPFailureTests(AchievementsBase, XPHelpers):
    """Transaction/failure safety around the award boundaries."""

    def test_source_failure_awards_nothing(self):
        from interview_Ai.app.ai.errors import GeminiRateLimitError

        def flaky(service, question, answer, concepts):
            raise GeminiRateLimitError(429, "rate limited")

        import interview_Ai.app.practice as practice_module
        from unittest import mock

        self._practice_ask()
        question_id = self._practice_question_id()
        with mock.patch.object(practice_module, "evaluate_answer", side_effect=flaky):
            response = self._practice_answer(question_id, "A late answer.")
        self.assertEqual(response.status_code, 302)

        self.assertEqual(self._total(self.user_id), 0)
        self.assertEqual(len(self._ledger_rows(self.user_id)), 0)

    def test_xp_failure_does_not_corrupt_source_state(self):
        from unittest import mock

        self._practice_ask()
        question_id = self._practice_question_id()
        token = self._get_csrf(f"/practice/question/{question_id}")

        with mock.patch(
            "interview_Ai.app.xp.award",
            side_effect=RuntimeError("ledger write failed"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post("/practice/answer", data={
                    "question_id": str(question_id),
                    "answer": "A graded answer.",
                    "csrf_token": token,
                })

        # The authoritative source write is intact; XP simply was not paid.
        conn = self._db()
        answer = conn.execute(
            "SELECT * FROM answers WHERE question_id = ?", (question_id,)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(answer)
        self.assertEqual(self._total(self.user_id), 0)
        self.assertEqual(len(self._ledger_rows(self.user_id)), 0)


class XPImportSanityTests(Stage4TestBase):
    """XP module wiring does not require an API key or break bootstrapping."""

    def test_module_imports_and_policy_constants(self):
        from interview_Ai.app import xp

        self.assertEqual(xp.XP_PRACTICE_ANSWER, 10)
        self.assertEqual(xp.XP_REAL_INTERVIEW, 50)
        self.assertEqual(xp.XP_DAILY_CHALLENGE, 25)
        self.assertEqual(xp.ACHIEVEMENT_XP["first_challenge"], 25)

    def test_total_xp_empty_is_zero(self):
        self._register_and_login()
        conn = self._db()
        user_id = conn.execute("SELECT id FROM users LIMIT 1").fetchone()[0]
        conn.close()
        self.assertEqual(self.client.get("/dashboard").status_code, 200)
        from interview_Ai.app.xp import total_xp
        with self.app.app_context():
            self.assertEqual(total_xp(user_id), 0)


if __name__ == "__main__":
    unittest.main()