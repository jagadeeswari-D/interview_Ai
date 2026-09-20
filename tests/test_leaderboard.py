"""Leaderboard (Stage 15) - focused coverage.

Covers: basic leaderboard (auth required, ordering, ledger-derived totals,
permitted public fields only), deterministic tie handling, privacy (no email,
profile, resume, interview, answer, note, weakness, roadmap or challenge data
exposed), authentication + cross-user isolation, XP integrity (reading the
leaderboard never writes - no ledger rows, no XP change, no achievement
unlocks, no challenge/interview mutation), current-user behaviour (in-page
highlight, out-of-page rank, zero-XP user) and Stage 14 regression (a Smart
Practice answer still awards exactly 10 XP once and is then visible).

The leaderboard derives totals straight from the Stage 14 xp_ledger; tests
seed the authoritative ledger directly for deterministic rankings and use the
real practice flow for the end-to-end regression test.
"""

import re

from tests.test_stage4 import Stage4TestBase, _csrf


class LeaderboardHelpers(object):
    """Shared helpers: registration, ledger seeding, page inspection."""

    def _logout(self):
        page = self.client.get("/settings")
        if page.status_code != 200:
            return
        token = _csrf(page.get_data(as_text=True))
        self.client.post(
            "/logout", data={"csrf_token": token}, follow_redirects=True
        )

    def _register(self, name, email):
        self._logout()
        page = self.client.get("/register")
        token = _csrf(page.get_data(as_text=True))
        return self.client.post("/register", data={
            "name": name,
            "email": email,
            "password": "password1",
            "confirm": "password1",
            "csrf_token": token,
        })

    def _login(self, email):
        self._logout()
        page = self.client.get("/login")
        token = _csrf(page.get_data(as_text=True))
        return self.client.post("/login", data={
            "email": email,
            "password": "password1",
            "csrf_token": token,
        })

    def _user_id(self, email):
        db = self._db()
        row = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        db.close()
        return row[0]

    def _db_user(self, name, email):
        """Insert a user + profile directly (no HTTP, no session side effects)."""
        db = self._db()
        cur = db.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, 'x')",
            (name, email),
        )
        db.execute("INSERT INTO profiles (user_id) VALUES (?)", (cur.lastrowid,))
        db.commit()
        user_id = cur.lastrowid
        db.close()
        return user_id

    def _seed_xp(self, user_id, amounts):
        """Insert XP ledger rows directly; `amounts` are one row each."""
        db = self._db()
        for index, amount in enumerate(amounts):
            db.execute(
                "INSERT INTO xp_ledger (user_id, source, event_key, xp) "
                "VALUES (?, 'practice_answer', ?, ?)",
                (user_id, "lead-{0}-{1}".format(user_id, index), amount),
            )
        db.commit()
        db.close()

    def _ledger_total(self, user_id):
        db = self._db()
        row = db.execute(
            "SELECT COALESCE(SUM(xp), 0) FROM xp_ledger WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        db.close()
        return int(row[0])

    def _leaderboard_body(self):
        response = self.client.get("/leaderboard")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    _ROW_RE = re.compile(
        r'<li class="le-row[^"]*">.*?</li>', re.DOTALL
    )

    def _rows(self, body):
        """Extract (rank, name, xp) tuples from each le-row in page HTML."""
        rows = []
        for item in self._ROW_RE.findall(body):
            rank = re.search(r'<span class="le-rank">(\d+)</span>', item)
            name = re.search(r'<span class="le-name">([^<]+)</span>', item)
            xp = re.search(
                r'<span class="le-xp">(\d+)<span class="le-xp-unit">XP</span>',
                item,
            )
            rows.append((int(rank.group(1)), name.group(1), int(xp.group(1))))
        return rows


class LeaderboardBasicsTests(Stage4TestBase, LeaderboardHelpers):
    """Core leaderboard: access, ordering, ledger-derived totals, fields."""

    def test_leaderboard_requires_authentication(self):
        response = self.client.get("/leaderboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers.get("Location", ""))

    def test_users_ranked_by_total_xp_descending(self):
        self._register("Paris", "paris@example.com")
        self._register("London", "london@example.com")
        self._register("Tokyo", "tokyo@example.com")
        self._seed_xp(self._user_id("paris@example.com"), [25, 25])   # 50
        self._seed_xp(self._user_id("london@example.com"), [30])      # 30
        self._seed_xp(self._user_id("tokyo@example.com"), [10])       # 10

        self._login("london@example.com")
        body = self._leaderboard_body()

        rows = self._rows(body)
        self.assertEqual(
            [(r, n) for r, n, _ in rows],
            [(1, "Paris"), (2, "London"), (3, "Tokyo")],
        )
        self.assertEqual([x for _, _, x in rows], [50, 30, 10])

    def test_rows_carry_only_public_fields(self):
        from interview_Ai.app.models import leaderboard_rows

        self._register("Alpha", "alpha@example.com")
        self._register("Beta", "beta@example.com")
        self._seed_xp(self._user_id("alpha@example.com"), [40])
        self._seed_xp(self._user_id("beta@example.com"), [20])

        with self.app.app_context():
            rows = leaderboard_rows(50)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["total_xp"], 40)
        self.assertEqual(rows[1]["total_xp"], 20)
        public = set(dict(rows[0]).keys())
        self.assertFalse(public & {"email", "password_hash"})

    def test_totals_match_the_stage14_ledger(self):
        self._register("Totals", "totals@example.com")
        uid = self._user_id("totals@example.com")
        self._seed_xp(uid, [10, 10, 25, 50])   # 95

        self._login("totals@example.com")
        body = self._leaderboard_body()
        self.assertEqual(self._rows(body), [(1, "Totals", 95)])
        self.assertEqual(self._ledger_total(uid), 95)

    def test_zero_xp_users_are_not_ranked(self):
        self._register("Active", "active@example.com")
        self._register("Idle", "idle@example.com")
        self._seed_xp(self._user_id("active@example.com"), [10])

        self._login("active@example.com")
        body = self._leaderboard_body()
        self.assertIn("Active", body)
        self.assertNotIn("Idle", body)


class LeaderboardTieTests(Stage4TestBase, LeaderboardHelpers):
    """Equal totals share a rank; ordering is stable across requests."""

    def test_ties_share_rank_and_use_user_id_as_tiebreaker(self):
        self._register("T1", "t1@example.com")
        self._register("T2", "t2@example.com")
        self._register("T3", "t3@example.com")
        self._register("T4", "t4@example.com")
        for email in ("t1@example.com", "t2@example.com", "t3@example.com"):
            self._seed_xp(self._user_id(email), [50])
        self._seed_xp(self._user_id("t4@example.com"), [20])

        self._login("t2@example.com")
        body = self._leaderboard_body()

        rows = self._rows(body)
        self.assertEqual(
            [(r, n) for r, n, _ in rows],
            [(1, "T1"), (1, "T2"), (1, "T3"), (4, "T4")],
        )

    def test_repeated_requests_return_same_ordering(self):
        self._register("S1", "s1@example.com")
        self._register("S2", "s2@example.com")
        self._register("S3", "s3@example.com")
        for email in ("s1@example.com", "s2@example.com"):
            self._seed_xp(self._user_id(email), [30])
        self._seed_xp(self._user_id("s3@example.com"), [15])

        self._login("s3@example.com")
        first = self._rows(self._leaderboard_body())
        second = self._rows(self._leaderboard_body())
        self.assertEqual(first, second)
        self.assertEqual([n for _, n, _ in first], ["S1", "S2", "S3"])


class LeaderboardPrivacyTests(Stage4TestBase, LeaderboardHelpers):
    """Nothing private ever leaks through the leaderboard surface."""

    MARKERS = {
        "email": "shadow@example.com",
        "profile_role": "PRIVATE_ROLE_NEVER_SHOW",
        "profile_skill": "PRIVATE_SKILL_NEVER_SHOW",
        "resume": "private_resume.pdf",
        "interview_role": "PRIVATE_INTERVIEW_ROLE",
        "question": "PRIVATE_QUESTION_TEXT_NEVER_SHOW",
        "answer": "PRIVATE_ANSWER_TEXT_NEVER_SHOW",
        "note": "PRIVATE_NOTE_TEXT_NEVER_SHOW",
        "weakness": "PRIVATE_WEAKNESS_NEVER_SHOW",
        "roadmap": "PRIVATE_ROADMAP_TOPIC_NEVER_SHOW",
        "challenge": "PRIVATE_CHALLENGE_QUESTION_NEVER_SHOW",
    }

    def _seed_private_target(self):
        self._register("Shadow", "shadow@example.com")
        self._register("Viewer", "viewer@example.com")
        shadow = self._user_id("shadow@example.com")
        self._seed_xp(shadow, [60])
        self._seed_xp(self._user_id("viewer@example.com"), [5])

        markers = self.MARKERS
        db = self._db()
        db.execute(
            "UPDATE profiles SET role = ?, skills = ?, resume_path = ? "
            "WHERE user_id = ?",
            (
                markers["profile_role"],
                '["' + markers["profile_skill"] + '"]',
                markers["resume"],
                shadow,
            ),
        )
        cur = db.execute(
            "INSERT INTO interviews (user_id, mode, role, difficulty, type, "
            "overall_score, status) VALUES (?, 'practice', ?, 'easy', 'sql', "
            "70.0, 'completed')",
            (shadow, markers["interview_role"]),
        )
        interview_id = cur.lastrowid
        cur = db.execute(
            "INSERT INTO questions (interview_id, question, question_type, "
            "sequence_order, expected_concepts) VALUES (?, ?, 'conceptual', 1, '[]')",
            (interview_id, markers["question"]),
        )
        question_id = cur.lastrowid
        db.execute(
            "INSERT INTO answers (question_id, user_answer, score, scores, "
            "feedback, missing_points, model_answer) "
            "VALUES (?, ?, 70, '{}', '', '[]', '')",
            (question_id, markers["answer"]),
        )
        db.execute(
            "INSERT INTO personal_notes (user_id, question_id, content) "
            "VALUES (?, ?, ?)",
            (shadow, question_id, markers["note"]),
        )
        db.execute(
            "INSERT INTO weaknesses (user_id, skill, frequency, interview_count) "
            "VALUES (?, ?, 2, 1)",
            (shadow, markers["weakness"]),
        )
        db.execute(
            "INSERT INTO roadmaps (user_id, skill, day_number, topic, "
            "practice_focus) VALUES (?, ?, 1, ?, '')",
            (shadow, markers["weakness"], markers["roadmap"]),
        )
        db.execute(
            "INSERT INTO daily_challenges (user_id, challenge_date, question, "
            "status) VALUES (?, '2099-01-01', ?, 'completed')",
            (shadow, markers["challenge"]),
        )
        db.execute(
            "INSERT INTO user_achievements (user_id, achievement_key) "
            "VALUES (?, 'first_challenge')",
            (shadow,),
        )
        db.commit()
        db.close()

    def test_page_exposes_only_display_name_and_xp(self):
        self._seed_private_target()
        self._login("viewer@example.com")
        body = self._leaderboard_body()

        rows = self._rows(body)
        self.assertIn(("Shadow", 60), [(n, x) for _, n, x in rows])
        for marker in self.MARKERS.values():
            self.assertNotIn(marker, body)

    def test_models_return_only_user_id_name_total_xp(self):
        from interview_Ai.app.models import leaderboard_rows

        self._seed_private_target()
        with self.app.app_context():
            rows = leaderboard_rows(50)
        public = set(dict(rows[0]).keys())
        self.assertTrue({"user_id", "name", "total_xp"} <= public)
        self.assertFalse(public & {
            "email", "password_hash", "role", "skills", "resume_path",
            "user_answer", "feedback", "content", "question",
        })

    def test_query_parameters_cannot_exfiltrate_data(self):
        self._seed_private_target()
        self._login("viewer@example.com")
        shadow_id = self._user_id("shadow@example.com")
        for suffix in (
            "?user_id={0}".format(shadow_id),
            "?user_id=1%20OR%201%3D1",
            "?email=shadow@example.com",
            "?interview_id=1",
        ):
            response = self.client.get("/leaderboard" + suffix)
            self.assertEqual(response.status_code, 200)
            body = response.get_data(as_text=True)
            self.assertIn("Shadow", body)
            for marker in self.MARKERS.values():
                self.assertNotIn(marker, body)


class LeaderboardAuthTests(Stage4TestBase, LeaderboardHelpers):
    """Cross-user isolation and endpoint mutability."""

    def test_post_is_rejected(self):
        self._register("Post", "post@example.com")
        page = self.client.get("/leaderboard")
        token = _csrf(page.get_data(as_text=True))
        response = self.client.post("/leaderboard", data={"csrf_token": token})
        self.assertEqual(response.status_code, 405)

    def test_viewer_cannot_retrieve_others_private_state(self):
        self._seed_private_target_simple()
        self._login("viewer@example.com")
        target = self._user_id("shadow@example.com")
        body = self.client.get("/leaderboard?u={0}".format(target))
        self.assertEqual(body.status_code, 200)
        text = body.get_data(as_text=True)
        self.assertIn("Shadow", text)
        self.assertNotIn("SHADOW_SECRET_7", text)

    def _seed_private_target_simple(self):
        self._register("Shadow", "shadow@example.com")
        self._register("Viewer", "viewer@example.com")
        shadow = self._user_id("shadow@example.com")
        self._seed_xp(shadow, [40])
        self._seed_xp(self._user_id("viewer@example.com"), [5])
        db = self._db()
        db.execute(
            "INSERT INTO interviews (user_id, mode, role, difficulty, type, "
            "overall_score, status) VALUES (?, 'practice', 'SHADOW_SECRET_7', "
            "'easy', 'sql', 70.0, 'completed')",
            (shadow,),
        )
        db.commit()
        db.close()


class LeaderboardXpIntegrityTests(Stage4TestBase, LeaderboardHelpers):
    """Reading the leaderboard must never write anything."""

    def _row_counts(self):
        tables = (
            "xp_ledger", "user_achievements", "daily_challenges", "interviews",
            "questions", "answers", "personal_notes", "roadmaps", "weaknesses",
        )
        db = self._db()
        counts = {}
        for table in tables:
            row = db.execute(
                "SELECT COUNT(*) FROM {0}".format(table)
            ).fetchone()
            counts[table] = int(row[0])
        db.close()
        return counts

    def test_viewing_creates_no_rows_and_does_not_alter_xp(self):
        self._register("Integrity", "integrity@example.com")
        uid = self._user_id("integrity@example.com")
        self._seed_xp(uid, [10, 25])
        before_rows = self._row_counts()
        before_total = self._ledger_total(uid)

        self._login("integrity@example.com")
        for _ in range(3):
            self.assertEqual(self.client.get("/leaderboard").status_code, 200)

        self.assertEqual(self._row_counts(), before_rows)
        self.assertEqual(self._ledger_total(uid), before_total)

    def test_viewing_does_not_unlock_achievements(self):
        self._register("Locker", "locker@example.com")
        uid = self._user_id("locker@example.com")
        self._seed_xp(uid, [25])
        db = self._db()
        db.execute(
            "INSERT INTO daily_challenges (user_id, challenge_date, question, "
            "status) VALUES (?, date('now'), 'q', 'completed')",
            (uid,),
        )
        db.commit()
        db.close()

        self._login("locker@example.com")
        self.client.get("/leaderboard")
        db = self._db()
        unlocked = db.execute(
            "SELECT COUNT(*) FROM user_achievements WHERE user_id = ?", (uid,)
        ).fetchone()[0]
        db.close()
        self.assertEqual(unlocked, 0)

    def test_viewing_does_not_modify_challenge_or_interview_state(self):
        self._register("State", "state@example.com")
        uid = self._user_id("state@example.com")
        self._seed_xp(uid, [25])
        db = self._db()
        db.execute(
            "INSERT INTO daily_challenges (user_id, challenge_date, question, "
            "status) VALUES (?, '2099-01-01', 'q', 'pending')",
            (uid,),
        )
        db.execute(
            "INSERT INTO interviews (user_id, mode, role, difficulty, type, "
            "overall_score, status) VALUES (?, 'practice', 'r', 'easy', 'sql', "
            "NULL, 'in_progress')",
            (uid,),
        )
        db.commit()
        db.close()

        self._login("state@example.com")
        self.client.get("/leaderboard")

        db = self._db()
        challenge = db.execute(
            "SELECT status FROM daily_challenges WHERE user_id = ?", (uid,)
        ).fetchone()
        interview = db.execute(
            "SELECT status, overall_score FROM interviews WHERE user_id = ?",
            (uid,),
        ).fetchone()
        db.close()
        self.assertEqual(challenge["status"], "pending")
        self.assertEqual(interview["status"], "in_progress")
        self.assertIsNone(interview["overall_score"])


class LeaderboardCurrentUserTests(Stage4TestBase, LeaderboardHelpers):
    """The signed-in user can locate their own position safely."""

    def test_current_user_rank_and_identity_are_rendered(self):
        self._register("First", "first@example.com")
        self._register("Second", "second@example.com")
        self._register("Current", "current@example.com")
        self._seed_xp(self._user_id("first@example.com"), [50])
        self._seed_xp(self._user_id("second@example.com"), [40])
        self._seed_xp(self._user_id("current@example.com"), [30])

        self._login("current@example.com")
        body = self._leaderboard_body()
        self.assertIn("Your rank", body)
        self.assertIn("#3", body)
        rows = self._rows(body)
        self.assertEqual(
            [(r, n, x) for r, n, x in rows],
            [(1, "First", 50), (2, "Second", 40), (3, "Current", 30)],
        )
        self.assertIn("is-self", body)
        self.assertIn("<span class=\"le-you\">You</span>", body)

    def test_zero_xp_current_user_is_not_ranked(self):
        self._register("First", "first@example.com")
        self._register("Current", "current@example.com")
        self._seed_xp(self._user_id("first@example.com"), [50])

        self._login("current@example.com")
        body = self._leaderboard_body()
        self.assertIn("You're not on the board yet", body)
        self.assertNotIn("<span class=\"le-you\">You</span>", body)
        self.assertNotIn("is-self", body)

    def test_own_rank_beyond_the_top_page_is_still_visible(self):
        self._register("Current", "current@example.com")
        for index in range(51):
            self._db_user("User{0}".format(index), "user{0}@example.com".format(index))
        for index in range(51):
            self._seed_xp(self._user_id("user{0}@example.com".format(index)), [20])
        self._seed_xp(self._user_id("current@example.com"), [5])

        self._login("current@example.com")
        body = self._leaderboard_body()
        self.assertIn("#52", body)
        self.assertNotIn("is-self", body)


class LeaderboardRegressionTests(Stage4TestBase, LeaderboardHelpers):
    """Stage 14 XP behaviour is unchanged; the leaderboard reflects it."""

    def test_practice_answer_still_awards_10_once_and_shows(self):
        self._register_and_login()
        self._practice_ask()
        qid = self._practice_question_id()
        self.assertEqual(
            self._practice_answer(qid, "Normal forms reduce duplication.").status_code,
            302,
        )
        self.assertEqual(self._ledger_total(self._user_id("student@example.com")), 10)
        self._practice_answer(qid, "An attempted duplicate answer.")
        self.assertEqual(self._ledger_total(self._user_id("student@example.com")), 10)

        body = self._leaderboard_body()
        rows = self._rows(body)
        self.assertEqual(rows, [(1, "Test Student", 10)])
        self.assertIn("<span class=\"le-you\">You</span>", body)