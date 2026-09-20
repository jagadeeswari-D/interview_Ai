"""Achievements / Badges (Phase 9 / Stage 3, blueprint Section H everyone).

Recognizes meaningful InterviewIQ activity with a small curated achievement
set. The whole feature is server-authoritative and side-effect free for
readers:

  * definitions are static in code — there is no configuration table that can
    drift from the product contract, and the set is intentionally small
    (no XP, no points, no leaderboards);
  * progress is recomputed on every read from the authoritative activity
    tables (completed Daily Challenge days and completed+graded interviews),
    never from anything the browser sends;
  * unlocks are persisted once, only at natural completion boundaries (challenge
    answered, answer submitted, real interview finalized) via an idempotent
    INSERT OR IGNORE, and are monotonic — once unlocked a badge stays unlocked.

The dashboard shows a compact progress panel; /achievements is the full
gallery. Both render the same `achievement_summary` structure.
"""

import datetime as _datetime

from flask import Blueprint, g, render_template

from .auth import login_required
from .models import (
    completed_challenge_dates,
    compute_streak,
    count_completed_challenge_days,
    count_graded_completed_interviews,
    unlock_achievements,
    unlocked_achievement_dates,
)

achievements_bp = Blueprint("achievements", __name__)

CATEGORY_LABELS = {
    "challenge": "Challenge",
    "streak": "Streak",
    "practice": "Practice",
}

# Curated achievement set with documented thresholds:
#   first_challenge — the first completed Daily Challenge
#   streak_3        — the Daily Challenge streak reaches 3 days
#   streak_7        — the Daily Challenge streak reaches 7 days
#   challenge_10    — 10 completed Daily Challenge days overall
#   interviews_5    — 5 completed+graded interview sessions
ACHIEVEMENTS = [
    {
        "key": "first_challenge",
        "title": "First Challenge",
        "description": "Complete your very first Daily Challenge.",
        "category": "challenge",
        "threshold": 1,
        "icon": "flag",
    },
    {
        "key": "streak_3",
        "title": "3-Day Streak",
        "description": "Complete the Daily Challenge on 3 consecutive days.",
        "category": "streak",
        "threshold": 3,
        "icon": "flame",
    },
    {
        "key": "streak_7",
        "title": "7-Day Streak",
        "description": "Complete the Daily Challenge on 7 consecutive days.",
        "category": "streak",
        "threshold": 7,
        "icon": "trophy",
    },
    {
        "key": "challenge_10",
        "title": "10 Challenges",
        "description": "Complete 10 Daily Challenges overall.",
        "category": "challenge",
        "threshold": 10,
        "icon": "target",
    },
    {
        "key": "interviews_5",
        "title": "5 Interviews",
        "description": "Finish 5 graded interview sessions.",
        "category": "practice",
        "threshold": 5,
        "icon": "briefcase",
    },
]

# Per-achievement progress function over the derived activity snapshot.
# Progress values are the raw activity count — the summary caps them for
# display, and unlock happens when the raw value reaches the threshold.
_PROGRESS_FUNCTIONS = {
    "first_challenge": lambda snapshot: snapshot["challenge_days"],
    "streak_3": lambda snapshot: snapshot["longest_streak"],
    "streak_7": lambda snapshot: snapshot["longest_streak"],
    "challenge_10": lambda snapshot: snapshot["challenge_days"],
    "interviews_5": lambda snapshot: snapshot["graded_interviews"],
}


def _activity_snapshot(user_id, today):
    """Derive the authoritative activity counts used by every achievement.

    Reading this once per request keeps the evaluation and the summary views
    consistent with each other and cheap: two aggregate queries plus the same
    completed-date list the streak metric already uses.
    """
    completed_days = completed_challenge_dates(user_id)
    return {
        "challenge_days": min(
            count_completed_challenge_days(user_id, today),
            len(completed_days),
        ),
        "longest_streak": compute_streak(completed_days, today)["longest_streak"],
        "graded_interviews": count_graded_completed_interviews(user_id),
    }


def evaluate_achievements(user_id, today=None):
    """Unlock any achievement the user's activity now qualifies for.

    Idempotent and monotonic: each qualifying achievement is persisted exactly
    once (INSERT OR IGNORE mutates nothing for repeats), so calling this at
    every completion boundary costs a no-op for already-earned badges.
    Returns the list of remaining new unlocks in achievement order.
    """
    day = today or _datetime.date.today().isoformat()
    snapshot = _activity_snapshot(user_id, day)
    reached = [
        definition["key"]
        for definition in ACHIEVEMENTS
        if _PROGRESS_FUNCTIONS[definition["key"]](snapshot)
        >= definition["threshold"]
    ]
    return unlock_achievements(user_id, reached)


def achievement_summary(user_id, today=None):
    """The full achievement gallery for one user (either the dashboard panel
    or the /achievements page renders this).

    Each entry carries the title/description/category/icon, the capped progress
    out of the threshold, an unlock flag that is true even before a persisted
    row exists (activity already reached the threshold), and the persisted
    unlock date when a row exists. Progress is capped at the threshold so the
    UI never shows more than the badge needs.
    """
    day = today or _datetime.date.today().isoformat()
    snapshot = _activity_snapshot(user_id, day)
    unlocked_dates = unlocked_achievement_dates(user_id)
    summary = []
    for definition in ACHIEVEMENTS:
        key = definition["key"]
        progress = _PROGRESS_FUNCTIONS[key](snapshot)
        reached = progress >= definition["threshold"]
        has_row = key in unlocked_dates
        unlocked_at = unlocked_dates.get(key)
        summary.append(
            {
                "key": key,
                "title": definition["title"],
                "description": definition["description"],
                "category": CATEGORY_LABELS.get(
                    definition["category"], definition["category"].capitalize()
                ),
                "icon": definition["icon"],
                "threshold": definition["threshold"],
                "progress": min(progress, definition["threshold"]),
                "unlocked": has_row or reached,
                "unlocked_at": unlocked_at[:10] if unlocked_at else None,
                "percent": (
                    100
                    if reached
                    else round(progress * 100 / definition["threshold"])
                ),
            }
        )
    return summary


@achievements_bp.route("/achievements", methods=["GET"])
@login_required
def view():
    """The full achievement gallery page (GET only — nothing to mutate)."""
    summary = achievement_summary(g.user["id"])
    unlocked_count = sum(1 for entry in summary if entry["unlocked"])
    return render_template(
        "achievements.html",
        active_page="achievements",
        achievements=summary,
        unlocked_count=unlocked_count,
        total_count=len(summary),
    )