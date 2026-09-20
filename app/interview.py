"""AI Real Interview Mode (Stage 3, blueprint Sections B.5 / E / H).

Flow: configure role/type/difficulty -> timer starts -> Gemini asks an opening
question -> user answers -> Gemini extracts key entities from the answer ->
generates a contextual follow-up (depth-adjusted by the previous answer's
technical_accuracy score) -> repeats for N questions or until time expires ->
the full evaluation is shown only at the end.

Adaptive mechanism (Section H):
1. entity extraction from each answer,
2. relevance prioritization: project/technology mentions beat generic claims,
3. follow-up prompt built from original question + answer + selected entity,
4. depth adjustment: low technical_accuracy -> foundational, high -> deeper,
5. session context: the last two Q&A pairs travel in the prompt (ephemeral),
6. timer expiry: the pending answer is auto-submitted as-is (client JS), and
   the server independently enforces the deadline with a small grace window.

Hint/model-answer suppression: expected concepts, scores, feedback, missing
points and model answers are stored server-side per answer but are never
rendered until the interview completes.
"""

from datetime import datetime, timezone

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
from .evaluation import (
    SCORE_DIMENSIONS,
    evaluate_answer,
    mean_score,
    store_evaluation,
)
from .memory import on_interview_completed
from .models import (
    add_question,
    create_real_interview,
    get_answer,
    get_interview,
    get_open_question,
    get_profile,
    get_question_with_interview,
    get_transcript,
    set_interview_status,
)
from .ratelimit import check_gemini_limit
from . import xp

interview_bp = Blueprint("interview", __name__)

DIFFICULTIES = ("easy", "medium", "hard")
INTERVIEW_TYPES = {
    "technical": "Technical",
    "behavioral": "Behavioral",
    "mixed": "Mixed (technical + behavioral)",
}
DEFAULT_ROLE = "Software Engineer"
ROLE_MIN_LENGTH = 2
ROLE_MAX_LENGTH = 80
ANSWER_MAX_LENGTH = 5000

# Per-difficulty interview budgets. The blueprint configures role/type/
# difficulty only; time and question budgets are fixed per difficulty so the
# deadline is deterministic, server-enforceable, and survives page reloads.
QUESTION_BUDGETS = {
    "easy": {"question_limit": 4, "duration_minutes": 12},
    "medium": {"question_limit": 5, "duration_minutes": 20},
    "hard": {"question_limit": 6, "duration_minutes": 30},
}

# Opening-question topic seeds for the generate_question task.
TYPE_TOPICS = {
    "technical": "core technical fundamentals of the role",
    "behavioral": "behavioral and situational skills",
    "mixed": "a mix of technical and behavioral skills",
}

# Window after expiry in which an auto-submitted answer is still accepted and
# evaluated before the server finalizes the interview (H.6 round trip).
GRACE_SECONDS = 90

_ENTITY_KIND_RANK = {"project": 0, "technology": 1, "claim": 2, "other": 3}


@interview_bp.route("/interview", methods=["GET"])
@login_required
def config():
    profile = get_profile(g.user["id"])
    role = (profile["role"] if profile else "").strip()
    return render_template(
        "interview.html",
        active_page="interview",
        view="config",
        role_label=role or DEFAULT_ROLE,
        difficulties=DIFFICULTIES,
        types=INTERVIEW_TYPES,
        budgets=QUESTION_BUDGETS,
        companies=company_select_options(),
        selected_company=GENERAL_KEY,
    )


