"""Smart Practice Mode (Stage 2 Task 4, blueprint Sections B.4 / C / G).

Flow: pick topic -> Gemini generates a question -> user answers -> instant
rubric-based evaluation -> hint/model answer available -> retry or pick a new
topic. Every attempt is logged to Questions/Answers/Performance.

All AI calls go through the shared GeminiService (`current_app.extensions
["gemini"]`) and are validated against the fixed JSON contracts in schemas.py.
The Gemini key stays server-side; the browser only ever sees rendered HTML.
"""

import json

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from .ai.errors import (
    GeminiConfigError,
    GeminiError,
    GeminiRateLimitError,
)
from .auth import login_required
from .achievements import evaluate_achievements
from .company_presets import (
    GENERAL_KEY,
    company_context_for,
    company_preset_by_key,
    company_select_options,
    resolve_company_key,
)
from .evaluation import SCORE_DIMENSIONS, evaluate_answer, store_evaluation
from .memory import on_interview_completed
from .models import (
    add_question,
    create_practice_interview,
    get_answer,
    get_interview,
    get_profile,
    get_question_with_interview,
    set_interview_status,
)
from .ratelimit import check_gemini_limit
from . import xp

practice_bp = Blueprint("practice", __name__)

DIFFICULTIES = ("easy", "medium", "hard")
DEFAULT_ROLE = "Software Engineer"
TOPIC_MIN_LENGTH = 2
TOPIC_MAX_LENGTH = 80
ANSWER_MAX_LENGTH = 5000

TOPIC_SUGGESTIONS = [
    "SQL joins & indexes",
    "Data structures",
    "REST API design",
    "OOP principles",
    "Time complexity",
    "HTTP & networking",
    "Git workflows",
    "Database normalization",
]


@practice_bp.route("/practice", methods=["GET"])
@login_required
def pick():
    profile = get_profile(g.user["id"])
    role = (profile["role"] if profile else "").strip()

    # Optional prefill so other pages (e.g. Learning Roadmap days, Stage 5)
    # can link straight into practicing a specific topic. Unknown values
    # fall back to the plain picker.
    topic = (request.args.get("topic") or "").strip()
    if not TOPIC_MIN_LENGTH <= len(topic) <= TOPIC_MAX_LENGTH:
        topic = None
    difficulty = (request.args.get("difficulty") or "").strip().lower()
    if difficulty not in DIFFICULTIES:
        difficulty = "medium"

    return render_template(
        "practice.html",
        active_page="practice",
        view="pick",
        role_label=role or DEFAULT_ROLE,
        suggestions=TOPIC_SUGGESTIONS,
        difficulties=DIFFICULTIES,
        prefill_topic=topic,
        prefill_difficulty=difficulty,
        companies=company_select_options(),
        selected_company=GENERAL_KEY,
    )


@practice_bp.route("/practice/question", methods=["POST"])
@login_required
def new_question():
    """Generate one question for a topic; `interview_id` continues a session."""
    # Section J (Stage 6): AI-backed endpoints are limited per user.
    check_gemini_limit()
    topic = (request.form.get("topic") or "").strip()
    difficulty = (request.form.get("difficulty") or "medium").strip().lower()

    errors = []
    if not TOPIC_MIN_LENGTH <= len(topic) <= TOPIC_MAX_LENGTH:
        errors.append(
            f"Topic must be between {TOPIC_MIN_LENGTH} and "
            f"{TOPIC_MAX_LENGTH} characters."
        )
    if difficulty not in DIFFICULTIES:
        errors.append("Choose a valid difficulty level.")
    if errors:
        for message in errors:
            flash(message, "error")
        return redirect(url_for("practice.pick"))

    interview_id = request.form.get("interview_id", type=int)
    interview = None
    if interview_id:
        interview = get_interview(interview_id)
        if (
            interview is None
            or interview["user_id"] != g.user["id"]
            or interview["mode"] != "practice"
        ):
            abort(404)

    # Company Presets (Phase 10 / Stage 1): validate the submitted key server-
    # side against the static allowlist before it can reach Gemini or the
    # database. Blank/"general" -> General (None); any unlisted value (free
    # text, tampered keys) is rejected outright. A continued session with a
    # blank field inherits its stored company key.
    try:
        company_key, company_preset = resolve_company_key(
            request.form.get("company"), interview
        )
    except ValueError:
        flash("Choose a valid company context.", "error")
        return redirect(url_for("practice.pick"))

    role = _role_for_user()
    service = current_app.extensions["gemini"]
    try:
        inputs = {"role": role, "topic": topic, "difficulty": difficulty}
        if company_preset is not None:
            inputs["company"] = company_context_for(company_preset)
        payload = service.generate("generate_question", inputs)
    except GeminiConfigError:
        flash(
            "AI features are not configured yet. Ask an operator to set the "
            "GEMINI_API_KEY on the server.",
            "warning",
        )
        return redirect(url_for("practice.pick"))
    except GeminiRateLimitError:
        flash(
            "The AI coach is rate limited right now. Please wait a moment "
            "and try again.",
            "warning",
        )
        return redirect(url_for("practice.pick"))
    except GeminiError:
        flash(
            "The AI coach could not generate a question right now. "
            "Please try again.",
            "error",
        )
        return redirect(url_for("practice.pick"))

    if interview is None:
        interview = create_practice_interview(
            g.user["id"], role, difficulty, topic, company_key=company_key
        )

    question_id = add_question(
        interview["id"],
        payload["question"],
        payload.get("question_type", ""),
        payload.get("expected_concepts", []),
    )

    # A pending question means the session is open again (retry case).
    set_interview_status(interview["id"], "in_progress")
    return redirect(url_for("practice.question_view", question_id=question_id))


