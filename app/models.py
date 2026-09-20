"""Data access layer for Users, Profiles, and practice/interview records.

Parameterized queries only — never string-built SQL.
"""

import datetime as _datetime
import json

from .db import get_db


def create_user(name, email, password_hash):
    db = get_db()
    cur = db.execute(
        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
        (name, email, password_hash),
    )
    db.commit()
    return get_user_by_id(cur.lastrowid)


def get_user_by_email(email):
    db = get_db()
    return db.execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()


def get_user_by_id(user_id):
    db = get_db()
    return db.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()


def create_profile(user_id):
    db = get_db()
    db.execute(
        "INSERT INTO profiles (user_id) VALUES (?)", (user_id,)
    )
    db.commit()


def get_profile(user_id):
    db = get_db()
    return db.execute(
        "SELECT * FROM profiles WHERE user_id = ?", (user_id,)
    ).fetchone()


def update_user_name(user_id, name):
    """Update the display name on the users row (Profile page)."""
    db = get_db()
    db.execute("UPDATE users SET name = ? WHERE id = ?", (name, user_id))
    db.commit()


def set_profile_details(user_id, role, skills):
    """Persist target role and skills list (Profile page, Section E).

    `skills` is a list of strings stored as a JSON array. Only these two
    columns are written: resume_path and everything else on the row are
    untouched, so Resume Analysis state survives profile edits.
    """
    db = get_db()
    db.execute(
        "UPDATE profiles SET role = ?, skills = ?, "
        "updated_at = datetime('now') WHERE user_id = ?",
        (role, json.dumps(skills), user_id),
    )
    db.commit()


def set_resume_path(user_id, path):
    """Record where the user's uploaded resume PDF is stored (Stage 6)."""
    db = get_db()
    db.execute(
        "UPDATE profiles SET resume_path = ? WHERE user_id = ?",
        (path, user_id),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Practice / interview records (Stage 2, blueprint Section F)
# ---------------------------------------------------------------------------

def create_practice_interview(user_id, role, difficulty, topic,
                              company_key=None):
    """Open a Smart Practice session for one topic.

    `company_key` is an allowlisted Company Preset key (Phase 10 / Stage 1)
    or None for General/No Company; it is validated by the caller.
    """
    db = get_db()
    cur = db.execute(
        "INSERT INTO interviews (user_id, mode, role, difficulty, type, "
        "company_key) "
        "VALUES (?, 'practice', ?, ?, ?, ?)",
        (user_id, role, difficulty, topic, company_key),
    )
    db.commit()
    return get_interview(cur.lastrowid)


def get_interview(interview_id):
    db = get_db()
    return db.execute(
        "SELECT * FROM interviews WHERE id = ?", (interview_id,)
    ).fetchone()


def set_interview_status(interview_id, status, overall_score=None):
    db = get_db()
    db.execute(
        "UPDATE interviews SET status = ?, overall_score = ? WHERE id = ?",
        (status, overall_score, interview_id),
    )
    db.commit()


def next_sequence_order(interview_id):
    db = get_db()
    row = db.execute(
        "SELECT COALESCE(MAX(sequence_order), 0) + 1 AS next FROM questions "
        "WHERE interview_id = ?",
        (interview_id,),
    ).fetchone()
    return row["next"]


def add_question(interview_id, question, question_type, expected_concepts):
    """Store one generated question; `expected_concepts` is a list."""
    db = get_db()
    cur = db.execute(
        "INSERT INTO questions "
        "(interview_id, question, question_type, sequence_order, expected_concepts) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            interview_id,
            question,
            question_type,
            next_sequence_order(interview_id),
            json.dumps(expected_concepts),
        ),
    )
    db.commit()
    return cur.lastrowid


def get_question_with_interview(question_id):
    """Return the question joined with its interview (includes user_id), or None."""
    db = get_db()
    return db.execute(
        "SELECT q.*, i.user_id, i.mode, i.role, i.difficulty, i.type AS topic, "
        "i.company_key, i.status AS interview_status "
        "FROM questions q JOIN interviews i ON i.id = q.interview_id "
        "WHERE q.id = ?",
        (question_id,),
    ).fetchone()