@interview_bp.route("/interview/start", methods=["POST"])
@login_required
def start():
    """Validate the configuration, then open the timed session with Q1."""
    # Section J (Stage 6): AI-backed endpoints are limited per user.
    check_gemini_limit()
    role = (request.form.get("role") or "").strip() or DEFAULT_ROLE
    difficulty = (request.form.get("difficulty") or "").strip().lower()
    interview_type = (request.form.get("type") or "").strip().lower()

    errors = []
    if not ROLE_MIN_LENGTH <= len(role) <= ROLE_MAX_LENGTH:
        errors.append(
            f"Role must be between {ROLE_MIN_LENGTH} and "
            f"{ROLE_MAX_LENGTH} characters."
        )
    if difficulty not in DIFFICULTIES:
        errors.append("Choose a valid difficulty level.")
    if interview_type not in INTERVIEW_TYPES:
        errors.append("Choose a valid interview type.")
    if errors:
        for message in errors:
            flash(message, "error")
        return redirect(url_for(".config"))

    # Company Presets (Phase 10 / Stage 1): validate the submitted key server-
    # side against the static allowlist before it can reach Gemini or the
    # database. Blank/"general" -> General (None); unlisted values are
    # rejected outright.
    try:
        company_key, company_preset = resolve_company_key(
            request.form.get("company")
        )
    except ValueError:
        flash("Choose a valid company context.", "error")
        return redirect(url_for(".config"))

    service = current_app.extensions["gemini"]
    try:
        inputs = {
            "role": role,
            "topic": TYPE_TOPICS[interview_type],
            "difficulty": difficulty,
        }
        if company_preset is not None:
            inputs["company"] = company_context_for(company_preset)
        payload = service.generate("generate_question", inputs)
    except GeminiConfigError:
        flash(
            "AI features are not configured yet. Ask an operator to set the "
            "GEMINI_API_KEY on the server.",
            "warning",
        )
        return redirect(url_for(".config"))
    except GeminiRateLimitError:
        flash(
            "The AI interviewer is rate limited right now. Please wait a "
            "moment and try again.",
            "warning",
        )
        return redirect(url_for(".config"))
    except GeminiError:
        flash(
            "The AI interviewer could not prepare your first question right "
            "now. Please try again.",
            "error",
        )
        return redirect(url_for(".config"))

    budget = QUESTION_BUDGETS[difficulty]
    interview = create_real_interview(
        g.user["id"],
        role,
        difficulty,
        interview_type,
        budget["question_limit"],
        budget["duration_minutes"],
        company_key=company_key,
    )
    add_question(
        interview["id"],
        payload["question"],
        payload.get("question_type", ""),
        payload.get("expected_concepts", []),
    )
    return redirect(url_for(".live", interview_id=interview["id"]))


@interview_bp.route("/interview/<int:interview_id>", methods=["GET"])
@login_required
def live(interview_id):
    interview = _owned_real_interview(interview_id)
    if interview is None:
        abort(404)
    if interview["status"] == "completed":
        return redirect(url_for(".complete", interview_id=interview_id))

    if get_open_question(interview_id) is None:
        if _remaining_seconds(interview) <= 0:
            # The timer expired while the interview was stuck on the
            # recovery screen: close it now so an empty session can never
            # linger in_progress forever (server-enforced expiry, H.6).
            _finalize(interview)
            return redirect(url_for(".complete", interview_id=interview_id))
        # Every asked question is answered but the next one was never
        # prepared (the adaptive step failed mid-interview): recovery screen.
        return render_template(
            "interview.html",
            active_page="interview",
            view="pending",
            interview=interview,
            type_label=INTERVIEW_TYPES.get(interview["type"], interview["type"]),
            graded_count=_answered_count(interview_id),
            company_preset=_company_preset_for(interview),
        )

    remaining = _remaining_seconds(interview)
    if remaining <= 0:
        # Server-enforced expiry (H.6): the client auto-submit never arrived,
        # so close the session now; the open question stays unanswered.
        _finalize(interview)
        return redirect(url_for(".complete", interview_id=interview_id))

    return render_template(
        "interview.html",
        active_page="interview",
        view="live",
        interview=interview,
        q=get_open_question(interview_id),
        question_number=_answered_count(interview_id) + 1,
        remaining_seconds=remaining,
        dimensions=SCORE_DIMENSIONS,
        preserved_answer="",
        type_label=INTERVIEW_TYPES.get(interview["type"], interview["type"]),
        company_preset=_company_preset_for(interview),
    )


