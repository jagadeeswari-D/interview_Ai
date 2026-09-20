"""Interview History (Stage 4 Item 8, blueprint Sections B.11/E/K.8).

List view: every completed session of the signed-in user (both modes),
newest first, filterable by mode — Section E's "filterable list of past
sessions (mode, date, score, role)".

Detail view: the full stored record of one completed session — questions,
answers, dimension scores, coach feedback, missing points and model answers.
This is the history *detail*; the dedicated per-question Replay walkthrough
is a later-phase feature (roadmap item 9) and is deliberately not built here.

Ownership: every query is scoped to `g.user`; another user's session is a
404, matching the existing practice/interview behavior. Sessions still in
progress redirect to wherever they left off instead of exposing partial
results here (Real Mode suppression stays intact).
"""

from datetime import date as _date

from flask import (
    Blueprint,
    abort,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from .auth import login_required
from .evaluation import SCORE_DIMENSIONS
from .interview import DIFFICULTIES, INTERVIEW_TYPES
from .models import (
    get_interview,
    get_interview_memory,
    get_open_question,
    get_transcript,
    list_completed_interviews,
    previous_comparable_interview,
)

history_bp = Blueprint("history", __name__)

MODE_FILTERS = ("practice", "real")
TYPE_FILTERS = tuple(k for k in INTERVIEW_TYPES)  # real-mode controlled vocab


def _filter_value(raw, allowed):
    value = (raw or "").strip().lower()
    return value if value in allowed else None


def _valid_date(raw):
    value = (raw or "").strip()
    try:
        _date.fromisoformat(value)
    except ValueError:
        return None
    return value


@history_bp.route("/history", methods=["GET"])
@login_required
def list_view():
    mode = _filter_value(request.args.get("mode"), MODE_FILTERS)
    difficulty = _filter_value(request.args.get("difficulty"), DIFFICULTIES)
    interview_type = _filter_value(
        request.args.get("type"), TYPE_FILTERS
    )
    # Interview types are a real-mode concept; practice rows keep free-text
    # topics in `type`, so a type filter is meaningless (and would return
    # zero rows) while browsing practice only.
    if mode == "practice":
        interview_type = None

    q = (request.args.get("q") or "").strip()
    if len(q) > 200:
        q = q[:200]
    date_from = _valid_date(request.args.get("from"))
    date_to = _valid_date(request.args.get("to"))

    interviews = list_completed_interviews(
        g.user["id"],
        mode=mode,
        q=q,
        difficulty=difficulty,
        interview_type=interview_type,
        date_from=date_from,
        date_to=date_to,
    )

    has_filters = bool(
        mode or difficulty or interview_type or q or date_from or date_to
    )

    # Chip/action URLs carry the other active filters so searches and
    # advanced filters survive mode switches. None values are omitted by
    # url_for, keeping the query string tidy.
    preserve_all = {
        "q": q or None,
        "difficulty": difficulty,
        "type": interview_type,
        "from": date_from,
        "to": date_to,
    }
    preserve_practice = {
        "q": q or None,
        "difficulty": difficulty,
        "from": date_from,
        "to": date_to,
    }
    clear_search = {
        "mode": mode,
        "difficulty": difficulty,
        "type": interview_type,
        "from": date_from,
        "to": date_to,
    }

    return render_template(
        "history.html",
        active_page="history",
        view="list",
        interviews=interviews,
        memory=get_interview_memory(g.user["id"]),
        mode_filter=mode,
        modes=MODE_FILTERS,
        difficulties=DIFFICULTIES,
        types=INTERVIEW_TYPES,
        type_labels=INTERVIEW_TYPES,
        q=q,
        difficulty=difficulty,
        interview_type=interview_type,
        date_from=date_from,
        date_to=date_to,
        has_filters=has_filters,
        preserve_all=preserve_all,
        preserve_practice=preserve_practice,
        clear_search=clear_search,
    )


@history_bp.route("/history/<int:interview_id>", methods=["GET"])
@login_required
def detail(interview_id):
    interview = get_interview(interview_id)
    if interview is None or interview["user_id"] != g.user["id"]:
        abort(404)

    if interview["status"] != "completed":
        # Send the user back to the session itself, never show partial
        # results in history (Real Mode reveals evaluation only at the end).
        if interview["mode"] == "real":
            return redirect(url_for("interview.live",
                                    interview_id=interview_id))
        open_question = get_open_question(interview_id)
        if open_question is not None:
            return redirect(url_for("practice.question_view",
                                    question_id=open_question["id"]))
        return redirect(url_for(".list_view"))

    transcript = get_transcript(interview_id)

    averages = _dimension_averages(transcript)
    comparison = _build_comparison(g.user["id"], interview, averages)

    return render_template(
        "history.html",
        active_page="history",
        view="detail",
        interview=interview,
        type_label=_type_label(interview),
        transcript=transcript,
        overall_score=interview["overall_score"],
        averages=averages,
        dimensions=SCORE_DIMENSIONS,
        comparison=comparison,
        graded_count=sum(
            1 for row in transcript if row["user_answer"] is not None
        ),
    )


def _dimension_averages(transcript):
    """Mean stored score per evaluation dimension across a transcript."""
    totals = {}
    for row in transcript:
        for key, value in (row["scores"] or {}).items():
            totals.setdefault(key, []).append(value)
    return {
        key: round(sum(values) / len(values), 1)
        for key, values in totals.items()
    }


def _build_comparison(user_id, interview, averages):
    """Compare the viewed session with the previous comparable session.

    Selection is delegated to models.previous_comparable_interview (same
    mode/role/difficulty/type, older, graded), so the comparison rule lives in
    one place. Returns None when there is no comparable earlier session;
    otherwise overall and per-dimension deltas computed from stored scores
    only — no evaluation formula is changed and nothing is fabricated.
    """
    previous = previous_comparable_interview(user_id, interview)
    if previous is None:
        return None

    previous_averages = _dimension_averages(get_transcript(previous["id"]))

    dimensions = []
    for key, label in SCORE_DIMENSIONS:
        current_value = averages.get(key)
        previous_value = previous_averages.get(key)
        if current_value is None or previous_value is None:
            continue
        dimensions.append({
            "label": label,
            "previous": previous_value,
            "current": current_value,
            "delta": round(current_value - previous_value, 1),
        })

    overall_delta = None
    if (interview["overall_score"] is not None
            and previous["overall_score"] is not None):
        overall_delta = round(
            interview["overall_score"] - previous["overall_score"], 1
        )

    return {
        "previous": previous,
        "type_label": _type_label(previous),
        "overall_delta": overall_delta,
        "dimensions": dimensions,
    }


def _type_label(interview):
    if interview["mode"] == "real":
        return INTERVIEW_TYPES.get(interview["type"], interview["type"])
    return interview["type"]  # practice rows keep the topic in `type`
