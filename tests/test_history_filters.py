"""History search + advanced filter coverage (Stage: History).

Exercises /history query-string filtering server-side: free-text search
(role/topic and real interview-type label), difficulty, real interview type,
inclusive date range, and their combinations. Reuses the stage-4 app/DB
fixture and fake Gemini transport; does not change the schema.

Contract notes copied from the existing suite that must keep passing:
  - mode=bogus falls back to all sessions;
  - every filtered-result or empty state still renders "No completed
    sessions" so earlier assertions stay true.
"""
import re

from tests.test_stage4 import Stage4TestBase

_LIST_RE = re.compile(r'<ul class="history-list">(.*?)</ul>', re.S)
_CHIP_HREF_RE = re.compile(r'iq-filter-chip[^>]*href="([^"]+)"')


class HistoryFilterTests(Stage4TestBase):
    """One logged-in user + sessions queried through the list route."""

    def _latest_interview_id(self):
        db = self._db()
        row = db.execute("SELECT MAX(id) FROM interviews").fetchone()
        db.close()
        return row[0]

    def _ensure_login(self):
        """Register (once) unless this client already has a live session."""
        response = self.client.get("/dashboard")
        if response.status_code == 200:
            return
        self._register_and_login()

    def _complete_practice_session(self, topic="Database normalization",
                                   difficulty="easy"):
        self._ensure_login()
        self._practice_ask(topic=topic, difficulty=difficulty)
        self._practice_answer(self._practice_question_id(), "My answer.")

    def _start_real_as(self, role="Backend Developer", type_key="technical",
                       difficulty="easy"):
        token = self._get_csrf("/interview")
        return self.client.post("/interview/start", data={
            "role": role, "type": type_key, "difficulty": difficulty,
            "csrf_token": token,
        })

    def _complete_real(self, role="Backend Developer", type_key="technical",
                       difficulty="easy"):
        self._ensure_login()
        self._start_real_as(role=role, type_key=type_key,
                            difficulty=difficulty)
        interview_id = self._latest_interview_id()
        self._submit_real_answer(interview_id, "Buzzer-beater answer.")
        # Expire the session: easy/medium/hard run 12/20/30 minutes.
        self._backdate({"easy": 13, "medium": 21, "hard": 31}[difficulty])
        response = self.client.get(f"/interview/{interview_id}")  # finalize
        assert response.status_code == 302, (
            "finalize", response.status_code, response.headers.get("Location")
        )
        return interview_id

    def _set_interview_date(self, interview_id, when):
        db = self._db()
        db.execute(
            "UPDATE interviews SET date = ? WHERE id = ?",
            (f"{when} 09:00:00", interview_id),
        )
        db.commit()
        db.close()

    def _history(self, **params):
        if "from_" in params:
            params["from"] = params.pop("from_")
        return self.client.get("/history", query_string=params)

    def _list_block(self, response):
        html = response if isinstance(response, str) else \
            response.get_data(as_text=True)
        match = _LIST_RE.search(html)
        return match.group(1) if match else ""

    # ------------------------------------------------------------------
    # Free-text search
    # ------------------------------------------------------------------

    def test_search_matches_topic_case_insensitive(self):
        self._complete_practice_session(topic="React hooks")

        block = self._list_block(self._history(q="react"))
        self.assertIn("React hooks", block)

        block = self._list_block(self._history(q="REACT"))
        self.assertIn("React hooks", block)

        block = self._list_block(self._history(q="zzzz-not-there"))
        self.assertEqual(block, "")

    def test_search_matches_real_role_and_type_label(self):
        self._complete_real(role="Backend Developer", type_key="technical")

        block = self._list_block(self._history(q="backend"))
        self.assertIn("Backend Developer", block)

        block = self._list_block(self._history(q="technical"))
        self.assertIn("Backend Developer", block)   # label match, not topic

    def test_search_requires_all_terms(self):
        self._complete_practice_session(topic="SQL joins")

        block = self._list_block(self._history(q="SQL joins"))
        self.assertIn("SQL joins", block)

        block = self._list_block(self._history(q="SQL database"))
        self.assertEqual(block, "")                 # AND across terms

    def test_search_escapes_like_wildcards(self):
        self._complete_practice_session(topic="SQL joins")

        block = self._list_block(self._history(q="SQL%"))
        self.assertEqual(block, "")                 # literal %, no wildcard

        block = self._list_block(self._history(q="SQL_joins"))
        self.assertEqual(block, "")                 # literal _

        block = self._list_block(self._history(q="100%_io"))
        self.assertEqual(block, "")

    def test_blank_and_oversized_q_are_harmless(self):
        self._complete_practice_session(topic="SQL joins")

        block = self._list_block(self._history(q="   "))
        self.assertIn("SQL joins", block)

        block = self._list_block(self._history(q="x" * 500))
        self.assertEqual(block, "")

    # ------------------------------------------------------------------
    # Advanced filters
    # ------------------------------------------------------------------

    def test_difficulty_filter(self):
        self._complete_practice_session(topic="Python dicts", difficulty="easy")
        self._complete_practice_session(topic="Rust lifetimes",
                                        difficulty="hard")

        block = self._list_block(self._history(difficulty="hard"))
        self.assertIn("Rust lifetimes", block)
        self.assertNotIn("Python dicts", block)

        block = self._list_block(self._history(difficulty="easy"))
        self.assertNotIn("Rust lifetimes", block)
        self.assertIn("Python dicts", block)

        block = self._list_block(self._history(difficulty="medium"))
        self.assertEqual(block, "")

    def test_type_filter_only_applies_to_real(self):
        self._complete_practice_session(topic="SQL joins")
        self._complete_real(role="Backend Developer", type_key="technical")

        block = self._list_block(self._history(type="technical"))
        self.assertIn("Backend Developer", block)
        self.assertNotIn("SQL joins", block)        # practice topics excluded

        block = self._list_block(self._history(type="behavioral"))
        self.assertEqual(block, "")

    def test_type_filter_ignored_while_browsing_practice(self):
        self._complete_practice_session(topic="SQL joins")
        self._complete_real(role="Backend Developer", type_key="technical")

        block = self._list_block(self._history(mode="practice", type="technical"))
        self.assertIn("SQL joins", block)

    def test_date_range_filter(self):
        first = self._latest_interview_id() or 0
        self._complete_practice_session(topic="Alpha session")
        alpha_id = self._latest_interview_id()
        self.assertTrue(alpha_id > first)
        self._set_interview_date(alpha_id, "2026-05-10")

        self._complete_practice_session(topic="Beta session")
        beta_id = self._latest_interview_id()
        self.assertTrue(beta_id > alpha_id)
        self._set_interview_date(beta_id, "2026-05-20")

        block = self._list_block(self._history(from_="2026-05-11", to="2026-05-30"))
        self.assertIn("Beta session", block)
        self.assertNotIn("Alpha session", block)

        block = self._list_block(self._history(from_="2026-04-01", to="2026-04-30"))
        self.assertEqual(block, "")

        block = self._list_block(self._history(from_="not-a-date"))
        self.assertIn("Alpha session", block)       # invalid -> ignored
        self.assertIn("Beta session", block)

    def test_invalid_filter_values_are_ignored(self):
        self._complete_practice_session(topic="Alpha session")
        self._complete_real(role="Backend Developer", type_key="technical")

        block = self._list_block(self._history(
            difficulty="bogus", type="bogus", q="   ", from_="junk",
        ))
        self.assertIn("Alpha session", block)
        self.assertIn("Backend Developer", block)

    # ------------------------------------------------------------------
    # Combinations
    # ------------------------------------------------------------------

    def test_search_plus_mode(self):
        self._complete_real(role="Backend Developer", type_key="technical")
        self._complete_practice_session(topic="Github Actions")

        block = self._list_block(self._history(mode="practice", q="github"))
        self.assertIn("Github Actions", block)
        self.assertNotIn("Backend Developer", block)

        block = self._list_block(self._history(mode="real", q="github"))
        self.assertEqual(block, "")

    def test_search_plus_difficulty_plus_mode(self):
        self._complete_practice_session(topic="SQL joins", difficulty="easy")
        self._complete_practice_session(topic="Golang interfaces",
                                        difficulty="hard")
        self._complete_real(role="Backend Developer", type_key="technical",
                            difficulty="hard")

        block = self._list_block(self._history(
            mode="practice", q="joins", difficulty="easy",
        ))
        self.assertIn("SQL joins", block)
        self.assertNotIn("Golang interfaces", block)
        self.assertNotIn("Backend Developer", block)

    # ------------------------------------------------------------------
    # Ownership
    # ------------------------------------------------------------------

    def test_search_is_scoped_to_owner(self):
        self._complete_practice_session(topic="Alice private topic")

        attacker = self.app.test_client()
        page = attacker.get("/register")
        token = re.search(r'name="csrf_token" value="([^"]+)"',
                          page.get_data(as_text=True)).group(1)
        attacker.post("/register", data={
            "name": "Mallory", "email": "mallory@example.com",
            "password": "password1", "confirm": "password1",
            "csrf_token": token,
        })

        html = attacker.get("/history?q=alice").get_data(as_text=True)
        self.assertNotIn("Alice private topic", html)
        self.assertIn("No completed sessions", html)

        block = self._list_block(self._history(q="alice"))
        self.assertIn("Alice private topic", block)

    # ------------------------------------------------------------------
    # Template affordances
    # ------------------------------------------------------------------

    def test_clear_search_link_keeps_other_filters(self):
        self._complete_practice_session(topic="SQL joins", difficulty="hard")

        html = self._history(q="joins", difficulty="hard").get_data(as_text=True)
        self.assertIn('name="q"', html)
        self.assertIn("Clear search", html)

        clear = re.search(r'iq-search-clear" href="([^"]+)"', html)
        self.assertIsNotNone(clear)
        self.assertNotIn("q=", clear.group(1))      # search dropped
        self.assertIn("difficulty=hard", clear.group(1))

    def test_chips_preserve_active_search_and_filters(self):
        self._complete_practice_session(topic="SQL joins", difficulty="hard")

        html = self._history(q="joins", difficulty="hard").get_data(as_text=True)
        hrefs = _CHIP_HREF_RE.findall(html)
        self.assertTrue(any(
            "mode=practice" in href and "q=joins" in href
            and "difficulty=hard" in href for href in hrefs
        ))
        self.assertTrue(any(
            "mode=real" in href and "q=joins" in href for href in hrefs
        ))

    def test_filtered_empty_state_clears_filters(self):
        self._complete_practice_session(topic="SQL joins")

        html = self._history(q="missing-topic").get_data(as_text=True)
        self.assertIn("No completed sessions", html)
        self.assertIn("Clear filters", html)

        html = self._history().get_data(as_text=True)
        self.assertNotIn("Clear filters", html)