def save_answer(question_id, user_answer, score, scores,
                feedback, missing_points, model_answer):
    """Store the evaluation result for a question's single answer.

    `scores` (five-dimension dict) and `missing_points` are stored as JSON.
    The UNIQUE constraint on answers.question_id plus ON CONFLICT DO NOTHING
    keeps a duplicate/raced submission from crashing with an IntegrityError:
    when the question already has an answer the write is a no-op and this
    returns False (the caller then skips downstream aggregation).
    Returns True when a new answer row was written.
    """
    db = get_db()
    cur = db.execute(
        "INSERT INTO answers "
        "(question_id, user_answer, score, scores, feedback, missing_points, model_answer) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(question_id) DO NOTHING",
        (
            question_id,
            user_answer,
            score,
            json.dumps(scores),
            feedback,
            json.dumps(missing_points),
            model_answer,
        ),
    )
    db.commit()
    return cur.rowcount == 1


def get_answer(question_id):
    db = get_db()
    row = db.execute(
        "SELECT * FROM answers WHERE question_id = ?", (question_id,)
    ).fetchone()
    if row is None:
        return None
    answer = dict(row)
    answer["scores"] = json.loads(answer.get("scores") or "{}")
    answer["missing_points"] = json.loads(answer.get("missing_points") or "[]")
    return answer


def add_performance(user_id, skill, score, interview_id):
    db = get_db()
    db.execute(
        "INSERT INTO performance (user_id, skill, score, interview_id) "
        "VALUES (?, ?, ?, ?)",
        (user_id, skill, score, interview_id),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Real Interview Mode (Stage 3)
# ---------------------------------------------------------------------------

def create_real_interview(user_id, role, difficulty, interview_type,
                          question_limit, duration_minutes,
                          company_key=None):
    """Open a timed Real Interview session (mode='real').

    `company_key` is an allowlisted Company Preset key (Phase 10 / Stage 1)
    or None for General/No Company; it is validated by the caller.
    """
    db = get_db()
    cur = db.execute(
        "INSERT INTO interviews "
        "(user_id, mode, role, difficulty, type, question_limit, "
        "duration_minutes, company_key) "
        "VALUES (?, 'real', ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            role,
            difficulty,
            interview_type,
            int(question_limit),
            int(duration_minutes),
            company_key,
        ),
    )
    db.commit()
    return get_interview(cur.lastrowid)


def get_open_question(interview_id):
    """Return the most recent question of the interview that has no answer."""
    db = get_db()
    return db.execute(
        "SELECT * FROM questions WHERE interview_id = ? AND id NOT IN "
        "(SELECT question_id FROM answers) ORDER BY sequence_order DESC LIMIT 1",
        (interview_id,),
    ).fetchone()


def get_transcript(interview_id):
    """All questions of an interview with their answers (decoded JSON).

    Answer fields are None for questions that were never answered.
    """
    db = get_db()
    rows = db.execute(
        "SELECT q.id AS question_id, q.question, q.question_type, "
        "q.sequence_order, a.user_answer, a.score, a.scores, a.feedback, "
        "a.missing_points, a.model_answer "
        "FROM questions q LEFT JOIN answers a ON a.question_id = q.id "
        "WHERE q.interview_id = ? ORDER BY q.sequence_order",
        (interview_id,),
    ).fetchall()

    transcript = []
    for row in rows:
        entry = dict(row)
        entry["scores"] = json.loads(entry["scores"] or "{}")
        entry["missing_points"] = json.loads(entry["missing_points"] or "[]")
        transcript.append(entry)
    return transcript


# ---------------------------------------------------------------------------
# Weaknesses / InterviewMemory / History (Stage 4, blueprint Section F)
# ---------------------------------------------------------------------------

def record_weakness_events(user_id, skill_events, interview_id):
    """Upsert one weakness row per skill flagged during a finished interview.

    `skill_events` maps skill label -> occurrence count within that single
    interview. Callers pass only the NEW evaluations since the previous call,
    so `frequency` accumulates occurrences exactly once each. `interview_count`
    increments only when the flagged interview changes (last_interview_id
    differs), making it the distinct-interview repeated-mistake signal even
    when one session completes in several steps (practice retries).
    """
    db = get_db()
    for skill, occurrences in sorted(skill_events.items()):
        db.execute(
            "INSERT INTO weaknesses "
            "(user_id, skill, frequency, interview_count, last_interview_id) "
            "VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(user_id, skill) DO UPDATE SET "
            "frequency = frequency + excluded.frequency, "
            "interview_count = interview_count + "
            "  CASE WHEN last_interview_id = excluded.last_interview_id "
            "       THEN 0 ELSE 1 END, "
            "last_interview_id = excluded.last_interview_id, "
            "updated_at = datetime('now')",
            (user_id, skill, int(occurrences), interview_id),
        )
    db.commit()


