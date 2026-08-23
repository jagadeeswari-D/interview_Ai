"""Resume Analysis (Stage 6 Item 11, blueprint Sections C/D/E/J/K.11).

Flow (all server-side):
1. Upload a PDF — validated per blueprint Section J: PDF-only (strict MIME
   type + .pdf extension + %PDF- magic bytes), ~5MB cap, stored OUTSIDE the
   web root under a fixed per-user filename (`<user_id>.pdf`, so no
   user-controlled path ever touches the filesystem).
2. Text is extracted locally with PyPDF2. Scanned/image-only PDFs carry no
   selectable text and are rejected with a clear message — OCR is excluded
   by the blueprint (Section L).
3. The extracted text goes through the EXISTING `analyze_resume` contract
   (schemas.py / prompts.py / GeminiService reused verbatim) and the
   structured result is shown: skills, projects, technologies,
   certifications.
4. "Generate questions from this" feeds a resume-derived topic into the
   EXISTING generate_question contract and the standard practice flow —
   there is deliberately no second question-generation AI path.

The last analysis is cached in the server-side session so revisiting the
page does not silently re-bill Gemini calls; "Analyze again" re-runs it on
demand. No resume content is ever embedded in URLs or logs.
"""

import io
import os

from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from PyPDF2 import PdfReader
from PyPDF2.errors import PdfReadError

from .ai.errors import GeminiConfigError, GeminiError, GeminiRateLimitError
from .auth import login_required
from .models import (
    add_question,
    create_practice_interview,
    get_profile,
    set_interview_status,
    set_resume_path,
)
from .practice import DEFAULT_ROLE, DIFFICULTIES
from .ratelimit import check_gemini_limit

resume_bp = Blueprint("resume", __name__)

PDF_MIME = "application/pdf"
MIN_RESUME_TEXT_CHARS = 80  # below this the PDF is effectively image-only
TOPIC_LABEL_MAX = 80        # interviews.type column width convention


@resume_bp.route("/resume", methods=["GET"])
@login_required
def view():
    profile = get_profile(g.user["id"])
    stored_path = _stored_resume_path(profile)
    return render_template(
        "resume.html",
        active_page="resume",
        has_resume=stored_path is not None,
        analysis=session.get("resume_analysis"),
        difficulties=DIFFICULTIES,
    )


@resume_bp.route("/resume/upload", methods=["POST"])
@login_required
def upload():
    check_gemini_limit()

    uploaded = request.files.get("resume")
    if uploaded is None or not uploaded.filename:
        flash("Choose a PDF file to upload.", "error")
        return redirect(url_for(".view"))

    # Section J: strict MIME type plus extension, before any parsing.
    if (uploaded.mimetype or "").lower() != PDF_MIME:
        flash("Only PDF resumes are accepted.", "error")
        return redirect(url_for(".view"))
    if not uploaded.filename.lower().endswith(".pdf"):
        flash("The file must have a .pdf extension.", "error")
        return redirect(url_for(".view"))

    max_bytes = current_app.config["RESUME_MAX_BYTES"]
    data = uploaded.stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        flash(
            f"Resume must be smaller than {max_bytes // (1024 * 1024)} MB.",
            "error",
        )
        return redirect(url_for(".view"))

    if not data.startswith(b"%PDF-"):
        flash("That file doesn't look like a valid PDF.", "error")
        return redirect(url_for(".view"))

    ok, text, error_message = extract_pdf_text(data)
    if not ok:
        flash(error_message, "error")
        return redirect(url_for(".view"))

    path = _save_resume_pdf(g.user["id"], data)
    set_resume_path(g.user["id"], path)
    session.pop("resume_analysis", None)

    _analyze_and_cache(text)
    flash("Resume uploaded.", "success")
    return redirect(url_for(".view"))


@resume_bp.route("/resume/analyze", methods=["POST"])
@login_required
def analyze():
    """Re-run the AI analysis on the stored resume."""
    check_gemini_limit()

    profile = get_profile(g.user["id"])
    stored_path = _stored_resume_path(profile)
    if stored_path is None:
        flash("Upload a resume first.", "error")
        return redirect(url_for(".view"))

    with open(stored_path, "rb") as handle:
        data = handle.read()
    ok, text, error_message = extract_pdf_text(data)
    if not ok:
        # The stored file changed on disk or was unreadable: drop the stale
        # reference instead of leaving broken state behind.
        set_resume_path(g.user["id"], None)
        session.pop("resume_analysis", None)
        flash(error_message, "error")
        return redirect(url_for(".view"))

    _analyze_and_cache(text)
    return redirect(url_for(".view"))


