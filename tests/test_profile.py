"""Focused tests for the Profile page (blueprint Sections B.2/D/E).

No real API calls are made: the Gemini service bound to the app uses a fake
transport (only needed for the personalization tests). All payloads are
synthetic fixtures.

Run:  .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

import json
import os
import re
import sqlite3
import tempfile
import unittest

from interview_Ai.app.ai.gemini import GeminiService
from interview_Ai.app.config import Config
from interview_Ai.app.validation import (
    MAX_PROFILE_SKILLS,
    SKILL_MAX_LENGTH,
    parse_skills,
)

SECRET_TEST_KEY = "test-secret-key"

QUESTION_PAYLOAD = {
    "question": "Explain how a B-tree index speeds up queries.",
    "question_type": "conceptual",
    "expected_concepts": ["B-tree structure"],
}

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _csrf(html):
    match = CSRF_RE.search(html)
    if not match:
        raise AssertionError("No CSRF token found on page.")
    return match.group(1)


class ProfileTestBase(unittest.TestCase):
    """Boots an isolated app with a fake Gemini service and a temp DB."""

    def setUp(self):
        os.environ.pop("GEMINI_API_KEY", None)
        self.tmp = tempfile.mkdtemp()
        self.calls = []

        from interview_Ai.app import create_app

        class TestConfig(Config):
            TESTING = True
            DATABASE_PATH = os.path.join(self.tmp, "profile_test.db")

        self.db_path = TestConfig.DATABASE_PATH
        self.app = create_app(TestConfig)
        self.app.extensions["gemini"] = GeminiService(
            api_key=SECRET_TEST_KEY, max_retries=0,
            transport=self._fake_transport,
        )
        self.client = self.app.test_client()

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def _fake_transport(self, system, user, temperature, max_output_tokens):
        self.calls.append(user)
        if user.startswith("Create one interview question"):
            return json.dumps(QUESTION_PAYLOAD)
        raise AssertionError(f"Unexpected prompt reached transport: {user[:80]}")

    def _register_and_login(self, email="student@example.com",
                            name="Test Student"):
        page = self.client.get("/register")
        token = _csrf(page.get_data(as_text=True))
        self.client.post("/register", data={
            "name": name,
            "email": email,
            "password": "password1",
            "confirm": "password1",
            "csrf_token": token,
        })
        return email

    def _get_profile(self):
        page = self.client.get("/profile")
        return page, _csrf(page.get_data(as_text=True))

    def _update_profile(self, name="Test Student", role="", skills="",
                        csrf=None):
        token = csrf or _csrf(self.client.get("/profile").get_data(as_text=True))
        return self.client.post("/profile", data={
            "name": name,
            "role": role,
            "skills": skills,
            "csrf_token": token,
        })

    def _db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


class AuthAndAccessTests(ProfileTestBase):
    def test_profile_requires_login(self):
        response = self.client.get("/profile")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].startswith("/login"))

    def test_profile_update_requires_login(self):
        # No CSRF token at all: rejected by CSRF protection (which runs
        # before authentication) before any handler sees the request.
        response = self.client.post("/profile", data={
            "name": "Ghost", "role": "Hacker", "skills": "",
        })
        self.assertEqual(response.status_code, 400)

        # A valid CSRF token does not help either: without a session the
        # view still bounces to login and nothing is written.
        page = self.client.get("/login")
        token = _csrf(page.get_data(as_text=True))
        response = self.client.post("/profile", data={
            "name": "Ghost", "role": "Hacker", "skills": "",
            "csrf_token": token,
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].startswith("/login"))

        db = self._db()
        users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        db.close()
        self.assertEqual(users, 0)

    def test_profile_page_renders_saved_data(self):
        self._register_and_login(name="Ada Lovelace")
        page, token = self._get_profile()
        html = page.get_data(as_text=True)

        self.assertEqual(page.status_code, 200)
        self.assertIn('value="Ada Lovelace"', html)
        self.assertIn("student@example.com", html)
        self.assertNotIn(SECRET_TEST_KEY, html)  # key never reaches client


class UpdatePersistenceTests(ProfileTestBase):
    def test_successful_update_persists_all_fields(self):
        self._register_and_login()
        response = self._update_profile(
            name="Grace Hopper",
            role="Data Analyst",
            skills="Python, SQL, Statistics",
        )
        self.assertEqual(response.status_code, 302)

        page = self.client.get("/profile")
        html = page.get_data(as_text=True)
        self.assertIn("Profile updated.", html)
        self.assertIn('value="Grace Hopper"', html)
        self.assertIn('value="Data Analyst"', html)
        self.assertIn("Python", html)

        # Sidebar reflects the fresh name too.
        self.assertIn("Grace Hopper", html)

        db = self._db()
        user = db.execute("SELECT * FROM users").fetchone()
        profile = db.execute("SELECT * FROM profiles").fetchone()
        db.close()

        self.assertEqual(user["name"], "Grace Hopper")
        self.assertEqual(profile["role"], "Data Analyst")
        self.assertEqual(
            json.loads(profile["skills"]),
            ["Python", "SQL", "Statistics"],
        )

    def test_partial_update_preserves_other_data(self):
        self._register_and_login()
        db = self._db()
        db.execute(
            "UPDATE profiles SET resume_path = ?",
            ("stored/resume.pdf",),
        )
        db.commit()
        db.close()

        # Only change the skills; name and role must stay untouched.
        self._update_profile(skills="Git, HTTP")

        db = self._db()
        user = db.execute("SELECT * FROM users").fetchone()
        profile = db.execute("SELECT * FROM profiles").fetchone()
        db.close()

        self.assertEqual(user["name"], "Test Student")
        self.assertEqual(profile["role"], "")
        self.assertEqual(json.loads(profile["skills"]), ["Git", "HTTP"])
        self.assertEqual(profile["resume_path"], "stored/resume.pdf")

    def test_clearing_role_and_skills_is_allowed(self):
        self._register_and_login()
        self._update_profile(role="Backend Dev", skills="Flask")
        response = self._update_profile(role="", skills="")
        self.assertEqual(response.status_code, 302)

        db = self._db()
        profile = db.execute("SELECT * FROM profiles").fetchone()
        db.close()

        self.assertEqual(profile["role"], "")
        self.assertEqual(json.loads(profile["skills"]), [])

        page = self.client.get("/profile")
        html = page.get_data(as_text=True)
        self.assertIn('value=""', html)  # inputs cleared

    def test_unchanged_resubmission_succeeds(self):
        self._register_and_login()
        first = self._update_profile(role="QA Engineer", skills="pytest")
        second = self._update_profile(role="QA Engineer", skills="pytest")
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)

        page = self.client.get("/profile")
        self.assertIn("Profile updated.", page.get_data(as_text=True))

        db = self._db()
        count = db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
        db.close()
        self.assertEqual(count, 1)


class ProfileValidationTests(ProfileTestBase):
    def _assert_rejected(self, response, message_fragment, field_sql,
                         expected):
        self.assertEqual(response.status_code, 200)
        self.assertIn(message_fragment, response.get_data(as_text=True))
        db = self._db()
        value = db.execute(f"SELECT {field_sql} FROM profiles").fetchone()[0]
        db.close()
        self.assertEqual(value, expected)

    def test_short_name_rejected_and_nothing_saved(self):
        self._register_and_login()
        response = self._update_profile(name="A", role="New Role")
        self._assert_rejected(response, "at least 2 characters", "role", "")

    def test_long_name_rejected(self):
        self._register_and_login()
        response = self._update_profile(name="x" * 81)
        self.assertIn("80 characters or fewer",
                      response.get_data(as_text=True))

        db = self._db()
        name = db.execute("SELECT name FROM users").fetchone()[0]
        db.close()
        self.assertEqual(name, "Test Student")

    def test_empty_name_rejected(self):
        self._register_and_login()
        response = self._update_profile(name="   ")
        self.assertIn("Name is required.", response.get_data(as_text=True))

    def test_long_role_rejected(self):
        self._register_and_login()
        response = self._update_profile(role="r" * 81)
        self._assert_rejected(
            response, "Target role must be 80 characters", "role", ""
        )

    def test_too_many_skills_rejected(self):
        self._register_and_login()
        skills = ", ".join(f"skill{i}" for i in range(MAX_PROFILE_SKILLS + 1))
        response = self._update_profile(skills=skills)
        self._assert_rejected(
            response, f"at most {MAX_PROFILE_SKILLS} skills", "skills", "[]"
        )

    def test_oversized_skill_rejected(self):
        self._register_and_login()
        response = self._update_profile(skills="s" * (SKILL_MAX_LENGTH + 1))
        self.assertIn("50 characters or fewer",
                      response.get_data(as_text=True))

        db = self._db()
        raw = db.execute("SELECT skills FROM profiles").fetchone()[0]
        db.close()
        self.assertEqual(json.loads(raw), [])

    def test_skills_are_trimmed_deduplicated_persisted(self):
        self._register_and_login()
        self._update_profile(skills="  Python , sql , PYTHON ,, \nData  ")
        self.assertEqual(self.client.get("/profile").status_code, 200)

        db = self._db()
        raw = db.execute("SELECT skills FROM profiles").fetchone()[0]
        db.close()
        self.assertEqual(json.loads(raw), ["Python", "sql", "Data"])


class ValidationUnitTests(unittest.TestCase):
    """parse_skills contract without any Flask machinery."""

    def test_handles_newlines_and_commas(self):
        skills, error = parse_skills("A\nB, C\n\n D ")
        self.assertIsNone(error)
        self.assertEqual(skills, ["A", "B", "C", "D"])

    def test_empty_input_yields_empty_list(self):
        skills, error = parse_skills("  ,,, ")
        self.assertIsNone(error)
        self.assertEqual(skills, [])


class CsrfTests(ProfileTestBase):
    def test_post_without_csrf_token_is_rejected(self):
        self._register_and_login()
        response = self.client.post("/profile", data={
            "name": "Attacker Rename",
            "role": "Evil Role",
            "skills": "none",
        })
        self.assertEqual(response.status_code, 400)

        db = self._db()
        name = db.execute("SELECT name FROM users").fetchone()[0]
        profile = db.execute("SELECT role FROM profiles").fetchone()[0]
        db.close()
        self.assertEqual(name, "Test Student")
        self.assertEqual(profile, "")


class IsolationTests(ProfileTestBase):
    def test_profiles_are_scoped_per_user(self):
        self._register_and_login(email="alice@example.com", name="Alice")
        self._update_profile(role="Alice Role", skills="Alice Skill")

        mallory = self.app.test_client()
        page = mallory.get("/register")
        token = _csrf(page.get_data(as_text=True))
        mallory.post("/register", data={
            "name": "Mallory",
            "email": "mallory@example.com",
            "password": "password1",
            "confirm": "password1",
            "csrf_token": token,
        })

        # Mallory sees only her own (empty) data, never Alice's.
        html = mallory.get("/profile").get_data(as_text=True)
        self.assertNotIn("Alice Role", html)
        self.assertNotIn("Alice Skill", html)
        self.assertNotIn("Alice</span>", html)

        # Mallory's own update leaves Alice's row untouched.
        page = mallory.get("/profile")
        token = _csrf(page.get_data(as_text=True))
        response = mallory.post("/profile", data={
            "name": "Mallory",
            "role": "Mallory Role",
            "skills": "",
            "csrf_token": token,
        })
        self.assertEqual(response.status_code, 302)

        db = self._db()
        rows = {
            row["user_id"]: row["role"]
            for row in db.execute(
                "SELECT p.user_id, p.role FROM profiles p "
                "JOIN users u ON u.id = p.user_id"
            ).fetchall()
        }
        names = {
            row["email"]: row["role"]
            for row in db.execute(
                "SELECT u.email, p.role FROM profiles p "
                "JOIN users u ON u.id = p.user_id"
            ).fetchall()
        }
        db.close()

        self.assertEqual(names["alice@example.com"], "Alice Role")
        self.assertEqual(names["mallory@example.com"], "Mallory Role")
        self.assertEqual(len(rows), 2)


class PersonalizationTests(ProfileTestBase):
    def test_saved_role_feeds_practice_question_generation(self):
        self._register_and_login()
        self._update_profile(role="Data Analyst")

        # Picker shows the personalized role label instead of the fallback.
        picker = self.client.get("/practice").get_data(as_text=True)
        self.assertIn("Data Analyst", picker)
        self.assertNotIn("Software Engineer", picker)

        token = _csrf(picker)
        response = self.client.post("/practice/question", data={
            "topic": "SQL joins",
            "difficulty": "easy",
            "csrf_token": token,
        })
        self.assertEqual(response.status_code, 302)

        # The generation prompt carries the saved role, and the session row
        # records it.
        self.assertEqual(len(self.calls), 1)
        self.assertIn("Data Analyst", self.calls[0])

        db = self._db()
        interview = db.execute("SELECT * FROM interviews").fetchone()
        db.close()
        self.assertEqual(interview["role"], "Data Analyst")

    def test_saved_role_feeds_real_interview_generation(self):
        self._register_and_login()
        self._update_profile(role="Cloud Engineer")

        # The config screen prefills its Target role input from the saved
        # profile role (role_label), so the browser submits it on start.
        config_page = self.client.get("/interview")
        config_html = config_page.get_data(as_text=True)
        self.assertIn('value="Cloud Engineer"', config_html)

        token = _csrf(config_html)
        response = self.client.post("/interview/start", data={
            "role": "Cloud Engineer",
            "type": "technical",
            "difficulty": "easy",
            "csrf_token": token,
        })
        self.assertEqual(response.status_code, 302)

        self.assertEqual(len(self.calls), 1)
        self.assertIn("Cloud Engineer", self.calls[0])

        db = self._db()
        interview = db.execute("SELECT * FROM interviews").fetchone()
        db.close()
        self.assertEqual(interview["mode"], "real")
        self.assertEqual(interview["role"], "Cloud Engineer")

    def test_cleared_role_falls_back_to_default_label(self):
        self._register_and_login()
        self._update_profile(role="Temporary Role")
        self._update_profile(role="")

        picker = self.client.get("/practice").get_data(as_text=True)
        self.assertIn("Software Engineer", picker)
        self.assertNotIn("Temporary Role", picker)


if __name__ == "__main__":
    unittest.main(verbosity=2)
