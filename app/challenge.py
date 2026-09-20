"""Daily Challenge (Phase 9 / Stage 1).

One deterministic interview question per user per calendar day, drawn from a
curated local pool — no Gemini call, so it works without an API key and never
touches the Gemini rate limit. The server is authoritative for both identity
and completion:

  * the question is picked deterministically from (user_id, challenge_date)
    and written to SQLite the first time the page is opened, so a refresh
    returns the same challenge and never creates a duplicate row;
  * completion is written only by the CSRF-protected POST /challenge/answer
    after ownership + day + status checks; the browser never decides state.

A previous day's row stays stored but is never shown as today's active
challenge (lookups are scoped to today's local date).
"""

import datetime as _datetime
import hashlib
import json

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
from .achievements import evaluate_achievements
from . import xp
from .models import (
    complete_daily_challenge,
    create_daily_challenge,
    get_daily_challenge,
    get_daily_challenge_by_id,
)

challenge_bp = Blueprint("challenge", __name__)

ANSWER_MAX_LENGTH = 5000

# Curated, deterministic local question pool. Rotates across users and days via
# a stable hash (see _pick_challenge), so every challenge is unique per
# (user, day) without any random or AI dependency.
CHALLENGE_POOL = [
    {
        "question": "Explain the difference between a LEFT JOIN and an INNER "
                    "JOIN in SQL.",
        "question_type": "conceptual",
        "expected_concepts": [
            "INNER JOIN returns only matching rows",
            "LEFT JOIN keeps every left-table row",
            "NULLs appear for non-matching right rows",
            "A concrete example",
        ],
    },
    {
        "question": "What is the time complexity of binary search and why?",
        "question_type": "conceptual",
        "expected_concepts": [
            "O(log n)",
            "The search space halves each step",
            "Requires a sorted collection",
        ],
    },
    {
        "question": "Walk through how you would design a REST API endpoint "
                    "for creating a new user.",
        "question_type": "practical",
        "expected_concepts": [
            "POST /users with a JSON body",
            "Validation and a 400 for bad input",
            "A 201 response for success",
            "Duplicate-email handling",
        ],
    },
    {
        "question": "Explain big-O notation in your own words and give an "
                    "example of two complexity classes.",
        "question_type": "conceptual",
        "expected_concepts": [
            "Describes how runtime or memory grows",
            "Worst-case vs average-case thinking",
            "Comparisons such as O(n) vs O(log n)",
        ],
    },
    {
        "question": "Describe the difference between a stack and a queue, "
                    "with a real-world analogy for each.",
        "question_type": "conceptual",
        "expected_concepts": [
            "LIFO for stacks, FIFO for queues",
            "Stack examples: undo history, call stack",
            "Queue examples: printer line, task queue",
        ],
    },
    {
        "question": "What does 'clean code' mean to you? Give one principle "
                    "you actively apply.",
        "question_type": "conceptual",
        "expected_concepts": [
            "Readability and honest naming",
            "Small, focused functions",
            "Single responsibility",
        ],
    },
    {
        "question": "How would you explain the difference between HTTP 404 "
                    "and HTTP 500 to a client?",
        "question_type": "practical",
        "expected_concepts": [
            "404: the resource was not found",
            "500: the server failed internally",
            "Client mistake vs server mistake",
        ],
    },
    {
        "question": "How do database indexes speed up queries, and what "
                    "trade-offs do they bring?",
        "question_type": "conceptual",
        "expected_concepts": [
            "Faster lookups instead of full scans",
            "Slower writes and extra storage",
            "Choosing the right column to index",
        ],
    },
    {
        "question": "What is encapsulation in OOP and why does it matter?",
        "question_type": "conceptual",
        "expected_concepts": [
            "Hiding internal state behind methods",
            "Exposing a controlled interface",
            "Reducing coupling between classes",
        ],
    },
    {
        "question": "Describe how you would debug a page that loads slowly "
                    "in a browser.",
        "question_type": "scenario",
        "expected_concepts": [
            "Inspect the Network tab and waterfall",
            "Identify the largest requests or assets",
            "Caching, compression, and lazy loading",
        ],
    },
    {
        "question": "When would you pair a primary key with a foreign key to "
                    "model a relationship?",
        "question_type": "conceptual",
        "expected_concepts": [
            "Primary key identifies each row",
            "Foreign key references another table's key",
            "A one-to-many example",
        ],
    },
    {
        "question": "What distinguishes HTTP GET from POST, and when do you "
                    "use each?",
        "question_type": "conceptual",
        "expected_concepts": [
            "GET is for retrieval and is idempotent",
            "POST creates or changes server state",
            "Parameters in the URL vs the request body",
        ],
    },
    {
        "question": "Talk through the steps you take before starting a new "
                    "feature.",
        "question_type": "behavioral",
        "expected_concepts": [
            "Clarify the requirement",
            "Plan the approach and edge cases",
            "Code, test, and review",
        ],
    },
    {
        "question": "What is the difference between an array and a linked "
                    "list?",
        "question_type": "conceptual",
        "expected_concepts": [
            "Contiguous memory vs nodes with pointers",
            "Random access O(1) vs O(n)",
            "Insert-and-delete trade-offs",
        ],
    },
    {
        "question": "Explain what a PRIMARY KEY's UNIQUE and NOT NULL "
                    "constraints guarantee.",
        "question_type": "conceptual",
        "expected_concepts": [
            "No duplicate rows",
            "No NULL identifiers",
            "Stable identity for references",
        ],
    },
    {
        "question": "A recruiter asks you to describe a bug you found and "
                    "fixed. What do you share?",
        "question_type": "behavioral",
        "expected_concepts": [
            "One specific, concrete example",
            "The root cause, not just the symptom",
            "The fix and how it was verified",
        ],
    },
]


