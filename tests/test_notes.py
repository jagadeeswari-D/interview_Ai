"""Personal Notes (Stage 8, Phase 8) — private per-question notes in Replay.

Exercises POST /replay/<interview_id>/note save/clear and the note UI on the
walkthrough: create, update (single row, upsert), clear, empty/whitespace
handling, length bounds, persistence across refreshes, per-question
isolation, coexistence with bookmarks, ownership isolation, invalid
references, and the new personal_notes table's constraints.

Reuses the stage-4 app/DB fixture (fake Gemini, temp SQLite). Exercises the
Replay guard contract: ownership failures are 404s, CSRF failures are 400s.
"""
import re
import sqlite3
import unittest

from tests.test_stage4 import Stage4TestBase, _csrf

_NOTE_MAX_LENGTH = 1000


class NoteTests(Stage4TestBase):
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

    def _note_rows(self, email="student@example.com"):
        db = self._db()
        rows = db.execute(
            "SELECT n.* FROM personal_notes n JOIN users u ON u.id = n.user_id "
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
        first = self._practice_question_id()
        self._ask_in(interview_id, "Session topic")
        second = self._practice_question_id()
        self._practice_answer(first, "Answer to the first.")
        self._practice_answer(second, "Answer to the second.")
        return interview_id, first, second

    def _post_note(self, interview_id, question_id, content=None, action="save",
                   q=None, with_token=True, token=None, client=None):
        data = {"action": action, "question_id": str(question_id)}
        if content is not None:
            data["content"] = content
        if q is not None:
            data["q"] = str(q)
        if with_token:
            target = client or self.client
            if token is None:
                token = _csrf(
                    target.get(f"/replay/{interview_id}").get_data(as_text=True)
                )
            data["csrf_token"] = token
        return (client or self.client).post(
            f"/replay/{interview_id}/note", data=data)

    def _attacker_csrf(self, attacker):
        return _csrf(attacker.get("/settings").get_data(as_text=True))

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

    def _complete_practice_session(self, topic="Database normalization"):
        self._practice_ask(topic=topic)
        self._practice_answer(self._practice_question_id(), "My answer.")

    def _complete_real(self):
        self._start_real()
        interview_id = self._latest_interview_id()
        self._submit_real_answer(interview_id, "Solid live answer.")
        self._backdate(13)
        finalize = self.client.get(f"/interview/{interview_id}")
        assert finalize.status_code == 302, finalize.status_code
        return interview_id

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

    def _bookmark_rows(self, email="student@example.com"):
        db = self._db()
        rows = db.execute(
            "SELECT b.* FROM bookmarks b JOIN users u ON u.id = b.user_id "
            "WHERE u.email = ?",
            (email,),
        ).fetchall()
        db.close()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def test_notes_table_has_user_question_unique_and_cascades(self):
        db = self._db()
        try:
            columns = {
                row[1] for row in db.execute("PRAGMA table_info(personal_notes)")
            }
            unique = {
                row[1] for row in
                db.execute("PRAGMA index_list(personal_notes)")
                if row[2] == 1
            }
            fks = {
                (row[2], row[3]) for row in
                db.execute("PRAGMA foreign_key_list(personal_notes)")
            }
        finally:
            db.close()
        self.assertEqual(
            columns, {"id", "user_id", "question_id", "content", "updated_at"},
        )
        self.assertEqual(
            unique,
            {"sqlite_autoindex_personal_notes_1"},
            "UNIQUE(user_id, question_id) index must exist",
        )
        self.assertEqual(
            fks,
            {("users", "user_id"), ("questions", "question_id")},
        )
        self._complete_practice_session()
        question_id = self._questions_of(self._latest_interview_id())[0]
        self._post_note(self._latest_interview_id(), question_id,
                        "Schema note", q=1)
        self.assertEqual(len(self._note_rows()), 1)

    def test_duplicate_insert_is_rejected_by_unique_constraint(self):
        self._complete_practice_session()
        question_id = self._questions_of(self._latest_interview_id())[0]
        db = self._db()
        try:
            db.execute(
                "INSERT INTO personal_notes (user_id, question_id, content) "
                "VALUES (1, ?, 'first')",
                (question_id,),
            )
            db.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO personal_notes (user_id, question_id, content) "
                    "VALUES (1, ?, 'second')",
                    (question_id,),
                )
                db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Save / update / clear lifecycle
    # ------------------------------------------------------------------

    def test_create_note_persists_and_is_shown_in_replay(self):
        self._complete_practice_session(topic="Note topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]

        page = self.client.get(f"/replay/{interview_id}")
        html = page.get_data(as_text=True)
        self.assertIn("Personal note", html)
        self.assertIn(f'/replay/{interview_id}/note', html)
        self.assertIn(f'name="question_id" value="{question_id}"', html)
        self.assertIn("No note yet", html)
        self.assertIn('name="action" value="save"', html)
        self.assertIn('name="action" value="clear"', html)

        response = self._post_note(
            interview_id, question_id, "Talk about ACID transactions.", q=1)
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/replay/{interview_id}?q=1", response.headers["Location"])
        rows = self._note_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["question_id"], question_id)
        self.assertEqual(rows[0]["content"], "Talk about ACID transactions.")

        refreshed = self.client.get(f"/replay/{interview_id}")
        html = refreshed.get_data(as_text=True)
        self.assertIn("Talk about ACID transactions.", html)
        self.assertIn("Saved", html)

    def test_update_replaces_content_keeping_a_single_row(self):
        self._complete_practice_session(topic="Update topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]

        self._post_note(interview_id, question_id, "First draft.", q=1)
        second = self._post_note(interview_id, question_id, "Second draft.", q=1)
        self.assertEqual(second.status_code, 302)
        rows = self._note_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "Second draft.")

        html = self.client.get(f"/replay/{interview_id}").get_data(as_text=True)
        self.assertIn("Second draft.", html)
        self.assertNotIn("First draft.", html)

    def test_clear_removes_row_and_resets_state(self):
        self._complete_practice_session(topic="Clear topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]
        self._post_note(interview_id, question_id, "Temporary thought.", q=1)
        self.assertEqual(len(self._note_rows()), 1)

        response = self._post_note(interview_id, question_id, action="clear", q=1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self._note_rows()), 0)

        html = self.client.get(f"/replay/{interview_id}").get_data(as_text=True)
        self.assertIn("No note yet", html)
        self.assertNotIn("Temporary thought.", html)

    def test_empty_note_clears_any_stored_note(self):
        self._complete_practice_session(topic="Empty topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]
        self._post_note(interview_id, question_id, "Discard me.", q=1)
        self.assertEqual(len(self._note_rows()), 1)

        response = self._post_note(interview_id, question_id, content="", q=1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self._note_rows()), 0)

    def test_whitespace_only_note_never_saved_and_clears_existing(self):
        self._complete_practice_session(topic="Whitespace topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]
        self._post_note(interview_id, question_id, "Before.", q=1)
        self.assertEqual(len(self._note_rows()), 1)

        response = self._post_note(interview_id, question_id,
                                   content="   \n\t  ", q=1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self._note_rows()), 0)

    def test_note_submission_without_content_field_is_an_empty_note(self):
        self._complete_practice_session(topic="No content topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]
        response = self._post_note(interview_id, question_id, q=1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self._note_rows()), 0)

    # ------------------------------------------------------------------
    # Length bounds
    # ------------------------------------------------------------------

    def test_note_at_maximum_length_is_accepted(self):
        self._complete_practice_session(topic="Max topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]
        exact = "a" * _NOTE_MAX_LENGTH

        response = self._post_note(interview_id, question_id, exact, q=1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._note_rows()[0]["content"], exact)

        html = self.client.get(f"/replay/{interview_id}").get_data(as_text=True)
        self.assertIn(f'maxlength="{_NOTE_MAX_LENGTH}"', html)

    def test_oversized_note_is_rejected_and_nothing_changes(self):
        self._complete_practice_session(topic="Oversize topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]

        response = self._post_note(interview_id, question_id,
                                   "b" * (_NOTE_MAX_LENGTH + 1), q=1)
        location = response.headers["Location"]
        self.assertIn(f"/replay/{interview_id}", location)
        self.assertEqual(len(self._note_rows()), 0)

        page = self.client.get(location)
        html = page.get_data(as_text=True)
        self.assertIn("limited to 1000 characters", html)

    def test_oversized_note_leaves_an_existing_note_untouched(self):
        self._complete_practice_session(topic="Oversize keep topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]
        self._post_note(interview_id, question_id, "Keep me.", q=1)

        response = self._post_note(interview_id, question_id,
                                   "c" * (_NOTE_MAX_LENGTH + 1), q=1)
        self.assertEqual(response.status_code, 302)
        rows = self._note_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "Keep me.")

    # ------------------------------------------------------------------
    # Persistence and per-question isolation
    # ------------------------------------------------------------------

    def test_notes_are_per_question_within_a_session(self):
        interview_id, first, second = self._complete_two_question_session()
        self._post_note(interview_id, first, "Note for Q1.", q=1)

        q1 = self.client.get(f"/replay/{interview_id}?q=1").get_data(as_text=True)
        q2 = self.client.get(f"/replay/{interview_id}?q=2").get_data(as_text=True)
        self.assertIn("Note for Q1.", q1)
        self.assertIn(f'name="question_id" value="{first}"', q1)
        self.assertNotIn("Note for Q1.", q2)
        self.assertIn("No note yet", q2)
        self.assertIn(f'name="question_id" value="{second}"', q2)

        # Coming back to the same question again still shows the note.
        again = self.client.get(f"/replay/{interview_id}?q=1").get_data(as_text=True)
        self.assertIn("Note for Q1.", again)

    def test_note_survives_fresh_walkthrough_requests(self):
        self._complete_practice_session(topic="Fresh topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]
        self._post_note(interview_id, question_id, "Remembered.", q=1)

        for _ in range(3):
            html = self.client.get(f"/replay/{interview_id}").get_data(as_text=True)
            self.assertIn("Remembered.", html)

    def test_real_mode_sessions_support_notes(self):
        interview_id = self._complete_real()
        question_id = self._questions_of(interview_id)[0]

        page = self.client.get(f"/replay/{interview_id}")
        self.assertEqual(page.status_code, 200)
        self._post_note(interview_id, question_id, "Real-mode note.", q=1)
        html = self.client.get(f"/replay/{interview_id}?q=1").get_data(as_text=True)
        self.assertIn("Real-mode note.", html)

    # ------------------------------------------------------------------
    # Privacy (notes never leak into aggregate views)
    # ------------------------------------------------------------------

    def test_notes_never_leak_into_index_history_or_report_views(self):
        self._complete_practice_session(topic="Leak topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]
        self._post_note(interview_id, question_id, "Secret reminder.", q=1)

        for path in ("/replay", "/history", f"/history/{interview_id}"):
            html = self.client.get(path).get_data(as_text=True)
            self.assertNotIn("Secret reminder.", html, path)

    # ------------------------------------------------------------------
    # Security / ownership
    # ------------------------------------------------------------------

    def test_note_requires_login(self):
        anonymous = self.app.test_client()
        token = _csrf(anonymous.get("/login").get_data(as_text=True))
        response = anonymous.post(
            "/replay/1/note",
            data={"question_id": 1, "content": "x", "csrf_token": token},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_ownership_isolation_and_direct_manipulation_blocked(self):
        self._complete_practice_session(topic="Victim note topic")
        victim_interview = self._latest_interview_id()
        victim_q = self._questions_of(victim_interview)[0]
        self._post_note(victim_interview, victim_q, "Victim's private note.", q=1)

        attacker = self._register_attacker()
        attacker_token = self._attacker_csrf(attacker)

        attacker_index = attacker.get("/replay").get_data(as_text=True)
        self.assertNotIn("Victim note topic", attacker_index)
        self.assertNotIn("Victim's private note.", attacker_index)

        found = attacker.get(f"/replay/{victim_interview}")
        self.assertEqual(found.status_code, 404)
        blocked = self._post_note(
            victim_interview, victim_q, "Mallory was here", q=1,
            client=attacker, token=attacker_token,
        )
        self.assertEqual(blocked.status_code, 404)
        self.assertEqual(len(self._note_rows()), 1)
        self.assertEqual(self._note_rows()[0]["content"], "Victim's private note.")

        cleared = self._post_note(
            victim_interview, victim_q, action="clear", q=1,
            client=attacker, token=attacker_token,
        )
        self.assertEqual(cleared.status_code, 404)
        self.assertEqual(len(self._note_rows()), 1)

    def test_question_from_another_interview_cannot_receive_note(self):
        self._complete_practice_session(topic="Victim other session")
        victim_interview = self._latest_interview_id()
        victim_q = self._questions_of(victim_interview)[0]
        self._post_note(victim_interview, victim_q, "Existing note.", q=1)

        attacker = self._register_attacker()
        attacker_token = self._attacker_csrf(attacker)
        attacker.post("/practice/question", data={
            "topic": "Attacker topic", "difficulty": "easy",
            "csrf_token": attacker_token,
        })

        cross = self._post_note(
            self._latest_interview_id(), victim_q, "Cross-session", q=1,
            client=attacker, token=attacker_token,
        )
        self.assertEqual(cross.status_code, 404)
        self.assertEqual(self._note_rows("mallory@example.com"), [])

    def test_nonexistent_interview_and_question_references_fail_safely(self):
        self._complete_practice_session(topic="Safe note topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]
        token = _csrf(self.client.get(f"/replay/{interview_id}").get_data(as_text=True))

        missing_interview = self.client.post(
            "/replay/424242/note",
            data={"question_id": str(question_id), "content": "x",
                  "csrf_token": token},
        )
        self.assertEqual(missing_interview.status_code, 404)

        missing_q = self._post_note(interview_id, 999999, "x")
        self.assertEqual(missing_q.status_code, 404)

        no_q = self.client.post(
            f"/replay/{interview_id}/note",
            data={"content": "x", "csrf_token": token},
        )
        self.assertEqual(no_q.status_code, 404)

        non_numeric = self.client.post(
            f"/replay/{interview_id}/note",
            data={"question_id": "abc", "content": "x", "csrf_token": token},
        )
        self.assertEqual(non_numeric.status_code, 404)
        self.assertEqual(len(self._note_rows()), 0)

    def test_missing_csrf_token_rejected(self):
        self._complete_practice_session(topic="Csrf note topic")
        interview_id = self._latest_interview_id()
        question_id = self._questions_of(interview_id)[0]
        response = self.client.post(
            f"/replay/{interview_id}/note",
            data={"question_id": str(question_id), "content": "x"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self._note_rows()), 0)

    # ------------------------------------------------------------------
    # Bookmark coexistence
    # ------------------------------------------------------------------

    def test_bookmark_and_note_coexist_independently(self):
        interview_id, first, _ = self._complete_two_question_session()

        self._bookmark(interview_id, first, q=1)
        self._post_note(interview_id, first, "Noted and saved.", q=1)
        self.assertEqual(len(self._bookmark_rows()), 1)
        self.assertEqual(len(self._note_rows()), 1)

        html = self.client.get(f"/replay/{interview_id}?q=1").get_data(as_text=True)
        self.assertIn("bookmark-toggle is-bookmarked", html)
        self.assertIn("Noted and saved.", html)

        # Clearing the note must not touch the bookmark...
        self._post_note(interview_id, first, action="clear", q=1)
        self.assertEqual(len(self._note_rows()), 0)
        self.assertEqual(len(self._bookmark_rows()), 1)

        # ...and unbookmarking must not delete the note.
        self._post_note(interview_id, first, "Noted again.", q=1)
        self.assertEqual(len(self._note_rows()), 1)
        self._bookmark(interview_id, first, q=1)  # toggle off
        self.assertEqual(len(self._bookmark_rows()), 0)
        self.assertEqual(len(self._note_rows()), 1)


if __name__ == "__main__":
    unittest.main()