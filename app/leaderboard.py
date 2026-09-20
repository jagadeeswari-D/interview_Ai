"""Leaderboard (Stage 15) — privacy-safe, read-only XP ranking.

Ranks authenticated users by lifetime XP derived directly from the
authoritative Stage 14 xp_ledger. The page is GET-only and side-effect free:
it never awards, deducts or recalculates XP, never touches achievement,
challenge or interview state, and never accepts XP or identity input from
the client (there is no client input at all).

Public rows expose exactly three fields — rank, display name, total XP —
plus an `is_self` marker so the signed-in user can find their own position.
No email, user id, profile, resume, interview, answer, note, weakness,
roadmap, company or challenge data is ever selected, serialized or rendered.
"""

from flask import Blueprint, g, render_template

from .auth import login_required
from .xp import total_xp
from .models import (
    leaderboard_participant_count,
    leaderboard_rows,
    xp_rank,
)

leaderboard_bp = Blueprint("leaderboard", __name__)

LEADERBOARD_LIMIT = 50


def _with_ranks(rows, viewer_user_id):
    """Attach a competition rank to each ordered row and mark the viewer.

    Rows arrive ordered by (total_xp DESC, user_id ASC). A competition rank
    stays the same while the total is tied and only advances (to the 1-based
    position) when the total changes — the standard 1,1,3 ranking. Only the
    public fields (rank, name, total_xp) plus the is_self flag are produced;
    user ids never leave this function.
    """
    ranked = []
    position = 0
    rank = 0
    previous_total = None
    for row in rows:
        position += 1
        if previous_total is None or int(row["total_xp"]) < previous_total:
            rank = position
        previous_total = int(row["total_xp"])
        ranked.append(
            {
                "rank": rank,
                "name": row["name"],
                "total_xp": int(row["total_xp"]),
                "is_self": int(row["user_id"]) == viewer_user_id,
            }
        )
    return ranked


@leaderboard_bp.route("/leaderboard", methods=["GET"])
@login_required
def view():
    """The authenticated leaderboard page (GET only — nothing to mutate)."""
    viewer = g.user["id"]
    rows = leaderboard_rows(LEADERBOARD_LIMIT)
    entries = _with_ranks(rows, viewer)

    own_xp = total_xp(viewer)
    own_rank = xp_rank(viewer)

    return render_template(
        "leaderboard.html",
        active_page="leaderboard",
        entries=entries,
        limit=LEADERBOARD_LIMIT,
        participant_count=leaderboard_participant_count(),
        own_rank=own_rank,
        own_xp=own_xp,
    )