def get_weaknesses(user_id):
    """All weakness rows for a user, worst first."""
    db = get_db()
    return db.execute(
        "SELECT * FROM weaknesses WHERE user_id = ? "
        "ORDER BY frequency DESC, skill ASC",
        (user_id,),
    ).fetchall()


def get_skill_averages(user_id):
    """Average Performance score per skill label, best first."""
    db = get_db()
    return db.execute(
        "SELECT skill, AVG(score) AS average_score, COUNT(*) AS samples "
        "FROM performance WHERE user_id = ? "
        "GROUP BY skill ORDER BY average_score DESC, skill ASC",
        (user_id,),
    ).fetchall()


def save_interview_memory(user_id, strong_topics, weak_topics,
                          repeated_mistakes):
    """Replace the derived InterviewMemory cache for a user.

    Called only by memory.refresh_interview_memory — the cache is recomputed
    from Weaknesses + Performance, never written from raw request data.
    """
    db = get_db()
    db.execute(
        "INSERT INTO interview_memory "
        "(user_id, strong_topics, weak_topics, repeated_mistakes, updated_at) "
        "VALUES (?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "strong_topics = excluded.strong_topics, "
        "weak_topics = excluded.weak_topics, "
        "repeated_mistakes = excluded.repeated_mistakes, "
        "updated_at = datetime('now')",
        (
            user_id,
            json.dumps(strong_topics),
            json.dumps(weak_topics),
            json.dumps(repeated_mistakes),
        ),
    )
    db.commit()


def get_interview_memory(user_id):
    """The decoded InterviewMemory cache row for a user, or None."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM interview_memory WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is None:
        return None
    memory = dict(row)
    memory["strong_topics"] = json.loads(memory["strong_topics"] or "[]")
    memory["weak_topics"] = json.loads(memory["weak_topics"] or "[]")
    memory["repeated_mistakes"] = json.loads(
        memory["repeated_mistakes"] or "[]"
    )
    return memory


# Searchable text for one interview: role + the practice topic, or the real
# mode's human interview-type label (so "behavioral" and "mixed (technical +
# behavioral)" both match their sessions). Kept as SQL so filtering stays in
# one parameterized query scoped to the user.
_SEARCHABLE_TEXT = (
    "LOWER(i.role || ' ' || "
    "CASE WHEN i.mode = 'real' THEN "
    "CASE i.type WHEN 'technical' THEN 'technical' "
    "WHEN 'behavioral' THEN 'behavioral' "
    "WHEN 'mixed' THEN 'mixed (technical + behavioral)' "
    "ELSE i.type END ELSE i.type END)"
)


def _escape_like(term):
    """Escape LIKE wildcards so user input always matches literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_completed_interviews(user_id, mode=None, q=None, difficulty=None,
                              interview_type=None, date_from=None, date_to=None):
    """Finished sessions for a user, newest first, with graded-answer counts.

    All filters are optional and combine with AND; every one is validated
    against a fixed set by the caller before it reaches SQL:
      - `mode` ('practice' | 'real') narrows to one mode;
      - `q` is a case-insensitive, whitespace-split substring search over the
        role and type/topic label (every term must match);
      - `difficulty` ('easy' | 'medium' | 'hard');
      - `interview_type` (real-mode type key: technical/behavioral/mixed);
      - `date_from` / `date_to` (inclusive YYYY-MM-DD range on the session
        date, using the existing `date` column — no schema change).
    Filtering is entirely server-side and stays scoped to `user_id`.
    """
    db = get_db()
    sql = (
        "SELECT i.*, (SELECT COUNT(*) FROM questions q "
        "JOIN answers a ON a.question_id = q.id "
        "WHERE q.interview_id = i.id) AS graded_count "
        "FROM interviews i WHERE i.user_id = ? AND i.status = 'completed' "
    )
    params = [user_id]
    if mode in ("practice", "real"):
        sql += "AND i.mode = ? "
        params.append(mode)
    if difficulty in ("easy", "medium", "hard"):
        sql += "AND i.difficulty = ? "
        params.append(difficulty)
    if interview_type in ("technical", "behavioral", "mixed"):
        # Interview type is a real-mode concept; practice rows keep the topic
        # in `type` and must not be matched by this controlled vocabulary.
        sql += "AND i.mode = 'real' AND i.type = ? "
        params.append(interview_type)
    if q:
        terms = [_escape_like(term) for term in q.split() if term]
        if terms:
            sql += "AND (" + " AND ".join(
                f"({_SEARCHABLE_TEXT} LIKE ? ESCAPE '\\')" for _ in terms
            ) + ") "
            params.extend("%%{0}%".format(term) for term in terms)
    if date_from:
        sql += "AND date(i.date) >= ? "
        params.append(date_from)
    if date_to:
        sql += "AND date(i.date) <= ? "
        params.append(date_to)
    sql += "ORDER BY i.date DESC, i.id DESC"
    return db.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# Learning Roadmap (Stage 5 Item 10, blueprint Section F)
