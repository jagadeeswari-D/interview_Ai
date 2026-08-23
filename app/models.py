"""Data access layer for Users, Profiles, and practice/interview records.

Parameterized queries only — never string-built SQL.
"""

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

def create_practice_interview(user_id, role, difficulty, topic):
    """Open a Smart Practice session for one topic."""
    db = get_db()
    cur = db.execute(
        "INSERT INTO interviews (user_id, mode, role, difficulty, type) "
        "VALUES (?, 'practice', ?, ?, ?)",
        (user_id, role, difficulty, topic),
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
        "i.status AS interview_status "
        "FROM questions q JOIN interviews i ON i.id = q.interview_id "
        "WHERE q.id = ?",
        (question_id,),
    ).fetchone()


def save_answer(question_id, user_answer, score, scores,
                feedback, missing_points, model_answer):
    """Store the evaluation result for a question's single answer.

    `scores` (five-dimension dict) and `missing_points` are stored as JSON.
    """
    db = get_db()
    db.execute(
        "INSERT INTO answers "
        "(question_id, user_answer, score, scores, feedback, missing_points, model_answer) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
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
                          question_limit, duration_minutes):
    """Open a timed Real Interview session (mode='real')."""
    db = get_db()
    cur = db.execute(
        "INSERT INTO interviews "
        "(user_id, mode, role, difficulty, type, question_limit, duration_minutes) "
        "VALUES (?, 'real', ?, ?, ?, ?, ?)",
        (
            user_id,
            role,
            difficulty,
            interview_type,
            int(question_limit),
            int(duration_minutes),
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


def list_completed_interviews(user_id, mode=None):
    """Finished sessions for a user, newest first, with graded-answer counts.

    `mode` optionally filters to 'practice' or 'real'; anything else returns
    both modes.
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
