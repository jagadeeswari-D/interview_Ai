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
from .interview import INTERVIEW_TYPES
from .models import (
    get_interview,
    get_interview_memory,
    get_open_question,
    get_transcript,
    list_completed_interviews,
)

history_bp = Blueprint("history", __name__)

MODE_FILTERS = ("practice", "real")


@history_bp.route("/history", methods=["GET"])
@login_required
def list_view():
    mode = (request.args.get("mode") or "").strip().lower()
    if mode not in MODE_FILTERS:
        mode = None

    interviews = list_completed_interviews(g.user["id"], mode=mode)
    memory = get_interview_memory(g.user["id"])
    return render_template(
        "history.html",
        active_page="history",
        view="list",
        interviews=interviews,
        memory=memory,
        mode_filter=mode,
        modes=MODE_FILTERS,
        type_labels=INTERVIEW_TYPES,
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

    dimension_totals = {}
    for row in transcript:
        for key, value in (row["scores"] or {}).items():
            dimension_totals.setdefault(key, []).append(value)
    averages = {
        key: round(sum(values) / len(values), 1)
        for key, values in dimension_totals.items()
    }

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
        graded_count=sum(
            1 for row in transcript if row["user_answer"] is not None
        ),
    )


def _type_label(interview):
    if interview["mode"] == "real":
        return INTERVIEW_TYPES.get(interview["type"], interview["type"])
    return interview["type"]  # practice rows keep the topic in `type`
