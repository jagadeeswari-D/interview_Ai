"""Interview Replay (Stage 5 Item 9, blueprint Sections B.11/D/E/K.9).

Reconstructs a stored, completed session into a navigable per-question
walkthrough (Section E): question / your answer / AI feedback / missing
points / model answer — Section D's "Interview Replay Service: reconstructs
a stored interview session into a navigable Q→A→feedback view".

Replay is pure reconstruction from the Questions + Answers record via the
existing get_transcript() data source; it NEVER calls Gemini.

Ownership and in-progress guards mirror history.py: another user's session
is a 404, and sessions still in progress redirect to wherever they left off,
so Real Interview Mode's evaluate-only-at-the-end suppression stays intact.
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
from .interview import INTERVIEW_TYPES
from .models import (
    get_interview,
    get_open_question,
    get_transcript,
    list_completed_interviews,
)

replay_bp = Blueprint("replay", __name__)


@replay_bp.route("/replay", methods=["GET"])
@login_required
def index():
    """Landing list of replayable (completed) sessions for the signed-in user."""
    interviews = list_completed_interviews(g.user["id"])
    return render_template(
        "replay.html",
        active_page="replay",
        view="index",
        interviews=interviews,
        type_label=_type_label,
    )


@replay_bp.route("/replay/<int:interview_id>", methods=["GET"])
@login_required
def walkthrough(interview_id):
    interview = get_interview(interview_id)
    if interview is None or interview["user_id"] != g.user["id"]:
        abort(404)

    if interview["status"] != "completed":
        # Never expose partial results in replay (Real Mode reveals its
        # evaluation only at the end): send the user back to the session.
        if interview["mode"] == "real":
            return redirect(url_for("interview.live",
                                    interview_id=interview_id))
        open_question = get_open_question(interview_id)
        if open_question is not None:
            return redirect(url_for("practice.question_view",
                                    question_id=open_question["id"]))
        return redirect(url_for(".index"))

    transcript = get_transcript(interview_id)
    if not transcript:
        abort(404)

    # Per-question navigation via ?q=<1-based position>. Out-of-range values
    # clamp to the nearest end so old links never dead-end.
    try:
        position = int(request.args.get("q") or 1)
    except ValueError:
        position = 1
    position = min(max(position, 1), len(transcript))
    current = transcript[position - 1]

    return render_template(
        "replay.html",
        active_page="replay",
        view="walkthrough",
        interview=interview,
        type_label=_type_label(interview),
        transcript=transcript,
        current=current,
        position=position,
        total=len(transcript),
        prev_position=position - 1 if position > 1 else None,
        next_position=position + 1 if position < len(transcript) else None,
    )


def _type_label(interview):
    if interview["mode"] == "real":
        return INTERVIEW_TYPES.get(interview["type"], interview["type"])
    return interview["type"]  # practice rows keep the topic in `type`