@resume_bp.route("/resume/questions", methods=["POST"])
@login_required
def questions():
    """Generate one interview question grounded in the parsed resume.

    Reuses the shared generate_question contract and the standard practice
    flow (Practice interview row -> Questions -> evaluation) unchanged.
    """
    check_gemini_limit()

    analysis = session.get("resume_analysis")
    if not analysis:
        flash("Upload and analyze your resume first.", "error")
        return redirect(url_for(".view"))

    difficulty = (
        (request.form.get("difficulty") or "medium").strip().lower()
    )
    if difficulty not in DIFFICULTIES:
        difficulty = "medium"

    focus = _resume_focus(analysis)
    if not focus:
        flash(
            "The resume didn't contain enough project or skill details to "
            "build questions from. Upload a richer resume and try again.",
            "error",
        )
        return redirect(url_for(".view"))

    role = _role_for_user()
    service = current_app.extensions["gemini"]
    try:
        payload = service.generate(
            "generate_question",
            {"role": role, "topic": focus, "difficulty": difficulty},
        )
    except GeminiConfigError:
        flash(
            "AI features are not configured yet. Ask an operator to set the "
            "GEMINI_API_KEY on the server.",
            "warning",
        )
        return redirect(url_for(".view"))
    except GeminiRateLimitError:
        flash(
            "The AI coach is rate limited right now. Please wait a moment "
            "and try again.",
            "warning",
        )
        return redirect(url_for(".view"))
    except GeminiError:
        flash(
            "The AI coach could not generate a question right now. "
            "Please try again.",
            "error",
        )
        return redirect(url_for(".view"))

    label = f"Resume: {_shorten(focus, TOPIC_LABEL_MAX - len('Resume: '))}"
    interview = create_practice_interview(g.user["id"], role, difficulty, label)
    question_id = add_question(
        interview["id"],
        payload["question"],
        payload.get("question_type", ""),
        payload.get("expected_concepts", []),
    )
    set_interview_status(interview["id"], "in_progress")
    return redirect(url_for("practice.question_view", question_id=question_id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_pdf_text(data):
    """Parse PDF bytes and return (ok, text, user_facing_error).

    Malformed and password-protected files fail with specific messages;
    a parseable PDF without selectable text is treated as scanned/image-only
    and rejected (OCR is out of scope by blueprint Section L).
    """
    try:
        reader = PdfReader(io.BytesIO(data))
    except PdfReadError:
        return False, "", "That PDF appears to be corrupted. Try exporting it again."
    except Exception:
        return False, "", "That PDF could not be read. Make sure it's a valid PDF file."

    if getattr(reader, "is_encrypted", False):
        return False, "", (
            "Password-protected PDFs can't be processed. Remove the "
            "password and upload again."
        )
    try:
        text = "".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return False, "", "That PDF could not be read. Make sure it's a valid PDF file."

    if len("".join(text.split())) < MIN_RESUME_TEXT_CHARS:
        return False, "", (
            "No readable text found in this PDF — it looks like a scanned "
            "or image-only resume. OCR isn't supported; please upload a "
            "text-based PDF."
        )
    trimmed = text.strip()
    return True, trimmed[:20000], ""


def _save_resume_pdf(user_id, data):
    """Persist the PDF outside the web root under a fixed filename."""
    directory = current_app.config["RESUME_UPLOAD_DIR"]
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{user_id}.pdf")
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _analyze_and_cache(resume_text):
    """Run analyze_resume via the shared contract; cache result in session."""
    service = current_app.extensions["gemini"]
    try:
        payload = service.generate("analyze_resume", {"resume_text": resume_text})
    except GeminiConfigError:
        flash(
            "AI features are not configured yet. Ask an operator to set the "
            "GEMINI_API_KEY on the server.",
            "warning",
        )
        return
    except GeminiRateLimitError:
        flash(
            "The AI coach is rate limited right now. Your resume was saved — "
            "use Analyze again in a moment.",
            "warning",
        )
        return
    except GeminiError:
        flash(
            "The resume couldn't be analyzed right now. It was saved — use "
            "Analyze again to retry.",
            "error",
        )
        return

    session["resume_analysis"] = {
        "skills": [str(item) for item in payload.get("skills", [])],
        "projects": [str(item) for item in payload.get("projects", [])],
        "technologies": [str(item) for item in payload.get("technologies", [])],
        "certifications": [str(item) for item in payload.get("certifications", [])],
    }
    flash("Resume analyzed.", "success")


def _resume_focus(analysis):
    """Deterministic topical focus derived from the parsed resume."""
    projects = analysis.get("projects") or []
    technologies = analysis.get("technologies") or []
    skills = analysis.get("skills") or []
    if projects:
        return f"the candidate's project '{_shorten(str(projects[0]), 60)}' from their resume"
    if technologies:
        return f"the candidate's experience with {_shorten(str(technologies[0]), 60)}"
    if skills:
        return f"{', '.join(str(skill) for skill in skills[:3])}"
    return None


def _shorten(value, limit):
    value = str(value).strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "\u2026"


def _role_for_user():
    profile = get_profile(g.user["id"])
    return (profile["role"] if profile else "").strip() or DEFAULT_ROLE


def _save_resume_path(user_id, data):
    """Persist the PDF outside the web root under a fixed filename."""
    directory = current_app.config["RESUME_UPLOAD_DIR"]
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{g.user['id']}.pdf")
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _stored_resume_path(profile):
    """Absolute path of this user's stored resume, or None if unusable."""
    if profile is None or not profile["resume_path"]:
        return None
    expected_dir = os.path.abspath(current_app.config["RESUME_UPLOAD_DIR"])
    path = os.path.abspath(profile["resume_path"])
    # Defense in depth: never honor a DB path that escaped the upload dir.
    if os.path.dirname(path) != expected_dir:
        return None
    if not os.path.isfile(path):
        return None
    return path
