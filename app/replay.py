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
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from .auth import login_required
from .interview import INTERVIEW_TYPES
from .models import (
    bookmarked_question_ids,
    delete_note,
    get_interview,
    get_note,
    get_open_question,
    get_transcript,
    list_bookmarks,
    list_completed_interviews,
    question_in_interview,
    save_note,
    toggle_bookmark,
)

replay_bp = Blueprint("replay", __name__)

NOTE_MAX_LENGTH = 1000  # compact private note bound (answers cap at 5000)


@replay_bp.route("/replay", methods=["GET"])
@login_required
def index():
    """Landing list of replayable (completed) sessions for the signed-in user."""
    interviews = list_completed_interviews(g.user["id"])
    saved = list_bookmarks(g.user["id"])
    return render_template(
        "replay.html",
        active_page="replay",
        view="index",
        interviews=interviews,
        saved=saved,
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
        bookmarked=bookmarked_question_ids(g.user["id"]),
        note_content=get_note(g.user["id"], current["question_id"]),
        note_max_length=NOTE_MAX_LENGTH,
    )


@replay_bp.route("/replay/<int:interview_id>/bookmark", methods=["POST"])
@login_required
def bookmark_toggle(interview_id):
    """Bookmark/unbookmark one question of the user's own interview.

    Ownership gate mirrors replay.walkthrough: the interview must belong to
    the signed-in user (404 otherwise) and the question must exist inside
    that exact interview (404 for foreign/nonexistent/bogus ids). CSRF is
    enforced globally on every state-changing request. Responds with a
    redirect back to the replay position (or to the Saved questions panel).
    """
    interview = get_interview(interview_id)
    if interview is None or interview["user_id"] != g.user["id"]:
        abort(404)

    question_id = request.form.get("question_id", type=int)
    if question_id is None or not question_in_interview(question_id, interview_id):
        abort(404)

    toggle_bookmark(g.user["id"], question_id)

    if request.form.get("back") == "saved":
        return redirect(url_for("replay.index", _anchor="saved"))

    kwargs = {"interview_id": interview_id}
    position = request.form.get("q", type=int)
    if position is not None:
        kwargs["q"] = position
    return redirect(url_for("replay.walkthrough", **kwargs))


@replay_bp.route("/replay/<int:interview_id>/note", methods=["POST"])
@login_required
def note_save(interview_id):
    """Save or clear the signed-in user's private note for one question.

    Ownership gate is identical to replay.walkthrough/bookmark_toggle: the
    interview must belong to the user (404 otherwise) and the question must
    exist inside that exact interview (404 for foreign/nonexistent/bogus
    ids). Notes are keyed by (user_id, question_id), so a note can never be
    read, updated, or deleted across users. CSRF is enforced globally.
    Validation: an empty/whitespace-only note clears any stored note; an
    oversized note is rejected with a flash and nothing is written.
    """
    interview = get_interview(interview_id)
    if interview is None or interview["user_id"] != g.user["id"]:
        abort(404)

    question_id = request.form.get("question_id", type=int)
    if question_id is None or not question_in_interview(question_id, interview_id):
        abort(404)

    kwargs = {"interview_id": interview_id}
    position = request.form.get("q", type=int)
    if position is not None:
        kwargs["q"] = position
    back = lambda: redirect(url_for("replay.walkthrough", **kwargs))

    action = request.form.get("action")
    if action == "clear":
        delete_note(g.user["id"], question_id)
        flash("Personal note cleared.", "info")
        return back()

    note = request.form.get("content", "") or ""
    if len(note) > NOTE_MAX_LENGTH:
        flash(f"Personal notes are limited to {NOTE_MAX_LENGTH} characters.", "error")
        return back()

    if not note.strip():
        # An empty/whitespace-only note means "no note": remove any stored one.
        delete_note(g.user["id"], question_id)
        flash("Personal note cleared.", "info")
    else:
        save_note(g.user["id"], question_id, note)
        flash("Personal note saved.", "success")
    return back()


def _type_label(interview):
    if interview["mode"] == "real":
        return INTERVIEW_TYPES.get(interview["type"], interview["type"])
    return interview["type"]  # practice rows keep the topic in `type`
