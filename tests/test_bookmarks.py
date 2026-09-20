"""Question Bookmarks (Stage 8, Phase 8) — Replay bookmark coverage.

Exercises the POST /replay/<interview_id>/bookmark toggle and its state on
the walkthrough + Saved questions panel: create, duplicate prevention,
unbookmark, retrieval, per-question replay state, ownership isolation,
invalid/nonexistent references, and the new bookmarks table's constraints.

Reuses the stage-4 app/DB fixture (fake Gemini, temp SQLite). Exercises the
Replay guard contract: ownership failures are 404s, exactly like Replay.
"""
import re
import sqlite3
import unittest

from tests.test_stage4 import Stage4TestBase, _csrf

_SAVED_HREF_RE = re.compile(r'href="/replay/(\d+)\?q=(\d+)"')


class BookmarkTests(Stage4TestBase):
    """One logged-in user whose completed sessions produce replayable questions."""

    def setUp(self):
        super().setUp()
        self._register_and_login()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _latest_interview_id(self):
        db = self._db()
        row = db.execute("SELECT MAX(id) FROM interviews").fetchone()
        db.close()
        return row[0]

    def _questions_of(self, interview_id):
        db = self._db()
        rows = db.execute(
            "SELECT id FROM questions WHERE interview_id = ? ORDER BY sequence_order",
            (interview_id,),
        ).fetchall()
        db.close()
        return [row[0] for row in rows]

    def _bookmark_rows(self, email="student@example.com"):
        db = self._db()
        rows = db.execute(
            "SELECT b.* FROM bookmarks b JOIN users u ON u.id = b.user_id "
            "WHERE u.email = ?",
            (email,),
        ).fetchall()
        db.close()
        return [dict(row) for row in rows]

    def _ask_in(self, interview_id, topic="Database normalization"):
        token = self._get_csrf("/practice")
        return self.client.post("/practice/question", data={
            "topic": topic, "difficulty": "easy",
            "interview_id": str(interview_id), "csrf_token": token,
        })

    def _complete_two_question_session(self):
        self._practice_ask(topic="Session topic")
        interview_id = self._latest_interview_id()
        first = self._latest_question_id()
        self._ask_in(interview_id, "Session topic")
        second = self._latest_question_id()
        self._practice_answer(first, "Answer to the first.")
        self._practice_answer(second, "Answer to the second.")
        return interview_id, first, second

    def _latest_question_id(self):
        db = self._db()
        row = db.execute("SELECT MAX(id) FROM questions").fetchone()
        db.close()
        return row[0]

    def _bookmark(self, interview_id, question_id, q=None, back=None,
                  client=None, with_token=True, token=None):
        data = {"question_id": str(question_id)}
        if q is not None:
            data["q"] = str(q)
        if back is not None:
            data["back"] = back
        if with_token:
            target = client or self.client
            if token is None:
                token = _csrf(
                    target.get(f"/replay/{interview_id}").get_data(as_text=True)
                )
            data["csrf_token"] = token
        return (client or self.client).post(
            f"/replay/{interview_id}/bookmark", data=data)

    def _attacker_csrf(self, attacker):
        return _csrf(attacker.get("/settings").get_data(as_text=True))

    def _complete_practice_session(self, topic="Database normalization"):
        self._practice_ask(topic=topic)
        self._practice_answer(self._practice_question_id(), "My answer.")

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

    def _complete_real(self):
        self._start_real()
        interview_id = self._latest_interview_id()
        self._submit_real_answer(interview_id, "Solid live answer.")
        self._backdate(13)
        finalize = self.client.get(f"/interview/{interview_id}")
        assert finalize.status_code == 302, finalize.status_code
        return interview_id

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def test_bookmarks_table_has_user_question_unique_and_cascades(self):
        db = self._db()
        try:
            columns = {
                row[1] for row in db.execute("PRAGMA table_info(bookmarks)")
            }
            unique = {
                row[1] for row in
                db.execute("PRAGMA index_list(bookmarks)")
                if row[2] == 1  # sqlite_master: unique flags
            }
            fks = {
                (row[2], row[3]) for row in
                db.execute("PRAGMA foreign_key_list(bookmarks)")
            }
        finally:
            db.close()
        self.assertEqual(
            columns, {"id", "user_id", "question_id", "created_at"},
        )
        self.assertEqual(
            unique,
            {"sqlite_autoindex_bookmarks_1"},
            "UNIQUE(user_id, question_id) index must exist",
        )
        self.assertEqual(
            fks,
            {("users", "user_id"), ("questions", "question_id")},
        )
        self._complete_practice_session(topic="Schema topic")
        db = self._db()
        try:
            db.execute(
                "INSERT INTO bookmarks (user_id, question_id) SELECT 1, id "
                "FROM questions LIMIT 1"
            )
            db.commit()
        finally:
            db.close()
        self.assertEqual(len(self._bookmark_rows()), 1)

    def test_duplicate_insert_is_rejected_by_unique_constraint(self):
        self._complete_practice_session(topic="Unique topic")
        question_id = self._questions_of(self._latest_interview_id())[0]
        db = self._db()
        try:
            db.execute(
                "INSERT INTO bookmarks (user_id, question_id) VALUES (1, ?)",
                (question_id,),
            )
            db.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO bookmarks (user_id, question_id) VALUES (1, ?)",
                    (question_id,),
                )
                db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Bookmark / unbookmark lifecycle
    # ------------------------------------------------------------------

    def test_bookmark_requires_login(self):
        anonymous = self.app.test_client()
        token = _csrf(anonymous.get("/login").get_data(as_text=True))
        response = anonymous.post(
            "/replay/1/bookmark",
            data={"question_id": 1, "csrf_token": token},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_toggle_creates_record_and_persists_on_replay(self):
        self._complete_practice_session(topic="Bookmark topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]

        page = self.client.get(f"/replay/{interview_id}")
        html = page.get_data(as_text=True)
        self.assertIn(f'/replay/{interview_id}/bookmark', html)
        self.assertIn(f'name="question_id" value="{question_id}"', html)
        self.assertIn('aria-pressed="false"', html)
        self.assertIn("<span>Bookmark</span>", html)

        response = self._bookmark(interview_id, question_id, q=1)
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/replay/{interview_id}?q=1", response.headers["Location"])
        rows = self._bookmark_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["question_id"], question_id)

        refreshed = self.client.get(f"/replay/{interview_id}")
        html = refreshed.get_data(as_text=True)
        self.assertIn('aria-pressed="true"', html)
        self.assertIn("<span>Saved</span>", html)
        self.assertIn("bookmark-toggle is-bookmarked", html)

    def test_unbookmark_removes_record_and_resets_state(self):
        self._complete_practice_session(topic="Unbookmark topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]
        self._bookmark(interview_id, question_id, q=1)
        self.assertEqual(len(self._bookmark_rows()), 1)

        response = self._bookmark(interview_id, question_id, q=1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self._bookmark_rows()), 0)

        html = self.client.get(f"/replay/{interview_id}").get_data(as_text=True)
        self.assertIn('aria-pressed="false"', html)
        self.assertIn("<span>Bookmark</span>", html)
        self.assertNotIn("bookmark-toggle is-bookmarked", html)

    def test_duplicate_requests_never_create_duplicate_records(self):
        self._complete_practice_session(topic="Race topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]

        counts = []
        # Four rapid toggles: each response is a toggle (0/1 alternating),
        # but the count is always 0 or 1 — never more than one bookmark row.
        for _ in range(4):
            self._bookmark(interview_id, question_id, q=1)
            counts.append(len(self._bookmark_rows()))
        self.assertEqual(set(counts), {0, 1})
        self._bookmark(interview_id, question_id, q=1)
        self.assertEqual(len(self._bookmark_rows()), 1)

    def test_replay_state_tracks_per_question_in_a_session(self):
        interview_id, first, second = self._complete_two_question_session()
        self._bookmark(interview_id, second, q=2)

        q1 = self.client.get(f"/replay/{interview_id}?q=1").get_data(as_text=True)
        q2 = self.client.get(f"/replay/{interview_id}?q=2").get_data(as_text=True)
        self.assertIn(f'name="question_id" value="{first}"', q1)
        self.assertIn("<span>Bookmark</span>", q1)
        self.assertNotIn("bookmark-toggle is-bookmarked", q1)
        self.assertIn(f'name="question_id" value="{second}"', q2)
        self.assertIn("<span>Saved</span>", q2)
        self.assertIn("bookmark-toggle is-bookmarked", q2)

    # ------------------------------------------------------------------
    # Saved questions view (Replay index)
    # ------------------------------------------------------------------

    def test_saved_panel_lists_bookmarks_with_session_context(self):
        self._complete_practice_session(topic="Database normalization")
        first_interview = self._latest_interview_id()
        first_q = self._questions_of(first_interview)[0]
        self._complete_practice_session(topic="System design")
        second_interview = self._latest_interview_id()
        second_q = self._questions_of(second_interview)[0]

        self._bookmark(first_interview, first_q, q=1)
        self._bookmark(second_interview, second_q, q=1)

        html = self.client.get("/replay").get_data(as_text=True)
        self.assertIn("Saved questions", html)
        self.assertIn("Database normalization", html)
        self.assertIn("System design", html)
        hrefs = set(_SAVED_HREF_RE.findall(html))
        self.assertEqual(
            {(int(a), int(b)) for a, b in hrefs},
            {(first_interview, 1), (second_interview, 1)},
        )

    def test_saved_deep_link_uses_the_bookmarked_question_position(self):
        interview_id, _, second = self._complete_two_question_session()
        self._bookmark(interview_id, second, q=2)
        html = self.client.get("/replay").get_data(as_text=True)
        hrefs = set(_SAVED_HREF_RE.findall(html))
        self.assertEqual(hrefs, {(str(interview_id), "2")})

    def test_removed_bookmark_disappears_from_saved_panel(self):
        self._complete_practice_session(topic="Remove topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]
        self._bookmark(interview_id, question_id, q=1)
        self.assertIn("Saved questions", self.client.get("/replay").get_data(as_text=True))

        response = self._bookmark(interview_id, question_id, back="saved")
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertIn("/replay", location)
        self.assertIn("#saved", location)
        html = self.client.get("/replay").get_data(as_text=True)
        self.assertNotIn("Saved questions", html)
        self.assertEqual(len(self._bookmark_rows()), 0)

    def test_real_mode_sessions_support_bookmarks(self):
        interview_id = self._complete_real()
        question_id = self._questions_of(interview_id)[0]

        page = self.client.get(f"/replay/{interview_id}")
        self.assertEqual(page.status_code, 200)
        self._bookmark(interview_id, question_id, q=1)
        html = self.client.get("/replay").get_data(as_text=True)
        self.assertIn("Saved questions", html)
        self.assertIn("Real interview", html)
        self.assertIn("Backend Developer", html)

    # ------------------------------------------------------------------
    # Security / ownership
    # ------------------------------------------------------------------

    def test_ownership_isolation_and_direct_manipulation_blocked(self):
        self._complete_practice_session(topic="Victim topic")
        victim_interview = self._latest_interview_id()
        victim_q = self._questions_of(victim_interview)[0]
        self._bookmark(victim_interview, victim_q, q=1)

        attacker = self._register_attacker()
        attacker_token = self._attacker_csrf(attacker)

        attacker_index = attacker.get("/replay").get_data(as_text=True)
        self.assertNotIn("Victim topic", attacker_index)
        self.assertNotIn("Saved questions", attacker_index)

        found = attacker.get(f"/replay/{victim_interview}")
        self.assertEqual(found.status_code, 404)
        blocked = self._bookmark(
            victim_interview, victim_q, q=1,
            client=attacker, token=attacker_token,
        )
        self.assertEqual(blocked.status_code, 404)
        self.assertEqual(len(self._bookmark_rows()), 1)

    def test_question_from_another_interview_cannot_be_bookmarked(self):
        self._complete_practice_session(topic="Other session")
        victim_interview = self._latest_interview_id()
        victim_q = self._questions_of(victim_interview)[0]
        self._bookmark(victim_interview, victim_q, q=1)

        attacker = self._register_attacker()
        attacker_token = self._attacker_csrf(attacker)
        attacker.post("/practice/question", data={
            "topic": "Attacker topic", "difficulty": "easy",
            "csrf_token": attacker_token,
        })
        db = self._db()
        row = db.execute(
            "SELECT interview_id FROM questions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        db.close()
        attacker_interview = row[0]

        cross = self._bookmark(
            attacker_interview, victim_q, q=1,
            client=attacker, token=attacker_token,
        )
        self.assertEqual(cross.status_code, 404)
        self.assertEqual(len(self._bookmark_rows()), 1)

    def test_nonexistent_interview_and_question_references_fail_safely(self):
        self._complete_practice_session(topic="Safe topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]
        token = _csrf(self.client.get(f"/replay/{interview_id}").get_data(as_text=True))

        missing_interview = self.client.post(
            "/replay/424242/bookmark",
            data={"question_id": str(question_id), "csrf_token": token},
        )
        self.assertEqual(missing_interview.status_code, 404)

        missing_q = self._bookmark(interview_id, 999999)
        self.assertEqual(missing_q.status_code, 404)

        no_q = self.client.post(
            f"/replay/{interview_id}/bookmark",
            data={"csrf_token": token},
        )
        self.assertEqual(no_q.status_code, 404)

        non_numeric = self.client.post(
            f"/replay/{interview_id}/bookmark",
            data={"question_id": "abc", "csrf_token": token},
        )
        self.assertEqual(non_numeric.status_code, 404)
        self.assertEqual(len(self._bookmark_rows()), 0)

    def test_missing_csrf_token_rejected(self):
        self._complete_practice_session(topic="Csrf topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]
        response = self.client.post(
            f"/replay/{interview_id}/bookmark",
            data={"question_id": str(question_id)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self._bookmark_rows()), 0)


if __name__ == "__main__":
    unittest.main()