# ---------------------------------------------------------------------------

def replace_roadmap(user_id, days):
    """Replace a user's whole roadmap with `days` and reset progress.

    `days` is an iterable of (skill, day_number, topic, practice_focus)
    tuples. The DELETE + INSERTs run in one implicit transaction: if any
    insert fails, the rollback discards both sides, so a regeneration never
    leaves the user with half a plan.
    """
    db = get_db()
    try:
        db.execute("DELETE FROM roadmaps WHERE user_id = ?", (user_id,))
        db.executemany(
            "INSERT INTO roadmaps "
            "(user_id, skill, day_number, topic, practice_focus) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (user_id, skill, int(day_number), topic, practice_focus)
                for skill, day_number, topic, practice_focus in days
            ],
        )
        db.commit()
    except Exception:
        db.rollback()
        raise


def get_roadmap(user_id):
    """All roadmap days for a user, ordered by day number."""
    db = get_db()
    return db.execute(
        "SELECT * FROM roadmaps WHERE user_id = ? ORDER BY day_number ASC",
        (user_id,),
    ).fetchall()


def get_roadmap_day(day_id):
    """One roadmap day row by id, or None."""
    db = get_db()
    return db.execute(
        "SELECT * FROM roadmaps WHERE id = ?", (day_id,)
    ).fetchone()


def toggle_roadmap_day(day_id):
    """Flip one day between 'pending' and 'completed'; returns new status.

    Returns None if the row no longer exists.
    """
    db = get_db()
    db.execute(
        "UPDATE roadmaps SET status = CASE WHEN status = 'completed' "
        "THEN 'pending' ELSE 'completed' END WHERE id = ?",
        (day_id,),
    )
    db.commit()
    row = db.execute(
        "SELECT status FROM roadmaps WHERE id = ?", (day_id,)
    ).fetchone()
    return row["status"] if row else None


# ---------------------------------------------------------------------------
# Dashboard metrics (real stored data only — never fabricated)
# ---------------------------------------------------------------------------

def count_answers(user_id):
    """Total number of graded answers across the user's interviews."""
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) AS total FROM answers a "
        "JOIN questions q ON q.id = a.question_id "
        "JOIN interviews i ON i.id = q.interview_id "
        "WHERE i.user_id = ?",
        (user_id,),
    ).fetchone()
    return row["total"] or 0


def average_overall_score(user_id):
    """Mean overall_score across completed, graded interviews, or None."""
    db = get_db()
    row = db.execute(
        "SELECT AVG(overall_score) AS average FROM interviews "
        "WHERE user_id = ? AND status = 'completed' "
        "AND overall_score IS NOT NULL",
        (user_id,),
    ).fetchone()
    value = row["average"]
    return round(value, 1) if value is not None else None


def get_performance_series(user_id):
    """(date, overall_score) pairs, chronological, for completed interviews."""
    db = get_db()
    rows = db.execute(
        "SELECT date, overall_score FROM interviews "
        "WHERE user_id = ? AND status = 'completed' "
        "AND overall_score IS NOT NULL ORDER BY date ASC, id ASC",
        (user_id,),
    ).fetchall()
    return [(row["date"][:10], row["overall_score"]) for row in rows]