def _today():
    """The local calendar day key (YYYY-MM-DD)."""
    return _datetime.date.today().isoformat()


def _pick_challenge(user_id, challenge_date):
    """Deterministically select today's challenge for one user.

    A stable SHA-256 over (user_id, date) means the same user sees the same
    question all day, different users generally get different questions, and
    tomorrow is a fresh selection — with no randomness and no storage lookup.
    """
    digest = hashlib.sha256(
        f"interviewiq:daily:{user_id}:{challenge_date}".encode("utf-8")
    ).hexdigest()
    index = int(digest[:8], 16) % len(CHALLENGE_POOL)
    return CHALLENGE_POOL[index]


@challenge_bp.route("/challenge", methods=["GET"])
@login_required
def view():
    """Show the user's challenge for today, creating it exactly once."""
    user_id = g.user["id"]
    today = _today()
    challenge = get_daily_challenge(user_id, today)
    if challenge is None:
        picked = _pick_challenge(user_id, today)
        challenge = create_daily_challenge(
            user_id,
            today,
            picked["question"],
            picked["question_type"],
            picked["expected_concepts"],
        )
    return render_template(
        "challenge.html",
        active_page="challenge",
        challenge=challenge,
        concepts=json.loads(challenge["expected_concepts"] or "[]"),
    )


@challenge_bp.route("/challenge/answer", methods=["POST"])
@login_required
def answer():
    """Record the answer and complete today's challenge.

    Guards: the challenge must exist, belong to the signed-in user, be for
    today's date, and not already be completed. Any violation is a 404 (so a
    foreign or stale id reveals nothing) and repeated submissions are
    idempotent — the answer is written once.
    """
    challenge_id = request.form.get("challenge_id", type=int)
    answer_text = (request.form.get("answer") or "").strip()

    row = get_daily_challenge_by_id(challenge_id) if challenge_id else None
    if (
        row is None
        or row["user_id"] != g.user["id"]
        or row["challenge_date"] != _today()
    ):
        abort(404)

    if row["status"] == "completed":
        return redirect(url_for("challenge.view"))

    if not answer_text:
        flash("Write your answer before submitting.", "error")
        return redirect(url_for("challenge.view"))
    if len(answer_text) > ANSWER_MAX_LENGTH:
        flash(
            f"Answers are limited to {ANSWER_MAX_LENGTH} characters.",
            "error",
        )
        return redirect(url_for("challenge.view"))

    complete_daily_challenge(challenge_id, answer_text)
    evaluate_achievements(g.user["id"])

    # Stage 14: 25 XP for today's completed challenge. The unique ledger
    # boundary means a raced or repeated submission can never pay twice.
    xp.award(g.user["id"], xp.SRC_DAILY_CHALLENGE, challenge_id,
             xp.XP_DAILY_CHALLENGE)
    xp.award_achievement_bonuses(g.user["id"])

    flash("Challenge completed — nice work today!", "success")
    return redirect(url_for("challenge.view"))