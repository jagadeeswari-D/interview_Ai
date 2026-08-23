"""Performance Analytics (Stage 6 Item 11, blueprint Sections B.10/C/D/E/K.11).

Server-side computation of everything the analytics UI shows:

* **Placement Readiness Score** — Section C requires a "transparent weighted
  formula, explicitly labeled as self-assessment"; Section 0 #6 replaced any
  black-box AI score with exactly such a formula. It is therefore pure,
  deterministic arithmetic over stored data — it NEVER calls Gemini — and is
  recomputed on every request, which inherently satisfies B.10's "recalculated
  after every interview" because each finished interview adds Performance
  rows and an overall score that flow straight into the inputs below.

  Weights (chosen here because the blueprint deliberately does not fix them;
  shown verbatim in the UI so users can audit the result):

      Skill mastery       40 %  mean of per-skill average answer scores
      Interview results   35 %  mean overall score of completed sessions
      Weakness control    15 %  100 minus 100 x min(weak skills / 6, 1)
      Practice experience 10 %  100 x min(completed sessions / 10, 1)

  Components without backing data are excluded and their weights are
  renormalized across the remaining signals, and the UI says how many
  signals were used. With no graded evidence at all the score is reported
  as "not enough data" rather than a misleading number.

* **Trend series** — chronological overall scores of completed sessions.
* **Skill heatmap** — per-skill averages bucketed high/mid/low.

The JSON endpoint exposes only aggregate numbers and skill labels — never
user identifiers, answers, feedback, or credentials.
"""

from flask import Blueprint, g, jsonify, render_template

from .auth import login_required
from .models import (
    get_skill_averages,
    get_weaknesses,
    list_completed_interviews,
)

analytics_bp = Blueprint("analytics", __name__)

# (key, display label, weight percent) — weights must total 100.
READINESS_COMPONENTS = [
    ("skill_mastery", "Skill mastery", 40),
    ("interview_results", "Interview results", 35),
    ("weakness_control", "Weakness control", 15),
    ("experience", "Practice experience", 10),
]

WEAKNESS_CONTROL_CAP = 6      # distinct weak skills that bottom the signal out
EXPERIENCE_TARGET_SESSIONS = 10  # completed sessions that max the signal out


@analytics_bp.route("/analytics", methods=["GET"])
@login_required
def view():
    stats = _gather_stats(g.user["id"])
    return render_template(
        "performance.html",
        active_page="analytics",
        readiness=stats["readiness"],
        skill_rows=stats["skill_rows"],
        trend=stats["trend"],
        has_any_data=stats["has_any_data"],
        components=READINESS_COMPONENTS,
    )


@analytics_bp.route("/analytics/data", methods=["GET"])
@login_required
def data():
    """Aggregate chart payloads for Chart.js (aggregate values only)."""
    stats = _gather_stats(g.user["id"])
    return jsonify({
        "trend": {
            "labels": [label for label, _score in stats["trend"]],
            "scores": [score for _label, score in stats["trend"]],
        },
        "skills": [
            {"skill": row["skill"],
             "average": round(row["average_score"], 1),
             "samples": row["samples"]}
            for row in stats["skill_rows"]
        ],
    })


def _gather_stats(user_id):
    completed = list_completed_interviews(user_id)          # newest first
    scores_chronological = [
        (row["date"][:10], row["overall_score"])
        for row in reversed(completed)
        if row["overall_score"] is not None
    ]
    skill_rows = get_skill_averages(user_id)                # best first
    weak_topics = [row["skill"] for row in get_weaknesses(user_id)]

    readiness = compute_readiness(
        skill_rows,
        [score for _label, score in scores_chronological],
        len(weak_topics),
    )

    return {
        "readiness": readiness,
        "skill_rows": skill_rows,
        "trend": scores_chronological,
        "has_any_data": bool(skill_rows or completed),
    }


def heat_tier(average):
    """Shared high/mid/low bucketing (matches badge thresholds app-wide)."""
    if average is None:
        return "low"
    if average >= 75:
        return "high"
    if average >= 50:
        return "mid"
    return "low"


def compute_readiness(skill_rows, completed_scores, weak_topic_count):
    """Deterministic Placement Readiness Score — see module docstring.

    `skill_rows` are rows with an `average_score` field (get_skill_averages),
    `completed_scores` the overall scores of completed sessions, and
    `weak_topic_count` the number of distinct recorded weak skills.
    """
    skill_values = [row["average_score"] for row in skill_rows]
    skill_mastery = (
        sum(skill_values) / len(skill_values) if skill_values else None
    )
    interview_results = (
        sum(completed_scores) / len(completed_scores)
        if completed_scores else None
    )

    # Assessed at all? Weakness control only carries meaning once answers
    # have been graded or weaknesses recorded.
    assessed = bool(skill_values or completed_scores or weak_topic_count)
    weakness_control = (
        max(0.0, 100.0 - 100.0 * min(weak_topic_count / WEAKNESS_CONTROL_CAP, 1))
        if assessed else None
    )
    experience = (
        min(len(completed_scores) / EXPERIENCE_TARGET_SESSIONS, 1) * 100
        if completed_scores else None
    )

    values = {
        "skill_mastery": skill_mastery,
        "interview_results": interview_results,
        "weakness_control": weakness_control,
        "experience": experience,
    }

    components = []
    used_weight = 0.0
    weighted_sum = 0.0
    for key, label, weight in READINESS_COMPONENTS:
        value = values[key]
        available = value is not None
        components.append({
            "key": key,
            "label": label,
            "weight": weight,
            "value": None if value is None else round(value, 1),
            "available": available,
        })
        if available:
            used_weight += weight
            weighted_sum += value * weight

    # Real graded evidence (answers or finished sessions) must exist before
    # a score is shown at all; experience alone never qualifies.
    qualified = bool(skill_values or completed_scores)

    return {
        "score": round(weighted_sum / used_weight, 1)
                 if qualified and used_weight else None,
        "label": "Self-assessment readiness indicator",
        "components": components,
        "signals_used": sum(1 for c in components if c["available"]),
        "signals_weight_total": used_weight,
        "enough_data": qualified,
    }