def get_chart_series(user_id):
    """Chronological completed, graded sessions for the dashboard chart.

    Returns the full real session rows (id, date, mode, role, type,
    overall_score) so the chart can label every point truthfully instead of
    duplicating date-only labels. Same completed/overall-score filter as
    get_performance_series — nothing fabricated, nothing filtered out.
    """
    db = get_db()
    return db.execute(
        "SELECT id, date, mode, role, type, overall_score FROM interviews "
        "WHERE user_id = ? AND status = 'completed' "
        "AND overall_score IS NOT NULL ORDER BY date ASC, id ASC",
        (user_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# User Settings / Account (Settings page)
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS = {
    "theme": "system",
    "preferred_mode": "real",
    "difficulty": "medium",
    "duration": "30",
    "focus_areas": [],
    "notifications": {
        "interview_reminders": True,
        "progress_updates": True,
        "practice_reminders": True,
    },
}


def get_user_settings(user_id):
    """The decoded user_settings blob merged over defaults, or the defaults."""
    db = get_db()
    row = db.execute(
        "SELECT settings FROM user_settings WHERE user_id = ?", (user_id,)
    ).fetchone()
    merged = dict(DEFAULT_SETTINGS)
    if row is None:
        return merged
    try:
        stored = json.loads(row["settings"] or "{}")
    except (TypeError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    for key, value in stored.items():
        if key in merged:
            if isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key].update(value)
            else:
                merged[key] = value
    return merged


def set_user_settings(user_id, settings):
    """Replace the user's stored settings blob (caller passes a full dict)."""
    db = get_db()
    db.execute(
        "INSERT INTO user_settings (user_id, settings, updated_at) "
        "VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "settings = excluded.settings, updated_at = datetime('now')",
        (user_id, json.dumps(settings)),
    )
    db.commit()


def update_password_hash(user_id, password_hash):
    """Set a new password hash for the user (Account → Change password)."""
    db = get_db()
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (password_hash, user_id),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Question Bookmarks (Stage 8, Phase 8)
# ---------------------------------------------------------------------------

def question_in_interview(question_id, interview_id):
    """True when the question belongs to the given interview.

    This is the ownership gate for bookmarking: the route first verifies the
    interview belongs to the signed-in user, then requires the question to be
    part of that exact interview — so a question id can never be bookmarked
    across interviews or users.
    """
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM questions WHERE id = ? AND interview_id = ?",
        (question_id, interview_id),
    ).fetchone()
    return row is not None


def toggle_bookmark(user_id, question_id):
    """Add a bookmark if absent, remove it if present (server-side toggle).

    The UNIQUE(user_id, question_id) constraint means duplicate/raced requests
    can never create a second row. Returns True when the question is now
    bookmarked, False when it was removed.
    """
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM bookmarks WHERE user_id = ? AND question_id = ?",
        (user_id, question_id),
    ).fetchone()
    if row is not None:
        db.execute(
            "DELETE FROM bookmarks WHERE user_id = ? AND question_id = ?",
            (user_id, question_id),
        )
        db.commit()
        return False
    db.execute(
        "INSERT INTO bookmarks (user_id, question_id) VALUES (?, ?) "
        "ON CONFLICT(user_id, question_id) DO NOTHING",
        (user_id, question_id),
    )
    db.commit()
    return True