@interview_bp.route("/interview/<int:interview_id>/answer", methods=["POST"])
@login_required
def submit_answer(interview_id):
    check_gemini_limit()
    interview = _owned_real_interview(interview_id)
    if interview is None:
        abort(404)
    if interview["status"] == "completed":
        return redirect(url_for(".complete", interview_id=interview_id))

    question = get_open_question(interview_id)
    answer_text = (request.form.get("answer") or "").strip()
    remaining = _remaining_seconds(interview)
    expired = remaining <= 0

    if question is None or get_answer(question["id"]) is not None:
        return redirect(url_for(".live", interview_id=interview_id))
    if not answer_text:
        if expired:
            # Timer ran out on an empty box: nothing to auto-submit (H.6).
            _finalize(interview)
            return redirect(url_for(".complete", interview_id=interview_id))
        flash("Write your answer before submitting.", "error")
        return redirect(url_for(".live", interview_id=interview_id))
    if len(answer_text) > ANSWER_MAX_LENGTH:
        flash(f"Answers are limited to {ANSWER_MAX_LENGTH} characters.", "error")
        return redirect(url_for(".live", interview_id=interview_id))
    if remaining < -GRACE_SECONDS:
        # Long past expiry: not the auto-submit round trip anymore.
        flash("Time is up — this answer arrived too late to be graded.", "error")
        _finalize(interview)
        return redirect(url_for(".complete", interview_id=interview_id))

    service = current_app.extensions["gemini"]
    try:
        # Real Mode evaluates against the raw answer (no concept hints are
        # leaked into the prompt — expected_concepts stay suppressed). The
        # company context comes from the persisted allowlisted interview key.
        company_preset = _company_preset_for(interview)
        if company_preset is not None:
            evaluation = evaluate_answer(
                service, question["question"], answer_text, [],
                company=company_context_for(company_preset),
            )
        else:
            evaluation = evaluate_answer(
                service, question["question"], answer_text, [],
            )
    except GeminiError as exc:
        # Nothing saved yet: re-render the live view so the candidate can
        # resubmit their kept text (graceful async error state, Section I).
        return _render_live_error(interview, answer_text, exc)

    tech_acc = _save_evaluation(interview, question, answer_text, evaluation)

    if tech_acc is None:
        # A duplicate submission raced ahead and already stored this answer;
        # that request owns the advance/finalize work, so just refresh state.
        return redirect(url_for(".live", interview_id=interview_id))

    finished = (
        _answered_count(interview_id) >= interview["question_limit"]
        or _remaining_seconds(interview) <= 0
    )
    if finished:
        _finalize(get_interview(interview_id))
        return redirect(url_for(".complete", interview_id=interview_id))

    try:
        _advance(service, get_interview(interview_id), question, answer_text,
                 tech_acc)
    except GeminiError as exc:
        return _redirect_pending(interview_id, exc)

    return redirect(url_for(".live", interview_id=interview_id))


@interview_bp.route("/interview/<int:interview_id>/continue", methods=["POST"])
@login_required
def continue_interview(interview_id):
    """Retry the failed adaptive step (recovery affordance)."""
    check_gemini_limit()
    interview = _owned_real_interview(interview_id)
    if interview is None:
        abort(404)
    if interview["status"] == "completed":
        return redirect(url_for(".complete", interview_id=interview_id))
    if get_open_question(interview_id) is not None:
        return redirect(url_for(".live", interview_id=interview_id))

    entry = _last_answered_entry(interview_id)
    if entry is None:
        _finalize(interview)
        return redirect(url_for(".complete", interview_id=interview_id))

    question_row = get_question_with_interview(entry["question_id"])
    service = current_app.extensions["gemini"]
    try:
        _advance(service, interview, question_row, entry["user_answer"],
                 _tech_accuracy_of(entry))
    except GeminiError as exc:
        return _redirect_pending(interview_id, exc)

    return redirect(url_for(".live", interview_id=interview_id))


@interview_bp.route("/interview/<int:interview_id>/finish", methods=["POST"])
@login_required
def finish(interview_id):
    """End the interview early and grade what was answered so far."""
    interview = _owned_real_interview(interview_id)
    if interview is None:
        abort(404)
    if interview["status"] != "completed":
        _finalize(interview)
    return redirect(url_for(".complete", interview_id=interview_id))


@interview_bp.route("/interview/<int:interview_id>/complete", methods=["GET"])
@login_required
def complete(interview_id):
    interview = _owned_real_interview(interview_id)
    if interview is None:
        abort(404)
    if interview["status"] != "completed":
        return redirect(url_for(".live", interview_id=interview_id))

    transcript = get_transcript(interview_id)
    scored = [row for row in transcript if row["score"] is not None]

    dimension_totals = {}
    for row in scored:
        for key, value in (row["scores"] or {}).items():
            dimension_totals.setdefault(key, []).append(value)
    averages = {
        key: round(sum(values) / len(values), 1)
        for key, values in dimension_totals.items()
    }

    return render_template(
        "interview.html",
        active_page="interview",
        view="complete",
        interview=interview,
        type_label=INTERVIEW_TYPES.get(interview["type"], interview["type"]),
        transcript=transcript,
        overall_score=interview["overall_score"],
        averages=averages,
        dimensions=SCORE_DIMENSIONS,
        graded_count=len(scored),
        company_preset=_company_preset_for(interview),
    )


