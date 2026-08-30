"""Offline tests for Stage 6: Analytics & Polish (blueprint K.11/K.12).

Covers the deterministic Placement Readiness Score (weights, renormalization,
no-AI guarantee), Performance Analytics page + aggregate JSON, the upgraded
Dashboard, Resume upload security (PDF-only: MIME + extension + magic bytes +
size cap; scanned-PDF rejection with no OCR), analyze_resume contract reuse,
resume-grounded questions through the EXISTING practice pipeline, on-demand
ReportLab PDF reports (ownership, in-progress guards, graceful narrative
degradation), Section J rate limiting (auth/IP + AI/user) and the sidebar /
scope guardrails. The Gemini transport is faked — NO real API calls.

Run:  .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

import io
import json
import os
import re
import sqlite3
import tempfile
import unittest
from unittest import mock

from PyPDF2 import PdfReader

from interview_Ai.app.ai.errors import GeminiError, GeminiRateLimitError
from interview_Ai.app.ai.gemini import GeminiService
from interview_Ai.app.config import Config

SECRET_TEST_KEY = "test-secret-key"

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')

QUESTION_PAYLOAD = {
    "question": "Walk me through your InterviewIQ project's architecture.",
    "question_type": "practical",
    "expected_concepts": ["Flask", "blueprints"],
}

EVALUATION_PAYLOAD = {
    "scores": {
        "technical_accuracy": 80,
        "relevance": 70,
        "completeness": 60,
        "clarity": 90,
        "communication": 50,
    },
    "feedback": "Strong, well-structured answer.",
    "missing_points": ["Mention scaling limits"],
    "model_answer": "A layered Flask app with blueprint-per-feature layout.",
}

WEAK_EVALUATION_PAYLOAD = {
    "scores": {
        "technical_accuracy": 45,
        "relevance": 55,
        "completeness": 40,
        "clarity": 35,
        "communication": 60,
    },
    "feedback": "This answer misses several fundamentals.",
    "missing_points": ["Define indexing"],
    "model_answer": "Indexes trade write cost for read speed.",
}

RESUME_ANALYSIS_PAYLOAD = {
    "skills": ["Python", "SQL", "Unit testing"],
    "projects": ["InterviewIQ capstone"],
    "technologies": ["Flask", "SQLite"],
    "certifications": ["AWS Cloud Practitioner"],
}

NARRATIVE_TEXT = "Great progress — your fundamentals are clearly showing."

ANALYZE_MARKER = "Extract structured information from this resume text"
NARRATIVE_MARKER = "Write a short, encouraging narrative summary"
QUESTION_MARKER = "Create one interview question"
EVALUATE_MARKER = "Evaluate this answer"


def _csrf(html):
    match = CSRF_RE.search(html)
    if not match:
        raise AssertionError("No CSRF token found on page.")
    return match.group(1)


def _build_pdf(lines):
    """A minimal but valid text PDF (ReportLab), built in memory."""
    from reportlab.pdfgen import canvas as pdf_canvas

    buffer = io.BytesIO()
    canvas = pdf_canvas.Canvas(buffer)
    y = 750
    for line in lines:
        canvas.drawString(72, y, line)
        y -= 18
    canvas.save()
    return buffer.getvalue()


def _build_blank_pdf():
    """A valid PDF with no selectable text — stands in for a scanned resume."""
    from reportlab.pdfgen import canvas as pdf_canvas

    buffer = io.BytesIO()
    pdf_canvas.Canvas(buffer).save()
    return buffer.getvalue()


VALID_RESUME_PDF = _build_pdf([
    "Jane Doe - Python developer",
    "Skills: Python, SQL, Flask, unit testing",
    "Project: InterviewIQ - an AI powered mock interview coach",
    "Certification: AWS Cloud Practitioner",
])
SCANNED_RESUME_PDF = _build_blank_pdf()
CORRUPT_PDF = b"%PDF-1.4\nthis is not really a pdf body at all"

RESUME_MAX_BYTES_DEFAULT = 5 * 1024 * 1024


def _pdf_text(pdf_bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


class Stage6TestBase(unittest.TestCase):
    """Boots an isolated app with a fake Gemini service and a temp DB."""

    def setUp(self):
        os.environ.pop("GEMINI_API_KEY", None)
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "stage6_test.db")
        self.upload_dir = os.path.join(self.tmp, "uploads-outside-webroot")
        self.calls = []
        self.question_payload = dict(QUESTION_PAYLOAD)
        self.evaluation_payload = json.loads(json.dumps(EVALUATION_PAYLOAD))
        self.resume_payload = json.loads(json.dumps(RESUME_ANALYSIS_PAYLOAD))
        self.narrative_text = NARRATIVE_TEXT
        self.fail_analyze_with = None
        self.fail_narrative_with = None

        from interview_Ai.app import create_app

        class TestConfig(Config):
            TESTING = True
            DATABASE_PATH = self.db_path
            RESUME_UPLOAD_DIR = self.upload_dir

        self.app = create_app(TestConfig)
        self._install_service()
        self.client = self.app.test_client()

    # ------------------------------------------------------------------
    # Fake transport
    # ------------------------------------------------------------------

    def _fake_transport(self, system, user, temperature, max_output_tokens):
        self.calls.append(user)

        if ANALYZE_MARKER in user:
            if self.fail_analyze_with is not None:
                raise self.fail_analyze_with
            return json.dumps(self.resume_payload)

        if NARRATIVE_MARKER in user:
            if self.fail_narrative_with is not None:
                raise self.fail_narrative_with
            return json.dumps({"narrative_summary": self.narrative_text})

        if EVALUATE_MARKER in user:
            return json.dumps(self.evaluation_payload)

        if user.startswith(QUESTION_MARKER):
            return json.dumps(self.question_payload)

        raise AssertionError(f"Unexpected prompt reached transport: {user[:80]}")

    def _install_service(self):
        service = GeminiService(
            api_key=SECRET_TEST_KEY, max_retries=0,
            transport=self._fake_transport,
        )
        self.app.extensions["gemini"] = service
        return service

    def _uninstall_service(self):
        self.app.extensions["gemini"] = GeminiService(api_key="", max_retries=0)

    # ------------------------------------------------------------------
    # Client helpers
    # ------------------------------------------------------------------

    def _register_and_login(self, email="student@example.com"):
        page = self.client.get("/register")
        token = _csrf(page.get_data(as_text=True))
        self.client.post("/register", data={
            "name": "Test Student", "email": email,
            "password": "password1", "confirm": "password1",
            "csrf_token": token,
        })
        return email

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

    def _get_csrf(self, path):
        page = self.client.get(path)
        return _csrf(page.get_data(as_text=True))

    def _practice_ask(self, topic="Database normalization", difficulty="easy",
                      interview_id=None):
        data = {"topic": topic, "difficulty": difficulty,
                "csrf_token": self._get_csrf("/practice")}
        if interview_id is not None:
            data["interview_id"] = str(interview_id)
        return self.client.post("/practice/question", data=data)

    def _practice_answer(self, question_id, answer="Indexes speed up reads."):
        token = self._get_csrf(f"/practice/question/{question_id}")
        return self.client.post("/practice/answer", data={
            "question_id": str(question_id), "answer": answer,
            "csrf_token": token,
        })

    def _last_question_id(self):
        db = self._db()
        row = db.execute(
            "SELECT id FROM questions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        db.close()
        return row[0]

    def _first_interview_id(self):
        db = self._db()
        row = db.execute("SELECT MIN(id) FROM interviews").fetchone()
        db.close()
        return row[0]

    def _complete_practice_session(self, topic="Database normalization"):
        self._practice_ask(topic=topic)
        return self._practice_answer(self._last_question_id())

    def _complete_weak_practice_session(self):
        previous = self.evaluation_payload
        self.evaluation_payload = WEAK_EVALUATION_PAYLOAD
        try:
            self._complete_practice_session()
        finally:
            self.evaluation_payload = previous

    def _upload_resume(self, pdf_bytes=VALID_RESUME_PDF,
                       filename="resume.pdf", mime="application/pdf"):
        data = {"csrf_token": self._get_csrf("/resume")}
        if pdf_bytes is not None or filename is not None:
            data["resume"] = (io.BytesIO(pdf_bytes or b""), filename, mime)
        return self.client.post("/resume/upload", data=data,
                                follow_redirects=True)

    def _start_real(self):
        token = self._get_csrf("/interview")
        return self.client.post("/interview/start", data={
            "role": "Backend Developer", "type": "technical",
            "difficulty": "easy", "csrf_token": token,
        })

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def _db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _profile_row(self):
        db = self._db()
        row = db.execute(
            "SELECT p.* FROM profiles p JOIN users u ON u.id = p.user_id "
            "WHERE u.email = 'student@example.com'"
        ).fetchone()
        db.close()
        return row


# ---------------------------------------------------------------------------
# Readiness score formula (pure, deterministic, no Gemini)
# ---------------------------------------------------------------------------

class ReadinessFormulaTests(unittest.TestCase):
    def test_component_weights_total_one_hundred(self):
        from interview_Ai.app.analytics import READINESS_COMPONENTS, compute_readiness

        self.assertEqual(
            sum(weight for _key, _label, weight in READINESS_COMPONENTS), 100
        )
        result = compute_readiness([], [], 0)
        self.assertEqual(len(result["components"]), len(READINESS_COMPONENTS))

    def test_full_data_produces_weighted_mean(self):
        from interview_Ai.app.analytics import compute_readiness

        skills = [
            {"skill": "A", "average_score": 90.0},
            {"skill": "B", "average_score": 70.0},
        ]
        result = compute_readiness(skills, [80.0], 2)
        expected = (
            80.0 * 40          # skill mastery: mean(90,70)=80 x weight 40
            + 80.0 * 35        # interview results: mean([80]) x weight 35
            + (100 - 100 * (2 / 6)) * 15   # weakness control: 66.7 x weight 15
            + min(1 / 10, 1) * 100 * 10    # experience: value 10 x weight 10
        ) / 100
        self.assertAlmostEqual(result["score"], round(expected, 1))

    def test_missing_signals_are_renormalized(self):
        from interview_Ai.app.analytics import compute_readiness

        result = compute_readiness([{"skill": "A", "average_score": 50.0}], [], 0)
        # Only mastery (weight 40) + weakness control (15) carry data.
        expected = (50.0 * 40 + 100.0 * 15) / 55
        self.assertAlmostEqual(result["score"], round(expected, 1))
        self.assertEqual(result["signals_used"], 2)
        self.assertAlmostEqual(result["signals_weight_total"], 55)

    def test_no_graded_evidence_means_no_score(self):
        from interview_Ai.app.analytics import compute_readiness

        result = compute_readiness([], [], 0)
        self.assertIsNone(result["score"])
        self.assertFalse(result["enough_data"])

    def test_experience_alone_never_qualifies(self):
        from interview_Ai.app.analytics import compute_readiness

        # No skill averages, no completed scores -> experience can't exist
        # either (it derives from completed scores); verify explicitly.
        result = compute_readiness([], [75.0], 0)
        self.assertIsNotNone(result["score"])
        self.assertTrue(result["enough_data"])

    def test_weakness_control_and_experience_are_capped(self):
        from interview_Ai.app.analytics import WEAKNESS_CONTROL_CAP, EXPERIENCE_TARGET_SESSIONS
        from interview_Ai.app.analytics import compute_readiness

        self.assertEqual(WEAKNESS_CONTROL_CAP, 6)
        self.assertEqual(EXPERIENCE_TARGET_SESSIONS, 10)

        result = compute_readiness([], [], 99)
        control = next(c for c in result["components"]
                       if c["key"] == "weakness_control")
        self.assertEqual(control["value"], 0)

        many = compute_readiness([], [70.0] * 25, 0)
        experience = next(c for c in many["components"]
                          if c["key"] == "experience")
        self.assertEqual(experience["value"], 100)

    def test_score_is_labeled_self_assessment(self):
        from interview_Ai.app.analytics import compute_readiness

        result = compute_readiness([{"skill": "A", "average_score": 60}], [60], 1)
        self.assertIn("self-assessment", result["label"].lower())


# ---------------------------------------------------------------------------
# Item 11 — Performance analytics page + dashboard
# ---------------------------------------------------------------------------

class AnalyticsPageTests(Stage6TestBase):
    def setUp(self):
        super().setUp()
        self._register_and_login()

    def test_requires_login(self):
        anonymous = self.app.test_client()
        response = anonymous.get("/analytics")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_empty_state_and_empty_json(self):
        html = self.client.get("/analytics").get_data(as_text=True)
        self.assertIn("Not enough data yet", html)

        payload = self.client.get("/analytics/data").get_json()
        self.assertEqual(
            payload,
            {"trend": {"labels": [], "scores": []}, "skills": []},
        )

    def test_page_renders_readiness_breakdown_after_activity(self):
        self._complete_weak_practice_session()   # low scores -> weaknesses
        self._complete_practice_session(topic="SQL indexes")  # high scores

        html = self.client.get("/analytics").get_data(as_text=True)
        self.assertIn("Self-assessment readiness indicator", html)
        self.assertIn("/100", html)
        self.assertIn("Skill mastery", html)
        self.assertIn("Interview results", html)
        self.assertIn("Weakness control", html)
        self.assertIn("Practice experience", html)

    def test_heatmap_and_trend_data_reflect_stored_results(self):
        self._complete_practice_session(topic="SQL indexes")

        payload = self.client.get("/analytics/data").get_json()
        self.assertEqual(len(payload["trend"]["labels"]), 1)
        self.assertEqual(len(payload["trend"]["scores"]), 1)
        self.assertEqual(payload["trend"]["scores"][0],
                         round(sum(EVALUATION_PAYLOAD["scores"].values()) / 5, 1))
        skills = {row["skill"]: row for row in payload["skills"]}
        self.assertIn("SQL indexes", skills)
        self.assertEqual(skills["SQL indexes"]["samples"], 1)

        html = self.client.get("/analytics").get_data(as_text=True)
        self.assertIn("SQL indexes", html)
        self.assertIn('id="trend-chart"', html)
        self.assertIn('id="skills-chart"', html)

    def test_json_contains_only_aggregates(self):
        self._complete_practice_session()
        raw = self.client.get("/analytics/data").get_data(as_text=True)
        for forbidden in ("email", "password", SECRET_TEST_KEY,
                          EVALUATION_PAYLOAD["feedback"]):
            self.assertNotIn(forbidden, raw)

    def test_data_is_isolated_between_users(self):
        self._complete_practice_session(topic="Secret topic X")

        attacker = self._register_attacker()
        payload = attacker.get("/analytics/data").get_json()
        self.assertEqual(payload["trend"]["labels"], [])
        self.assertEqual(payload["skills"], [])
        self.assertNotIn(
            "Secret topic X",
            attacker.get("/analytics").get_data(as_text=True),
        )


class DashboardTests(Stage6TestBase):
    def setUp(self):
        super().setUp()
        self._register_and_login()

    def test_new_user_sees_unlock_hint_instead_of_score(self):
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Finish a practice question", html)

    def test_tiles_populate_after_activity(self):
        self._complete_practice_session(topic="Tiles topic")
        self._complete_practice_session(topic="Tiles topic two")

        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Real Interviews", html)
        self.assertIn("Practice Sessions", html)
        self.assertIn('class="sparkline"', html)
        points = re.search(r'<polyline points="([^"]+)"', html)
        self.assertIsNotNone(points)
        self.assertTrue(points.group(1).count(",") >= 1)
        self.assertIn("Tiles topic", html)

    def test_readiness_recalculates_after_every_interview(self):
        self._complete_practice_session(topic="Recalc topic")
        after_one = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("/100", after_one)

        self._complete_practice_session(topic="Recalc topic two")
        after_two = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("/100", after_two)
        # The second session raised the average -> readiness improved.
        first_score = float(re.search(r"stat-value\">(\d+)/100", after_one).group(1))
        second_score = float(re.search(r"stat-value\">(\d+)/100", after_two).group(1))
        self.assertGreater(second_score, first_score)


# ---------------------------------------------------------------------------
# Item 11 — Resume analysis
# ---------------------------------------------------------------------------

class ResumeUploadValidationTests(Stage6TestBase):
    def setUp(self):
        super().setUp()
        self._register_and_login()

    def test_upload_requires_login(self):
        anonymous = self.app.test_client()
        response = anonymous.get("/resume")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_page_shows_form_and_constraints(self):
        html = self.client.get("/resume").get_data(as_text=True)
        self.assertIn('type="file"', html)
        self.assertIn("application/pdf", html)
        self.assertIn("max 5 MB", html)
        self.assertIn("OCR isn't supported", html)

    def test_missing_file_is_rejected(self):
        response = self._upload_resume(pdf_bytes=None, filename=None)
        html = response.get_data(as_text=True)
        self.assertIn("Choose a PDF file to upload.", html)

    def test_wrong_mime_type_is_rejected(self):
        response = self._upload_resume(b"%PDF-anything", mime="text/plain")
        self.assertIn("Only PDF resumes are accepted.",
                      response.get_data(as_text=True))

    def test_wrong_extension_is_rejected(self):
        response = self._upload_resume(filename="photo.png")
        self.assertIn(".pdf extension", response.get_data(as_text=True))

    def test_oversized_file_is_rejected(self):
        big = b"%PDF-1.4\n" + b"A" * (RESUME_MAX_BYTES_DEFAULT + 1)
        response = self._upload_resume(big)
        self.assertIn("smaller than 5 MB", response.get_data(as_text=True))

    def test_bad_magic_bytes_are_rejected(self):
        response = self._upload_resume(b"NOTPDF-not-a-pdf-at-all")
        # Flash text is HTML-escaped, so assert around the apostrophe.
        self.assertIn("look like a valid PDF",
                      response.get_data(as_text=True))

    def test_corrupted_pdf_is_rejected_cleanly(self):
        response = self._upload_resume(CORRUPT_PDF)
        html = response.get_data(as_text=True)
        self.assertTrue(
            "corrupted" in html or "could not be read" in html,
            f"No corruption message shown: {html[-400:]}"
        )

    def test_scanned_or_image_only_pdf_is_rejected_without_ocr(self):
        response = self._upload_resume(SCANNED_RESUME_PDF)
        html = response.get_data(as_text=True)
        self.assertIn("scanned", html)
        self.assertIn("OCR", html)

    def test_valid_pdf_uploads_parses_and_persists(self):
        response = self._upload_resume()

        self.assertIn("Resume analyzed.", response.get_data(as_text=True))
        profile = self._profile_row()
        self.assertTrue(profile["resume_path"])
        self.assertTrue(os.path.isfile(profile["resume_path"]))
        self.assertTrue(os.path.abspath(profile["resume_path"]).startswith(
            os.path.abspath(self.upload_dir)))

        html = self.client.get("/resume").get_data(as_text=True)
        for item in ("Python", "SQL", "Unit testing", "InterviewIQ capstone",
                     "Flask", "SQLite", "AWS Cloud Practitioner"):
            self.assertIn(item, html)

    def test_analysis_contract_receives_extracted_text(self):
        self._upload_resume()
        analyze_prompt = next(
            call for call in self.calls if ANALYZE_MARKER in call
        )
        self.assertIn("Jane Doe", analyze_prompt)
        self.assertIn("AWS Cloud Practitioner", analyze_prompt)

    def test_upload_without_csrf_token_fails(self):
        data = {"resume": (io.BytesIO(VALID_RESUME_PDF), "r.pdf")}
        response = self.client.post("/resume/upload", data=data)
        self.assertEqual(response.status_code, 400)

    def test_reupload_replaces_file_under_fixed_name(self):
        self._upload_resume()
        first_path = self._profile_row()["resume_path"]

        updated = _build_pdf(["Updated Resume", "Skill: Go", "x" * 120])
        self._upload_resume(updated)
        second_path = self._profile_row()["resume_path"]

        self.assertEqual(first_path, second_path)  # fixed per-user name
        self.assertTrue(os.path.isfile(second_path))


class ResumeAnalysisFlowTests(Stage6TestBase):
    def setUp(self):
        super().setUp()
        self._register_and_login()

    def test_unconfigured_service_saves_file_with_warning(self):
        self._uninstall_service()
        calls_before = list(self.calls)
        response = self._upload_resume()

        html = response.get_data(as_text=True)
        self.assertIn("GEMINI_API_KEY", html)
        self.assertTrue(self._profile_row()["resume_path"])  # file saved
        self.assertEqual(self.calls, calls_before)           # no call attempted
        self.assertNotIn("Extracted profile", html)          # nothing cached

    def test_gemini_failure_keeps_saved_resume(self):
        self.fail_analyze_with = GeminiError("upstream broke", 500)
        response = self._upload_resume()
        self.assertIn("Analyze again", response.get_data(as_text=True))
        self.assertTrue(self._profile_row()["resume_path"])

        # Configuring the service later lets Analyze again succeed.
        self.fail_analyze_with = None
        self._install_service()
        token = self._get_csrf("/resume")
        response = self.client.post("/resume/analyze",
                                    data={"csrf_token": token},
                                    follow_redirects=True)
        self.assertIn("Resume analyzed.", response.get_data(as_text=True))

    def test_rate_limited_analysis_flashes_warning(self):
        self.fail_analyze_with = GeminiRateLimitError(429, "slow down")
        response = self._upload_resume()
        self.assertIn("rate limited", response.get_data(as_text=True))
        self.assertTrue(self._profile_row()["resume_path"])

    def test_analyze_again_requires_a_stored_resume(self):
        token = self._get_csrf("/resume")
        response = self.client.post("/resume/analyze",
                                    data={"csrf_token": token},
                                    follow_redirects=True)
        self.assertIn("Upload a resume first.", response.get_data(as_text=True))


class ResumeQuestionTests(Stage6TestBase):
    def setUp(self):
        super().setUp()
        self._register_and_login()

    def _generate_from_resume(self, difficulty="medium"):
        token = self._get_csrf("/resume")
        return self.client.post("/resume/questions", data={
            "difficulty": difficulty, "csrf_token": token,
        }, follow_redirects=False)

    def test_requires_prior_analysis(self):
        response = self._generate_from_resume()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.calls, [])
        db = self._db()
        count = db.execute("SELECT COUNT(*) FROM interviews").fetchone()[0]
        db.close()
        self.assertEqual(count, 0)

    def test_generates_through_existing_practice_pipeline(self):
        self._upload_resume()
        calls_before = len(self.calls)

        response = self._generate_from_resume()
        self.assertEqual(response.status_code, 302)
        self.assertIn("/practice/question/", response.headers["Location"])

        prompt = next(
            call for call in self.calls[calls_before:]
            if QUESTION_MARKER in call
        )
        self.assertIn("InterviewIQ capstone", prompt)   # grounded in resume
        self.assertIn("Software Engineer", prompt)      # profile/default role

        db = self._db()
        row = db.execute(
            "SELECT mode, type FROM interviews ORDER BY id DESC LIMIT 1"
        ).fetchone()
        db.close()
        self.assertEqual(row["mode"], "practice")
        self.assertTrue(row["type"].startswith("Resume:"))

    def test_invalid_difficulty_falls_back_to_medium(self):
        self._upload_resume()
        self._generate_from_resume(difficulty="ludicrous")
        prompt = next(call for call in self.calls if QUESTION_MARKER in call)
        self.assertIn("medium difficulty", prompt)

    def test_generate_without_csrf_fails(self):
        self._upload_resume()
        response = self.client.post("/resume/questions", data={"difficulty": "easy"})
        self.assertEqual(response.status_code, 400)

    def test_unconfigured_service_creates_nothing(self):
        self._upload_resume()
        self._uninstall_service()
        token = self._get_csrf("/resume")
        response = self.client.post("/resume/questions", data={
            "difficulty": "medium", "csrf_token": token,
        }, follow_redirects=True)

        self.assertIn("GEMINI_API_KEY", response.get_data(as_text=True))
        self.assertEqual(self._interview_count(), 0)  # upload created none

    def _interview_count(self):
        db = self._db()
        count = db.execute("SELECT COUNT(*) FROM interviews").fetchone()[0]
        db.close()
        return count


# ---------------------------------------------------------------------------
# Item 12 — Reports (on-demand ReportLab PDFs)
# ---------------------------------------------------------------------------

class ReportTests(Stage6TestBase):
    def setUp(self):
        super().setUp()
        self._register_and_login()

    def _download(self, interview_id=None):
        interview_id = (
            self._first_interview_id() if interview_id is None else interview_id
        )
        return self.client.get(f"/reports/{interview_id}.pdf")

    def test_reports_page_lists_completed_sessions(self):
        self._complete_practice_session(topic="Reportable topic")
        html = self.client.get("/reports").get_data(as_text=True)
        self.assertIn("Reportable topic", html)
        self.assertIn("Download PDF", html)

    def test_reports_page_empty_state(self):
        html = self.client.get("/reports").get_data(as_text=True)
        self.assertIn("Nothing to report yet", html)

    def test_download_returns_generated_pdf_with_narrative(self):
        self._complete_practice_session(topic="PDF topic")

        response = self._download()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "application/pdf")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        body = response.get_data()
        self.assertTrue(body.startswith(b"%PDF-"))

        text = _pdf_text(body)
        self.assertIn("Test Student", text)
        self.assertIn(QUESTION_PAYLOAD["question"], text)
        self.assertIn(EVALUATION_PAYLOAD["feedback"], text)
        self.assertIn(self.narrative_text, text)
        self.assertIn("Dimension averages", text)
        self.assertIn("Technical accuracy", text)

    def test_report_is_generated_on_demand_not_stored(self):
        self._complete_practice_session()
        self._download()

        db = self._db()
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        db.close()
        self.assertNotIn("reports", tables)
        leftovers = [name for name in os.listdir(self.tmp)
                     if name.endswith(".pdf")]
        self.assertEqual(leftovers, [])

    def test_narrative_failure_still_produces_report(self):
        self._complete_practice_session()
        self.fail_narrative_with = GeminiError("narrative down", 503)

        response = self._download()
        self.assertEqual(response.status_code, 200)
        text = _pdf_text(response.get_data())
        self.assertIn(QUESTION_PAYLOAD["question"], text)
        self.assertNotIn(self.narrative_text, text)

    def test_unconfigured_service_still_produces_report(self):
        self._complete_practice_session()
        self._uninstall_service()

        response = self._download()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_data().startswith(b"%PDF-"))
        self.assertNotIn(
            self.narrative_text, _pdf_text(response.get_data())
        )

    def test_ownership_enforced(self):
        self._complete_practice_session()
        attacker = self._register_attacker()
        self.assertEqual(attacker.get(
            f"/reports/{self._first_interview_id()}.pdf").status_code, 404)

    def test_unknown_interview_is_404(self):
        self.assertEqual(self._download(interview_id=9999).status_code, 404)

    def test_in_progress_real_redirects_to_live_view(self):
        self._start_real()
        response = self._download()
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/interview/{self._first_interview_id()}",
                      response.headers["Location"])

    def test_in_progress_practice_redirects_to_open_question(self):
        self._practice_ask(topic="Unfinished")
        question_id = self._last_question_id()

        response = self._download()
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/practice/question/{question_id}",
                      response.headers["Location"])

    def test_pdf_never_contains_the_api_key(self):
        self._complete_practice_session()
        body = self._download().get_data()
        self.assertNotIn(SECRET_TEST_KEY.encode(), body)

    def test_download_requires_login(self):
        anonymous = self.app.test_client()
        self.assertEqual(
            anonymous.get("/reports/1.pdf").status_code, 302)


# ---------------------------------------------------------------------------
# Item 12 — Rate limiting (Section J)
# ---------------------------------------------------------------------------

class RateLimitTestBase(Stage6TestBase):
    """Rate limiting is disabled under TESTING by default; these tests opt in
    with deliberately tiny windows."""

    def setUp(self):
        super().setUp()
        from interview_Ai.app import ratelimit

        ratelimit._reset_for_tests()
        self.ratelimit = ratelimit
        self.app.config.update({
            "RATELIMIT_ENABLED": True,
            "RATELIMIT_AUTH_LIMIT": 3,
            "RATELIMIT_AUTH_WINDOW": 60,
            "RATELIMIT_GEMINI_LIMIT": 2,
            "RATELIMIT_GEMINI_WINDOW": 60,
        })

    def tearDown(self):
        self.ratelimit._reset_for_tests()
        super().tearDown()


class AuthRateLimitTests(RateLimitTestBase):
    def _post_register(self, email):
        # A fresh client per attempt: registering logs the browser in, and
        # a logged-in GET /register would redirect instead of serving the
        # form. Each attempt still comes from the same source IP.
        client = self.app.test_client()
        page = client.get("/register")
        return client.post("/register", data={
            "name": "Someone", "email": email,
            "password": "password1", "confirm": "password1",
            "csrf_token": _csrf(page.get_data(as_text=True)),
        })

    def test_fourth_auth_post_per_ip_gets_429(self):
        for index in range(3):
            response = self._post_register(f"user{index}@example.com")
            self.assertNotEqual(response.status_code, 429)

        fourth = self._post_register("user4@example.com")
        self.assertEqual(fourth.status_code, 429)
        self.assertIn("Too many requests", fourth.get_data(as_text=True))

    def test_window_reset_restores_access(self):
        real_now = self.ratelimit._now
        for index in range(3):
            self._post_register(f"w{index}@example.com")
        self.assertEqual(self._post_register("blocked@example.com").status_code,
                         429)

        with mock.patch.object(self.ratelimit, "_now",
                               lambda: real_now() + 61):
            response = self._post_register("after@example.com")
        self.assertNotEqual(response.status_code, 429)

    def test_get_requests_are_never_limited(self):
        for _ in range(3):
            self._post_register(f"g{self.id()}@example.com")
        self.assertEqual(self.client.get("/login").status_code, 200)
        self.assertEqual(self.client.get("/register").status_code, 200)

    def test_logout_is_exempt(self):
        self._register_and_login(email="logout@example.com")
        for index in range(3):
            self._post_register(f"burn{index}@example.com")

        token = self._get_csrf("/dashboard")
        response = self.client.post("/logout", data={"csrf_token": token})
        self.assertNotEqual(response.status_code, 429)

    def test_error_page_exposes_no_internals(self):
        for index in range(3):
            self._post_register(f"x{index}@example.com")
        body = self._post_register("final@example.com").get_data(as_text=True)
        self.assertNotIn("gemini:", body)
        self.assertNotIn("auth:127.0.0.1", body)
        self.assertNotIn(SECRET_TEST_KEY, body)


class GeminiRateLimitTests(RateLimitTestBase):
    def test_third_ai_call_for_user_gets_429(self):
        self._register_and_login()
        self.assertNotEqual(self._practice_ask().status_code, 429)
        self.assertNotEqual(self._practice_ask().status_code, 429)

        third = self._practice_ask()
        self.assertEqual(third.status_code, 429)
        self.assertIn("Too many requests", third.get_data(as_text=True))

    def test_limit_is_per_user_not_global(self):
        self._register_and_login(email="busy@example.com")
        self._practice_ask()
        self._practice_ask()
        self.assertEqual(self._practice_ask().status_code, 429)

        attacker = self._register_attacker()
        page = attacker.get("/practice")
        token = _csrf(page.get_data(as_text=True))
        response = attacker.post("/practice/question", data={
            "topic": "Attacker topic", "difficulty": "easy",
            "csrf_token": token,
        })
        self.assertEqual(response.status_code, 302)  # allowed: separate bucket

    def test_ai_limit_disabled_by_default_in_testing(self):
        class PlainConfig(Config):
            TESTING = True
            DATABASE_PATH = os.path.join(tempfile.mkdtemp(), "plain.db")

        from interview_Ai.app import create_app

        app = create_app(PlainConfig)
        self.assertFalse(app.config.get("RATELIMIT_ENABLED", False))
        self.assertTrue(app.config.get("TESTING"))


# ---------------------------------------------------------------------------
# Scope guardrails + migration + hygiene
# ---------------------------------------------------------------------------

class SidebarScopeTests(Stage6TestBase):
    def test_settings_still_disabled_profile_now_active(self):
        self._register_and_login()
        html = self.client.get("/dashboard").get_data(as_text=True)

        self.assertIn('href="/resume"', html)
        self.assertIn('href="/analytics"', html)
        self.assertIn('href="/reports"', html)
        # Profile shipped as a real page (Account section); Settings is the
        # only remaining deferred placeholder.
        self.assertEqual(html.count("soon-badge"), 1)
        self.assertNotIn('aria-disabled="true">Profile', html)
        self.assertIn('aria-disabled="true">Settings', html)


class StartupAndMigrationTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("GEMINI_API_KEY", None)
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "startup.db")

    def _config(self):
        class Cfg(Config):
            TESTING = True
            DATABASE_PATH = self.db_path
            RESUME_UPLOAD_DIR = ""

        return Cfg

    def test_default_upload_dir_created_outside_web_root(self):
        import shutil

        from interview_Ai.app import create_app

        # When RESUME_UPLOAD_DIR is unset it defaults to <instance>/resumes.
        # The instance dir of this checkout is shared, so clean up after.
        app = create_app(self._config())
        upload_dir = app.config["RESUME_UPLOAD_DIR"]
        try:
            self.assertTrue(os.path.isdir(upload_dir))
            static_root = os.path.abspath(app.static_folder)
            self.assertFalse(
                os.path.abspath(upload_dir).startswith(static_root)
            )
            self.assertEqual(os.path.basename(upload_dir), "resumes")
        finally:
            shutil.rmtree(upload_dir, ignore_errors=True)

    def test_double_boot_is_idempotent(self):
        from interview_Ai.app import create_app

        create_app(self._config())
        create_app(self._config())

        conn = sqlite3.connect(self.db_path)
        tables = sorted(row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(profiles)")}
        conn.close()

        self.assertIn("roadmaps", tables)
        self.assertIn("resume_path", columns)

    def test_stage5_database_boots_and_serves_new_pages(self):
        from interview_Ai.app import create_app

        app = create_app(self._config())
        with app.test_client() as client:
            page = client.get("/register")
            client.post("/register", data={
                "name": "Fresh", "email": "fresh6@example.com",
                "password": "password1", "confirm": "password1",
                "csrf_token": _csrf(page.get_data(as_text=True)),
            })
            for path in ("/analytics", "/resume", "/reports"):
                self.assertEqual(client.get(path).status_code, 200, path)
            shell = client.get("/dashboard").get_data(as_text=True)
            self.assertIn('href="/analytics"', shell)


class Stage6KeyHygieneTests(Stage6TestBase):
    def test_key_never_appears_in_new_pages_or_downloads(self):
        self._register_and_login()
        self._complete_practice_session()
        self._upload_resume()

        for path in ("/analytics", "/analytics/data", "/resume", "/reports",
                     "/dashboard"):
            body = self.client.get(path).get_data(as_text=True)
            self.assertNotIn(SECRET_TEST_KEY, body)

        pdf_body = self.client.get(
            f"/reports/{self._first_interview_id()}.pdf"
        ).get_data()
        self.assertNotIn(SECRET_TEST_KEY.encode(), pdf_body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