@practice_bp.route("/practice/question/<int:question_id>", methods=["GET"])
@login_required
def question_view(question_id):
    row = _owned_question(question_id)
    if row is None:
        abort(404)

    concepts = json.loads(row["expected_concepts"] or "[]")
    answer = get_answer(question_id)
    company_preset = (
        company_preset_by_key(row["company_key"]) if row["company_key"] else None
    )
    return render_template(
        "practice.html",
        active_page="practice",
        view="result" if answer else "answer",
        q=row,
        concepts=concepts,
        answer=answer,
        dimensions=SCORE_DIMENSIONS,
        company_preset=company_preset,
    )


@practice_bp.route("/practice/answer", methods=["POST"])
@login_required
def submit_answer():
    check_gemini_limit()
    question_id = request.form.get("question_id", type=int)
    answer_text = (request.form.get("answer") or "").strip()

    row = _owned_question(question_id) if question_id else None
    if row is None:
        abort(404)

    if get_answer(question_id) is not None:
        return redirect(url_for("practice.question_view", question_id=question_id))

    if not answer_text:
        flash("Write your answer before submitting.", "error")
        return redirect(url_for("practice.question_view", question_id=question_id))
    if len(answer_text) > ANSWER_MAX_LENGTH:
        flash(
            f"Answers are limited to {ANSWER_MAX_LENGTH} characters.",
            "error",
        )
        return redirect(url_for("practice.question_view", question_id=question_id))

    concepts = json.loads(row["expected_concepts"] or "[]")
    service = current_app.extensions["gemini"]
    # Company Presets (Stage 16): the evaluation carries the company context
    # of the SESSION the question belongs to (persisted allowlisted key from
    # the interview row) — never anything the browser submits at answer time.
    company_preset = (
        company_preset_by_key(row["company_key"]) if row["company_key"] else None
    )
    try:
        if company_preset is not None:
            evaluation = evaluate_answer(
                service, row["question"], answer_text, concepts,
                company=company_context_for(company_preset),
            )
        else:
            evaluation = evaluate_answer(
                service, row["question"], answer_text, concepts)
    except GeminiConfigError:
        flash(
            "AI features are not configured yet. Ask an operator to set the "
            "GEMINI_API_KEY on the server.",
            "warning",
        )
        return redirect(url_for("practice.question_view", question_id=question_id))
    except GeminiRateLimitError:
        flash(
            "The AI coach is rate limited right now. Your answer was kept — "
            "please wait a moment and submit it again.",
            "warning",
        )
        return redirect(url_for("practice.question_view", question_id=question_id))
    except GeminiError:
        flash(
            "The AI coach could not evaluate your answer right now. "
            "Please try submitting it again.",
            "error",
        )
        return redirect(url_for("practice.question_view", question_id=question_id))

    inserted, overall, _ = store_evaluation(
        question_id,
        answer_text,
        evaluation,
        user_id=g.user["id"],
        skill_label=row["topic"],
        interview_id=row["interview_id"],
    )
    if not inserted:
        # A duplicate submission raced ahead and already stored this answer:
        # do not complete the session or aggregate this evaluation again.
        return redirect(url_for("practice.question_view", question_id=question_id))
    set_interview_status(row["interview_id"], "completed", overall_score=overall)

    # Stage 4 (blueprint B.7/K.8): aggregate THIS answer into Weaknesses and
    # refresh the InterviewMemory derived cache. Practice completes per
    # question, so only the new entry is passed — retries never double-count.
    on_interview_completed(
        g.user["id"],
        get_interview(row["interview_id"]),
        [{
            "user_answer": answer_text,
            "score": overall,
            "scores": evaluation["scores"],
        }],
    )

    evaluate_achievements(g.user["id"])

    # Stage 14: award XP only for this newly graded practice question. The
    # ledger's UNIQUE(user_id, source, event_key) makes a retried/duplicate
    # submission unable to award the same question twice.
    xp.award(g.user["id"], xp.SRC_PRACTICE_ANSWER, question_id,
             xp.XP_PRACTICE_ANSWER)
    xp.award_achievement_bonuses(g.user["id"])

    return redirect(url_for("practice.question_view", question_id=question_id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _owned_question(question_id):
    row = get_question_with_interview(question_id)
    if row is None or row["user_id"] != g.user["id"]:
        return None
    # The practice endpoints must never operate on a Real Interview question:
    # those paths would leak hints/evaluation before the interview completes
    # and could finish a timed session early (bypassing the timer/limits).
    if row["mode"] != "practice":
        return None
    return row


def _role_for_user():
    profile = get_profile(g.user["id"])
    return (profile["role"] if profile else "").strip() or DEFAULT_ROLE