# ---------------------------------------------------------------------------
# Adaptive engine helpers (blueprint Section H)
# ---------------------------------------------------------------------------

def _advance(service, interview, answered_question, answer_text, tech_acc):
    """Prepare the next question adaptively. Raises GeminiError on failure.

    H.1 extract entities -> H.2 pick the most interview-worthy mention ->
    H.3/H.4 depth-adjusted follow-up prompt -> H.5 last-two-pairs context.
    With no usable entity the interview falls back to a fresh standalone
    question so the session always continues.
    """
    interview_id = interview["id"]
    entity = None
    try:
        extraction = service.generate(
            "extract_entities",
            {
                "question": answered_question["question"],
                "answer": answer_text,
            },
        )
        entity = _select_entity(extraction.get("entities") or [])
    except GeminiRateLimitError:
        raise
    except GeminiError:
        entity = None

    if entity is not None:
        follow_up_inputs = {
            "question": answered_question["question"],
            "answer": answer_text,
            "entity": entity["text"],
            "depth": _depth_for(tech_acc),
            "context": _context_window(interview_id,
                                       exclude=answered_question["id"]),
        }
        # Company Presets (Stage 16): an additional contextual signal only —
        # the follow-up always probes what the candidate's own mention opened.
        preset = _company_preset_for(interview)
        if preset is not None:
            follow_up_inputs["company"] = company_context_for(preset)
        follow_up = service.generate("generate_follow_up", follow_up_inputs)
        add_question(interview_id, follow_up["follow_up_question"], "follow-up", [])
        return

    # Fallback: nothing worth probing deeper -> a fresh question that keeps
    # the same role/type/difficulty shape (and company context, if any).
    inputs = {
        "role": interview["role"],
        "topic": TYPE_TOPICS.get(interview["type"], "general skills"),
        "difficulty": interview["difficulty"],
    }
    preset = _company_preset_for(interview)
    if preset is not None:
        inputs["company"] = company_context_for(preset)
    fresh = service.generate("generate_question", inputs)
    add_question(
        interview_id,
        fresh["question"],
        fresh.get("question_type", ""),
        fresh.get("expected_concepts", []),
    )


def _select_entity(entities):
    """Prioritize project/technology mentions over generic claims (H.2)."""
    best = None
    best_rank = len(_ENTITY_KIND_RANK) + 1
    for entity in entities:
        text = str((entity or {}).get("text", "")).strip()
        kind = (entity or {}).get("kind", "other")
        if not text:
            continue
        rank = _ENTITY_KIND_RANK.get(kind, len(_ENTITY_KIND_RANK))
        if rank < best_rank:
            best = {"text": text, "kind": kind}
            best_rank = rank
    return best


def _depth_for(technical_accuracy):
    """Adaptive depth instruction from the previous technical score (H.4)."""
    if technical_accuracy is None:
        return "standard"
    if technical_accuracy < 50:
        return "foundational"
    if technical_accuracy >= 75:
        return "deeper"
    return "standard"


def _context_window(interview_id, pairs=2, exclude=None):
    """Last `pairs` completed Q&A exchanges (H.5); ephemeral prompt context."""
    rows = [
        row for row in get_transcript(interview_id)
        if row["user_answer"] is not None and row["question_id"] != exclude
    ]
    return [
        {"question": row["question"], "answer": row["user_answer"]}
        for row in rows[-pairs:]
    ]


def _last_answered_entry(interview_id):
    rows = [
        row for row in get_transcript(interview_id)
        if row["user_answer"] is not None
    ]
    return rows[-1] if rows else None


def _answered_count(interview_id):
    return sum(
        1 for row in get_transcript(interview_id)
        if row["user_answer"] is not None
    )


def _tech_accuracy_of(entry):
    return (entry.get("scores") or {}).get("technical_accuracy")