def bookmarked_question_ids(user_id):
    """Question ids bookmarked by the user, for replay-state rendering."""
    db = get_db()
    rows = db.execute(
        "SELECT question_id FROM bookmarks WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    return [row["question_id"] for row in rows]


def list_bookmarks(user_id):
    """The user's bookmarked questions with their interview/session context.

    Returns one row per bookmark newest-first: the question text, its replay
    position (sequence_order), and the owning session's id/mode/role/topic/
    date so the Replay index can identify and deep-link into the session.
    Only the user's own questions can appear here by construction.
    """
    db = get_db()
    return db.execute(
        "SELECT b.question_id, b.created_at AS bookmarked_at, "
        "q.question, q.question_type, q.sequence_order, "
        "i.id AS interview_id, i.mode, i.role, i.type, i.difficulty, i.date, "
        "i.status AS interview_status "
        "FROM bookmarks b "
        "JOIN questions q ON q.id = b.question_id "
        "JOIN interviews i ON i.id = q.interview_id "
        "WHERE b.user_id = ? "
        "ORDER BY b.id DESC",
        (user_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Personal Notes (Stage 8, Phase 8)
# ---------------------------------------------------------------------------

def get_note(user_id, question_id):
    """The user's personal note for a question, or None if absent.

    Notes are keyed by (user_id, question_id) alone: the caller has already
    verified the question belongs to one of the user's own interviews, so a
    note can only ever be the caller's own.
    """
    db = get_db()
    row = db.execute(
        "SELECT content FROM personal_notes "
        "WHERE user_id = ? AND question_id = ?",
        (user_id, question_id),
    ).fetchone()
    return row["content"] if row is not None else None


def save_note(user_id, question_id, content):
    """Create or replace the user's single note for a question (upsert).

    UNIQUE(user_id, question_id) + ON CONFLICT guarantee one row per
    user+question (update, never a duplicate).
    """
    db = get_db()
    db.execute(
        "INSERT INTO personal_notes (user_id, question_id, content, updated_at) "
        "VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(user_id, question_id) DO UPDATE SET "
        "content = excluded.content, updated_at = datetime('now')",
        (user_id, question_id, content),
    )
    db.commit()


def delete_note(user_id, question_id):
    """Remove the user's personal note for a question (idempotent)."""
    db = get_db()
    db.execute(
        "DELETE FROM personal_notes WHERE user_id = ? AND question_id = ?",
        (user_id, question_id),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Daily Challenge (Phase 9 / Stage 1)
# ---------------------------------------------------------------------------

def get_daily_challenge(user_id, challenge_date):
    """The user's challenge row for one calendar day, or None.

    `challenge_date` is the local YYYY-MM-DD key. Exactly one row can exist
    per (user, day): UNIQUE(user_id, challenge_date) is the guarantee.
    """
    db = get_db()
    return db.execute(
        "SELECT * FROM daily_challenges "
        "WHERE user_id = ? AND challenge_date = ?",
        (user_id, challenge_date),
    ).fetchone()


def get_daily_challenge_by_id(challenge_id):
    """One challenge row by id (ownership checked by the caller)."""
    db = get_db()
    return db.execute(
        "SELECT * FROM daily_challenges WHERE id = ?", (challenge_id,)
    ).fetchone()


def create_daily_challenge(user_id, challenge_date, question,
                           question_type, expected_concepts):
    """Insert today's challenge exactly once and return the stored row.

    ON CONFLICT(user_id, challenge_date) DO NOTHING means a second/simultaneous
    request can never create a second row for the same user and day — the
    unique constraint wins and the existing row is returned untouched.
    `expected_concepts` is a list stored as a JSON array.
    """
    db = get_db()
    db.execute(
        "INSERT INTO daily_challenges "
        "(user_id, challenge_date, question, question_type, expected_concepts) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, challenge_date) DO NOTHING",
        (user_id, challenge_date, question, question_type,
         json.dumps(expected_concepts)),
    )
    db.commit()
    return get_daily_challenge(user_id, challenge_date)


def complete_daily_challenge(challenge_id, answer):
    """Mark the challenge completed and record the submitted answer.

    Only the CSRF-protected answer route calls this, and only after it has
    verified the row belongs to the signed-in user, is for today, and is not
    already completed — so completion can never be overwritten or faked.
    The `AND status = 'pending'` guard makes even a raced duplicate
    submission unable to overwrite the first stored answer at the DB level.
    """
    db = get_db()
    db.execute(
        "UPDATE daily_challenges SET status = 'completed', answer = ?, "
        "completed_at = datetime('now') WHERE id = ? AND status = 'pending'",
        (answer, challenge_id),
    )
    db.commit()


def completed_challenge_dates(user_id):
    """Distinct local dates (YYYY-MM-DD) on which the user completed a
    Daily Challenge, newest first.

    The Daily Challenge completion record (status = 'completed') is the single
    authoritative activity source for the streak. `challenge_date` is the local
    calendar-day key the server wrote when the challenge was created, so the
    streak never depends on clock skew between browsers.
    """
    db = get_db()
    rows = db.execute(
        "SELECT challenge_date FROM daily_challenges "
        "WHERE user_id = ? AND status = 'completed' "
        "ORDER BY challenge_date DESC",
        (user_id,),
    ).fetchall()
    return [row["challenge_date"] for row in rows]


def compute_streak(completed_days, today):
    """Derive the Daily Challenge streak from completed challenge dates.

    Pure function — no database, no clock — so tests use deterministic dates.

    `completed_days` is an iterable of YYYY-MM-DD local dates that had a
    completed challenge (duplicates are safe and collapse into one day).
    `today` is the YYYY-MM-DD local date.

    Product contract (inherited from the previous dashboard metric): the
    current streak is the run of consecutive completed calendar days ending
    today when today is completed, otherwise the run ending yesterday — the
    streak stays alive until the day lapses. Only completed days up to and
    including today count; future-dated rows never contribute to either the
    current or the longest streak. Returns:
        current_streak, longest_streak, today_completed, yesterday_completed
    """
    days = sorted({d for d in completed_days if d and d <= today})
    if not days:
        return {
            "current_streak": 0,
            "longest_streak": 0,
            "today_completed": False,
            "yesterday_completed": False,
        }

    day_set = set(days)
    today_date = _datetime.date.fromisoformat(today)
    yesterday = (today_date - _datetime.timedelta(days=1)).isoformat()

    def run_ending(at):
        count = 0
        cursor = _datetime.date.fromisoformat(at)
        while cursor.isoformat() in day_set:
            count += 1
            cursor -= _datetime.timedelta(days=1)
        return count

    longest = 1
    run = 1
    for previous, current in zip(days, days[1:]):
        if _datetime.date.fromisoformat(previous) + _datetime.timedelta(
            days=1
        ) == _datetime.date.fromisoformat(current):
            run += 1
            longest = max(longest, run)
        else:
            run = 1

    current = 0
    if today in day_set:
        current = run_ending(today)
    elif yesterday in day_set:
        current = run_ending(yesterday)

    return {
        "current_streak": current,
        "longest_streak": longest,
        "today_completed": today in day_set,
        "yesterday_completed": yesterday in day_set,
    }


def streak_summary(user_id, today=None):
    """Server-computed streak state for one user, for the current local day.

    Composes `completed_challenge_dates` (query) with `compute_streak` (pure).
    `today` is injectable for deterministic tests and defaults to the same
    local-date convention the Daily Challenge uses
    (datetime.date.today().isoformat()).
    """
    day = today or _datetime.date.today().isoformat()
    return compute_streak(completed_challenge_dates(user_id), day)


# ---------------------------------------------------------------------------
# Achievements / Badges (Phase 9 / Stage 3)
# ---------------------------------------------------------------------------

def count_graded_completed_interviews(user_id):
    """Number of the user's interview sessions that finished with a grade.

    An interview only proves practice once it actually completed *and* produced
    an overall score; in-progress, abandoned and ungraded sessions never count.
    This mirrors the completable-rows predicate used by Performance Comparison.
    """
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) AS total FROM interviews "
        "WHERE user_id = ? AND status = 'completed' "
        "AND overall_score IS NOT NULL",
        (user_id,),
    ).fetchone()
    return row["total"] or 0


def count_completed_challenge_days(user_id, today=None):
    """Total distinct local days on which the user completed a Daily
    Challenge, counting only days up to and including `today`.

    The Daily Challenge completion record is the single authoritative activity
    source; a completed challenge is a completed day regardless of how many
    times it was completed. Future-dated rows never count (mirrors the streak
    contract), and no other table contributes to the challenge count.
    """
    day = today or _datetime.date.today().isoformat()
    db = get_db()
    row = db.execute(
        "SELECT COUNT(DISTINCT challenge_date) AS total "
        "FROM daily_challenges "
        "WHERE user_id = ? AND status = 'completed' "
        "AND challenge_date <= ?",
        (user_id, day),
    ).fetchone()
    return row["total"] or 0


def get_unlocked_achievement_keys(user_id):
    """The achievement keys this user has unlocked, in unlock order (oldest
    first via the AUTOINCREMENT id assignment)."""
    db = get_db()
    rows = db.execute(
        "SELECT achievement_key FROM user_achievements "
        "WHERE user_id = ? ORDER BY id ASC",
        (user_id,),
    ).fetchall()
    return [row["achievement_key"] for row in rows]


def unlocked_achievement_dates(user_id):
    """Map of achievement_key -> unlocked_at for one user's unlocks.

    The persisted timestamps record when the unlock happened (server side);
    the evaluated/capped progress on the UI is always recomputed on read.
    """
    db = get_db()
    rows = db.execute(
        "SELECT achievement_key, unlocked_at FROM user_achievements "
        "WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    return {row["achievement_key"]: row["unlocked_at"] for row in rows}


def unlock_achievements(user_id, keys):
    """Persist one row per newly reached achievement (idempotent).

    INSERT OR IGNORE with the UNIQUE(user_id, achievement_key) constraint makes
    re-evaluation a no-op for already-unlocked badges: repeated calls never
    duplicate rows and never rewrite `unlocked_at`. Unlock order follows the
    caller's key order; `sort` keeps the persisted sequence deterministic.
    """
    db = get_db()
    ordered = sorted({key for key in keys if key})
    if not ordered:
        return []
    db.executemany(
        "INSERT OR IGNORE INTO user_achievements "
        "(user_id, achievement_key) VALUES (?, ?)",
        [(user_id, key) for key in ordered],
    )
    db.commit()
    return [key for key in ordered
            if db.execute(
                "SELECT 1 FROM user_achievements "
                "WHERE user_id = ? AND achievement_key = ?",
                (user_id, key),
            ).fetchone()]


# ---------------------------------------------------------------------------
# Performance Comparison (Phase 8, Stage 4)
# ---------------------------------------------------------------------------

def previous_comparable_interview(user_id, interview):
    """The most recent earlier completed session comparable to `interview`.

    Comparability rule (deterministic and shown verbatim in the History
    detail UI):
      * same owner — guaranteed by the `user_id` scope, so a user can only
        ever compare their own sessions;
      * `status = 'completed'` with a stored `overall_score` (an ungraded
        session has no performance to compare);
      * same `mode`, `role`, `difficulty` and `type` as the viewed session,
        so unlike setups are never treated as equivalent;
      * strictly earlier than the viewed session by `(date, id)` — id breaks
        ties when two sessions share the same second-resolution timestamp.

    Returns the previous row, or None when this is the user's first session
    on that exact setup. Read-only and parameterized.
    """
    db = get_db()
    return db.execute(
        "SELECT * FROM interviews "
        "WHERE user_id = ? AND status = 'completed' "
        "AND overall_score IS NOT NULL "
        "AND mode = ? AND role = ? AND difficulty = ? AND type = ? "
        "AND (date < ? OR (date = ? AND id < ?)) "
        "ORDER BY date DESC, id DESC LIMIT 1",
        (
            user_id,
            interview["mode"],
            interview["role"],
            interview["difficulty"],
            interview["type"],
            interview["date"],
            interview["date"],
            interview["id"],
        ),
    ).fetchone()


# ---------------------------------------------------------------------------
# XP Leaderboard (Stage 15) — privacy-safe, read-only ranking
# ---------------------------------------------------------------------------

def leaderboard_rows(limit=50):
    """Top XP holders, ranked by total XP (desc) with user id as the
    deterministic tiebreaker.

    Totals are derived from the authoritative Stage 14 xp_ledger with a
    single SUM(xp) GROUP BY aggregation (an indexed pass over the ledger, no
    per-user N+1, no cached leaderboard table). Only users with at least one
    earned XP point are ranked — accounts with no XP are inactive and simply
    have nothing to compare. Returns (user_id, name, total_xp) rows ordered
    by (total_xp DESC, user_id ASC); the leaderboard route maps these to the
    public rank/name/XP fields and never passes user_id to the template.
    """
    db = get_db()
    return db.execute(
        "SELECT u.id AS user_id, u.name, SUM(l.xp) AS total_xp "
        "FROM xp_ledger l JOIN users u ON u.id = l.user_id "
        "GROUP BY l.user_id "
        "HAVING SUM(l.xp) > 0 "
        "ORDER BY SUM(l.xp) DESC, u.id ASC LIMIT ?",
        (int(limit),),
    ).fetchall()


def xp_rank(user_id):
    """Competition rank of one user's lifetime XP (ties share a rank).

    Rank is 1 + the number of users whose total XP is strictly greater, which
    is exactly the rank the ordered leaderboard shows for that XP value.
    Returns None when the user has no XP (not ranked). Only counts users —
    no other user's identity or data is read or returned.
    """
    db = get_db()
    row = db.execute(
        "SELECT COALESCE(SUM(xp), 0) AS total FROM xp_ledger WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    total = int(row["total"])
    if total <= 0:
        return None
    ahead = db.execute(
        "SELECT COUNT(*) AS ahead FROM ("
        "  SELECT l.user_id FROM xp_ledger l "
        "  GROUP BY l.user_id HAVING SUM(l.xp) > ?"
        ")",
        (total,),
    ).fetchone()
    return int(ahead["ahead"]) + 1


def leaderboard_participant_count():
    """Number of users currently ranked (those with total XP greater than 0)."""
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) AS total FROM ("
        "  SELECT l.user_id FROM xp_ledger l "
        "  GROUP BY l.user_id HAVING SUM(l.xp) > 0"
        ")"
    ).fetchone()
    return int(row["total"])
