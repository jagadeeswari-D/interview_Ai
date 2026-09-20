"""Public and authenticated main routes: landing page and dashboard.

The dashboard is the product home page after login: real KPI tiles, a
professional performance-over-time chart (Chart.js, data from
GET /dashboard/data), per-skill performance bars, recent activity and quick
actions — all derived from stored data, never fabricated. The visual language
mirrors the landing/login pages (same tokens, fonts and accent) while staying
a genuine application interface.
"""

import datetime as _datetime

from flask import Blueprint, g, jsonify, redirect, render_template, url_for

from .achievements import achievement_summary
from .analytics import compute_readiness
from .auth import login_required
from .xp import total_xp
from .models import (
    average_overall_score,
    count_answers,
    get_chart_series,
    get_daily_challenge,
    get_performance_series,
    get_profile,
    get_roadmap,
    get_skill_averages,
    get_weaknesses,
    list_completed_interviews,
    streak_summary,
)

main_bp = Blueprint("main", __name__)

RECENT_ACTIVITY_COUNT = 5


@main_bp.route("/")
def landing():
    if g.get("user"):
        return redirect(url_for("main.dashboard"))
    return render_template("landing.html")


@main_bp.route("/favicon.ico")
def favicon():
    """Browser shorthand icon: route the root .ico request to the SVG.

    The pages declare ``favicon.svg`` via the link tag, but most browsers
    still probe ``/favicon.ico`` unconditionally; without this route the
    probe returns a 404 that shows up as a console error on every page.
    """
    return redirect(url_for("static", filename="favicon.svg"))


@main_bp.route("/dashboard")
@login_required
def dashboard():
    user_id = g.user["id"]
    completed = list_completed_interviews(user_id)          # newest first
    weakness_count = len(get_weaknesses(user_id))
    skill_rows = get_skill_averages(user_id)
    scores_chronological = [
        row["overall_score"] for row in reversed(completed)
        if row["overall_score"] is not None
    ]
    readiness = compute_readiness(skill_rows, scores_chronological, weakness_count)

    activity = _build_activity(completed, weakness_count)
    series = get_performance_series(user_id)

    profile = get_profile(user_id)
    roadmap = get_roadmap(user_id)
    roadmap_done = sum(1 for day in roadmap if day["status"] == "completed")
    roadmap_total = len(roadmap)
    roadmap_next = next(
        (day for day in roadmap if day["status"] != "completed"), None
    )

    improvement = None
    if len(scores_chronological) >= 2:
        improvement = round(scores_chronological[-1] - scores_chronological[-2], 1)

    weakest = get_weaknesses(user_id)[:5]
    strongest = skill_rows[:3]
    streak = streak_summary(user_id)

    achievement_rows = achievement_summary(user_id)

    return render_template(
        "dashboard.html",
        active_page="dashboard",
        user_name=g.user["name"],
        user_initial=(g.user["name"][:1] or "U").upper(),
        total_completed=len(completed),
        questions_practiced=count_answers(user_id),
        average_score=average_overall_score(user_id),
        total_xp=total_xp(user_id),
        current_streak=streak["current_streak"],
        longest_streak=streak["longest_streak"],
        streak_today_completed=streak["today_completed"],
        weakness_count=weakness_count,
        interview_count=sum(1 for r in completed if r["mode"] == "real"),
        practice_count=sum(1 for r in completed if r["mode"] != "real"),
        readiness=readiness,
        skill_rows=skill_rows,
        recent=activity,
        has_series=bool(series),
        trend_count=len(series),
        now_hour=_datetime.datetime.now().hour,
        target_role=(profile["role"].strip() if profile and profile["role"] else None),
        has_resume=bool(profile and profile["resume_path"].strip()),
        roadmap=roadmap,
        roadmap_done=roadmap_done,
        roadmap_total=roadmap_total,
        roadmap_next=roadmap_next,
        improvement=improvement,
        weakest=weakest,
        strongest=strongest,
        daily_challenge=get_daily_challenge(
            user_id, _datetime.date.today().isoformat()
        ),
        achievements=achievement_rows,
        achievements_unlocked=sum(1 for row in achievement_rows if row["unlocked"]),
        achievements_total=len(achievement_rows),
        has_data=bool(completed or skill_rows),
    )


@main_bp.route("/dashboard/data", methods=["GET"])
@login_required
def dashboard_data():
    """Aggregate chart payload for the dashboard (Chart.js).

    Exposes only aggregate numbers plus truthful per-session descriptors
    (mode/role/topic/date) built from the real stored interview rows — never
    user identifiers, answers, feedback or credentials.
    """
    rows = get_chart_series(g.user["id"])
    return jsonify({
        "trend": {
            "labels": [row["date"][:10] for row in rows],
            "scores": [row["overall_score"] for row in rows],
            "points": [
                {
                    "date": row["date"],
                    "score": row["overall_score"],
                    "name": _session_title(row),
                    "mode": row["mode"],
                }
                for row in rows
            ],
        },
    })


def _session_title(row):
    """Short label for one completed session, from real stored values only."""
    is_real = row["mode"] == "real"
    title = row["role"] if is_real else row["type"]
    if not title:
        title = "Real interview" if is_real else "Practice session"
    return title


def _build_activity(completed, weakness_count):
    """Recent activity list, newest first, from real stored events.

    Completed sessions come from the interviews table; weakness/roadmap
    signals are summarized only when they exist. Returns [] when the user
    has done nothing graded yet.
    """
    activity = []
    for row in completed[:RECENT_ACTIVITY_COUNT]:
        is_real = row["mode"] == "real"
        title = row["role"] if is_real else row["type"]
        if not title:
            title = "Real interview" if is_real else "Practice session"
        score = row["overall_score"]
        if score is not None:
            tier = "high" if score >= 75 else ("mid" if score >= 50 else "low")
            desc = f"Completed with a score of {score:.0f}/100"
            badge = score
        else:
            tier = "none"
            desc = "Session finished — answers not graded"
            badge = None
        activity.append({
            "kind": "interview",
            "icon": "real" if is_real else "practice",
            "title": title,
            "desc": desc,
            "when": row["date"],
            "link": url_for("replay.walkthrough", interview_id=row["id"]),
            "badge": badge,
            "tier": tier,
        })
    if weakness_count and not activity:
        activity.append({
            "kind": "weakness",
            "icon": "weakness",
            "title": "Weak spots detected",
            "desc": f"{weakness_count} area(s) flagged for focused practice.",
            "when": None,
            "link": url_for("roadmap.view"),
            "badge": None,
            "tier": "none",
        })
    return activity
