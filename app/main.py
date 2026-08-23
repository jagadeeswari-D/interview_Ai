"""Public and authenticated main routes: landing page and dashboard.

The dashboard is the Stage 6 upgraded home page: real readiness/stat tiles,
a mini score trend (server-rendered inline SVG — no JS dependency) and the
latest activity, mirroring blueprint Section E's dashboard description.
"""

from flask import Blueprint, g, redirect, render_template, url_for

from .analytics import compute_readiness
from .auth import login_required
from .models import (
    get_skill_averages,
    get_weaknesses,
    list_completed_interviews,
)

main_bp = Blueprint("main", __name__)

SPARK_POINTS = 12  # sessions shown in the dashboard mini trend
RECENT_ACTIVITY_COUNT = 5


@main_bp.route("/")
def landing():
    if g.get("user"):
        return redirect(url_for("main.dashboard"))
    return render_template("landing.html")


@main_bp.route("/dashboard")
@login_required
def dashboard():
    completed = list_completed_interviews(g.user["id"])   # newest first
    real_count = sum(1 for row in completed if row["mode"] == "real")
    practice_count = len(completed) - real_count
    weakness_count = len(get_weaknesses(g.user["id"]))
    scores_chronological = [
        row["overall_score"] for row in reversed(completed)
        if row["overall_score"] is not None
    ]
    readiness = compute_readiness(
        get_skill_averages(g.user["id"]),
        scores_chronological,
        weakness_count,
    )

    return render_template(
        "dashboard.html",
        active_page="dashboard",
        readiness=readiness,
        interview_count=real_count,
        practice_count=practice_count,
        weakness_count=weakness_count,
        spark_points=_spark_points(scores_chronological),
        spark_count=len(scores_chronological[-SPARK_POINTS:]),
        recent=completed[:RECENT_ACTIVITY_COUNT],
        total_completed=len(completed),
    )


def _spark_points(scores):
    """Normalize the last scores into an SVG polyline points string."""
    values = scores[-SPARK_POINTS:]
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    width, height, pad = 100.0, 36.0, 2.0
    step = (width - 2 * pad) / (len(values) - 1)
    points = []
    for index, value in enumerate(values):
        x = pad + index * step
        y = height - pad - (value - low) / span * (height - 2 * pad)
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)