def _save_evaluation(interview, question, answer_text, evaluation):
    """Store the hidden per-answer evaluation + performance row.

    Returns the technical_accuracy score, or None when a duplicate
    submission means another request already stored this answer (the
    caller must then skip finalize/advance to avoid double work).
    """
    inserted, _, technical_accuracy = store_evaluation(
        question["id"],
        answer_text,
        evaluation,
        user_id=g.user["id"],
        skill_label=interview["type"].capitalize(),
        interview_id=interview["id"],
    )
    return technical_accuracy if inserted else None


def _finalize(interview):
    """Close the session: overall score = mean of evaluated answers (B.5)."""
    transcript = get_transcript(interview["id"])
    overall = mean_score(row["score"] for row in transcript)
    set_interview_status(interview["id"], "completed", overall_score=overall)

    # Stage 4 (blueprint K.8): aggregate weaknesses and refresh the
    # InterviewMemory derived cache once the session is closed.
    on_interview_completed(interview["user_id"], interview, transcript)

    # Stage 3 (Phase 9): a finished, graded session can unlock achievements.
    evaluate_achievements(interview["user_id"])

    # Stage 14: one 50 XP award for the whole interview, only when the
    # session actually produced a grade (an empty session that expired with
    # no graded answers never pays). The UNIQUE(user_id, source, event_key)
    # boundary keeps a raised/finished duplicate from ever paying twice.
    if overall is not None:
        xp.award(interview["user_id"], xp.SRC_REAL_INTERVIEW,
                 interview["id"], xp.XP_REAL_INTERVIEW)
    xp.award_achievement_bonuses(interview["user_id"])


def _remaining_seconds(interview):
    started = datetime.strptime(
        interview["date"], "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=timezone.utc)
    deadline = started.timestamp() + int(interview["duration_minutes"]) * 60
    return int(deadline - datetime.now(timezone.utc).timestamp())


def _owned_real_interview(interview_id):
    interview = get_interview(interview_id)
    if interview is None or interview["user_id"] != g.user["id"]:
        return None
    if interview["mode"] != "real":
        return None
    return interview


def _render_live_error(interview, preserved_answer, exc):
    """Re-render the live screen with the candidate's text intact."""
    if isinstance(exc, GeminiConfigError):
        message = (
            "AI features are not configured yet. Ask an operator to set the "
            "GEMINI_API_KEY on the server."
        )
        category = "warning"
    elif isinstance(exc, GeminiRateLimitError):
        message = (
            "The AI coach is rate limited right now. Your answer was kept — "
            "wait a moment and submit it again."
        )
        category = "warning"
    else:
        message = (
            "The AI coach could not evaluate your answer right now. Please "
            "try submitting it again."
        )
        category = "error"

    flash(message, category)
    return render_template(
        "interview.html",
        active_page="interview",
        view="live",
        interview=interview,
        q=get_open_question(interview["id"]),
        question_number=_answered_count(interview["id"]) + 1,
        remaining_seconds=max(_remaining_seconds(interview), 0),
        dimensions=SCORE_DIMENSIONS,
        preserved_answer=preserved_answer,
        type_label=INTERVIEW_TYPES.get(interview["type"], interview["type"]),
        company_preset=_company_preset_for(interview),
    )


def _redirect_pending(interview_id, exc):
    interview = get_interview(interview_id)
    reason = (
        "The AI coach is rate limited right now."
        if isinstance(exc, GeminiRateLimitError)
        else "The AI coach could not prepare your next question."
    )
    flash(
        f"{reason} Your answer is saved — retry below, or finish to see "
        f"your results.",
        "warning" if isinstance(exc, GeminiRateLimitError) else "error",
    )
    return render_template(
        "interview.html",
        active_page="interview",
        view="pending",
        interview=interview,
        type_label=INTERVIEW_TYPES.get(interview["type"], interview["type"]),
        graded_count=_answered_count(interview_id),
        company_preset=_company_preset_for(interview),
    )


def _company_preset_for(interview):
    """Resolve the interview's allowlisted company preset (or None)."""
    if interview is None:
        return None
    key = (
        interview["company_key"]
        if hasattr(interview, "keys") and "company_key" in interview.keys()
        else None
    )
    return company_preset_by_key(key) if key else None
