"""XP (Stage 14) — Experience Points ledger.

XP is awarded only when the server itself records an authoritative,
successful completion event. The browser never supplies amounts or triggers
awards directly; every award goes through the three natural completion
boundaries the rest of the product already uses:

    Smart Practice answer graded      -> 10 XP   source 'practice_answer'
    Real Interview finalized+graded   -> 50 XP   source 'real_interview'
    Daily Challenge completed         -> 25 XP   source 'daily_challenge'
    Achievement unlocked (first time) -> bonus   source 'achievement'

Idempotency / race safety is enforced at the database level: xp_ledger has
UNIQUE(user_id, source, event_key), and every award is an
INSERT OR IGNORE against that boundary. The same logical event (a retried
POST, a browser refresh, a raced duplicate request, a repeated evaluation)
can therefore never produce a second award — the unique event identifier is
the authoritative source row's own id (question_id / interview_id /
challenge_id / achievement_key).

The ledger is the single source of truth for the user's XP total. Awarded
rows are append-only; totals are recomputed from the ledger every time, so
there is no cached counter that can drift or be raced.
"""

from .achievements import ACHIEVEMENTS
from .models import get_db, unlocked_achievement_dates

# Source identifiers recorded in the ledger.
SRC_PRACTICE_ANSWER = "practice_answer"
SRC_REAL_INTERVIEW = "real_interview"
SRC_DAILY_CHALLENGE = "daily_challenge"
SRC_ACHIEVEMENT = "achievement"

# XP policy (stage 14): exact award amounts per completion event.
XP_PRACTICE_ANSWER = 10
XP_REAL_INTERVIEW = 50
XP_DAILY_CHALLENGE = 25

# Achievement unlock bonuses. Only achievements with a defined bonus map to
# XP; everything else is deliberately XP-free.
ACHIEVEMENT_XP = {
    "first_challenge": 25,
    "streak_3": 25,
    "streak_7": 50,
    "challenge_10": 100,
    "interviews_5": 100,
}


def award(user_id, source, event_key, amount):
    """Award `amount` XP for one (user, source, event) — exactly once.

    The UNIQUE(user_id, source, event_key) constraint plus INSERT OR IGNORE
    make this idempotent and race-safe: the first call wins, every repeat is
    a no-op that returns False. Commits on its own connection, following the
    existing model-function convention of one commit per write.
    """
    db = get_db()
    cur = db.execute(
        "INSERT OR IGNORE INTO xp_ledger "
        "(user_id, source, event_key, xp) VALUES (?, ?, ?, ?)",
        (user_id, source, str(event_key), int(amount)),
    )
    db.commit()
    return cur.rowcount == 1


def total_xp(user_id):
    """Lifetime XP total from the ledger (recomputed on every read)."""
    db = get_db()
    row = db.execute(
        "SELECT COALESCE(SUM(xp), 0) AS total FROM xp_ledger WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return int(row["total"])


def award_achievement_bonuses(user_id):
    """Award the XP bonus for each currently unlocked, bonus-eligible
    achievement that has not already been awarded.

    Reads the authoritative unlock rows (user_achievements) — so a bonus only
    ever pays once an achievement is genuinely unlocked — and lets the
    unique ledger boundary decide who has already been paid, which makes
    repeated evaluation (e.g. every completion boundary) safe.
    """
    unlocked = unlocked_achievement_dates(user_id)
    for definition in ACHIEVEMENTS:
        key = definition["key"]
        bonus = ACHIEVEMENT_XP.get(key)
        if bonus and key in unlocked:
            award(user_id, SRC_ACHIEVEMENT, key, bonus)