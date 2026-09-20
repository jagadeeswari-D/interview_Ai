"""QA fixture: seed a realistic InterviewIQ account for browser QA.

The account is created through the app's REAL /register HTTP flow (so the
user row, profile and password hash are exactly what production creates),
then realistic interview/roadmap/analytics data is inserted straight into
the SQLite DB using the production schema shapes.

This is TEMPORARY test data only — it never touches application code.

Usage (from the interview_Ai project dir, with the dev server running):
    .venv\\Scripts\\python.exe qa\\seed_qa.py
"""

import json
import os
import re
import sqlite3
import datetime as dt

import requests

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_DIR, "instance", "interviewiq.db")
BASE_URL = os.environ.get("QA_BASE_URL", "http://127.0.0.1:5000")

EMAIL = "qa@example.com"
PASSWORD = "Qa-password-1"
NAME = "QA Analyst"

_DIMS = ["technical_accuracy", "relevance", "completeness", "clarity", "communication"]

_SKILLS = [
    "Python", "SQL", "Data structures & algorithms", "System design",
    "Testing & debugging", "Communication", "REST APIs", "Frontend fundamentals",
]


def register_user():
    """Create the account through the real HTTP registration flow."""
    sess = requests.Session()
    html = sess.get(f"{BASE_URL}/register", timeout=15).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    token = m.group(1) if m else ""
    resp = sess.post(
        f"{BASE_URL}/register",
        data={
            "csrf_token": token,
            "name": NAME,
            "email": EMAIL,
            "password": PASSWORD,
            "confirm": PASSWORD,
        },
        allow_redirects=False,
        timeout=15,
    )
    # 302 -> dashboard on success; 200 -> re-rendered form with flash (failure)
    return resp.status_code == 302


def _dims(*values):
    return {k: v for k, v in zip(_DIMS, values)}


def _rows_for_day(base, day_offset, n):
    date = (base + dt.timedelta(days=day_offset)).isoformat(sep=" ", timespec="seconds")
    sessions = []
    for i in range(n):
        mode = "real" if (i % 2 == 0) else "practice"
        role = "Software Engineer" if mode == "real" else ""
        topic = "Python" if mode == "practice" else ""
        qcount = 3 if mode == "real" else 2
        overall = round(52 + (i % 5) * 9 + (day_offset % 3) * 4, 1)
        sessions.append((date, mode, role, topic, qcount, overall))
    return sessions


def main():
    now = dt.datetime.now()

    # ---- Join the account to the app's own DB (idempotent) -----------------
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    # Clean orphaned rows (e.g. from an earlier aborted seed) and prior QA data.
    for table in ("performance", "weaknesses", "interview_memory", "roadmaps",
                  "user_settings", "interviews"):
        cur.execute(f"DELETE FROM {table} WHERE user_id NOT IN (SELECT id FROM users)")
    cur.execute("DELETE FROM answers WHERE question_id IN "
                "(SELECT id FROM questions WHERE interview_id IN "
                "(SELECT id FROM interviews WHERE user_id NOT IN (SELECT id FROM users)))")
    cur.execute("DELETE FROM questions WHERE interview_id IN "
                "(SELECT id FROM interviews WHERE user_id NOT IN (SELECT id FROM users))")
    conn.commit()

    row = cur.execute("SELECT id FROM users WHERE email = ?", (EMAIL,)).fetchone()
    if row:
        uid = row[0]
        for table in ("performance", "weaknesses", "interview_memory",
                      "roadmaps", "user_settings", "interviews"):
            cur.execute(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
        cur.execute("DELETE FROM answers WHERE question_id IN "
                    "(SELECT id FROM questions WHERE interview_id IN "
                    "(SELECT id FROM interviews WHERE user_id = ?))", (uid,))
        cur.execute("DELETE FROM questions WHERE interview_id IN "
                    "(SELECT id FROM interviews WHERE user_id = ?)", (uid,))
        cur.execute("DELETE FROM profiles WHERE user_id = ?", (uid,))
        cur.execute("DELETE FROM users WHERE id = ?", (uid,))
        conn.commit()
    else:
        if not register_user():
            body = requests.get(f"{BASE_URL}/register", timeout=15).text
            raise SystemExit("HTTP registration failed — cannot proceed.")
        row = cur.execute("SELECT id FROM users WHERE email = ?", (EMAIL,)).fetchone()
        uid = row[0]
    print(f"[seed] QA user ready id={uid} ({EMAIL})")

    # ---- Profile / preferences ---------------------------------------------
    cur.execute(
        "UPDATE profiles SET role = ?, skills = ?, updated_at = datetime('now') "
        "WHERE user_id = ?",
        ("Software Engineer", json.dumps(_SKILLS[:5]), uid),
    )
    cur.execute(
        "INSERT INTO user_settings (user_id, settings) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET settings = excluded.settings",
        (uid, json.dumps({
            "theme": "system",
            "preferred_mode": "real",
            "difficulty": "medium",
            "duration": "30",
            "focus_areas": ["Python", "System design"],
            "notifications": {
                "interview_reminders": True,
                "progress_updates": True,
                "practice_reminders": True,
            },
        })),
    )

    # ---- Interviews, questions, graded answers ------------------------------
    interview_id = None
    skill_index = 0
    base = now - dt.timedelta(days=16)
    for day_offset in range(8):
        for date, mode, role, topic, qcount, overall in _rows_for_day(base, day_offset, 2):
            if mode == "real":
                cur.execute(
                    "INSERT INTO interviews (user_id, mode, role, difficulty, type, "
                    "date, overall_score, status, question_limit, duration_minutes) "
                    "VALUES (?, ?, ?, 'medium', 'Technical', ?, ?, 'completed', 5, 30)",
                    (uid, mode, role, date, overall),
                )
            else:
                cur.execute(
                    "INSERT INTO interviews (user_id, mode, role, difficulty, type, "
                    "date, overall_score, status) "
                    "VALUES (?, ?, '', 'medium', ?, ?, ?, 'completed')",
                    (uid, mode, topic, date, overall),
                )
            interview_id = cur.lastrowid
            for q in range(qcount):
                skill = _SKILLS[(skill_index + q) % len(_SKILLS)]
                skill_index += 1
                concept = skill if skill != "REST APIs" else "API design"
                cur.execute(
                    "INSERT INTO questions (interview_id, question, question_type, "
                    "sequence_order, expected_concepts) "
                    "VALUES (?, ?, 'Technical', ?, ?)",
                    (interview_id,
                     f"Explain how you would approach {concept.lower()} in a real product.",
                     q + 1, json.dumps([f"cover {concept.lower()} trade-offs"]),
                     ),
                )
                qid = cur.lastrowid
                d = [round(max(30, min(98, overall - 10 + (i % 4) * 6)), 1)
                     for i in range(5)]
                score = round(sum(d) / 5, 1)
                cur.execute(
                    "INSERT INTO answers (question_id, user_answer, score, scores, "
                    "feedback, missing_points, model_answer, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (qid,
                     "I would break the problem into small steps, sketch a clean "
                     "design, then implement and test it. " + ("SWE ROLE " * 8),
                     score, json.dumps(_dims(*d)),
                     "Strong structure. Add a concrete example of handling edge cases.",
                     json.dumps(["mention failure handling", "quantify impact"]),
                     "A model answer walking through the same scenario end to end.",
                     date,
                     ),
                )
                cur.execute(
                    "INSERT INTO performance (user_id, skill, score, date, interview_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (uid, skill, score, date, interview_id),
                )

    # ---- Weaknesses + interview memory --------------------------------------
    for skill, freq in [("System design", 4), ("Communication", 3),
                        ("Testing & debugging", 2)]:
        cur.execute(
            "INSERT INTO weaknesses (user_id, skill, frequency, interview_count, "
            "last_interview_id) VALUES (?, ?, ?, 2, ?) "
            "ON CONFLICT(user_id, skill) DO UPDATE SET "
            "frequency = excluded.frequency, interview_count = excluded.interview_count, "
            "last_interview_id = excluded.last_interview_id",
            (uid, skill, freq, interview_id),
        )
    cur.execute(
        "INSERT INTO interview_memory (user_id, strong_topics, weak_topics, "
        "repeated_mistakes) VALUES (?, ?, ?, ?)",
        (uid, json.dumps(["Python", "SQL"]),
         json.dumps(["System design", "Communication"]),
         json.dumps(["System design: repeated misses"])),
    )

    # ---- Learning roadmap (14 days, first 6 completed) -----------------------
    days = [
        ("System design", 1, "Foundations of scaling", "Read about horizontal vs vertical scaling"),
        ("System design", 2, "Load balancing deep dive", "Whiteboard a load-balanced service"),
        ("System design", 3, "Database sharding 101", "Shard the QA schema by user id"),
        ("System design", 4, "Caching strategies", "Redis caching for hot reads"),
        ("Communication", 5, "STAR answer technique", "Rewrite 3 answers in STAR form"),
        ("Communication", 6, "Clarity drills", "Record a 2-minute design pitch"),
        ("Testing & debugging", 7, "Test pyramid", "Write unit tests for a small module"),
        ("Testing & debugging", 8, "Root-cause debugging", "Debug a memory leak realistically"),
        ("Python", 9, "Python performance idioms", "Optimize a slow loop"),
        ("SQL", 10, "Index tuning", "Analyze a query plan"),
        ("REST APIs", 11, "API versioning", "Design a v2 endpoint"),
        ("Frontend fundamentals", 12, "Layout and a11y", "Recount CSS layout fundamentals"),
        ("System design", 13, "Design a chat service", "Podcast-style design review"),
        ("Communication", 14, "Behavioral storytelling", "Share a postmortem story"),
    ]
    for skill, day, topic, focus in days:
        cur.execute(
            "INSERT INTO roadmaps (user_id, skill, day_number, topic, practice_focus, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uid, skill, day, topic, focus,
             "completed" if day <= 6 else "pending"),
        )

    # ---- In-progress sessions for the live interview page --------------------
    cur.execute(
        "INSERT INTO interviews (user_id, mode, role, difficulty, type, date, status) "
        "VALUES (?, 'practice', 'Backend Engineer', 'easy', 'Python', ?, 'in_progress')",
        (uid, now.isoformat(sep=" ", timespec="seconds")),
    )
    live_practice_id = cur.lastrowid
    cur.execute(
        "INSERT INTO questions (interview_id, question, question_type, sequence_order, "
        "expected_concepts) VALUES (?, ?, 'Technical', 1, ?)",
        (live_practice_id,
         "Write a Python function that deduplicates a list while preserving order.",
         json.dumps(["list comprehension", "set membership"])),
    )
    cur.execute(
        "INSERT INTO interviews (user_id, mode, role, difficulty, type, date, "
        "status, question_limit, duration_minutes) "
        "VALUES (?, 'real', 'Software Engineer', 'medium', 'Technical', ?, "
        "'in_progress', 5, 30)",
        (uid, now.isoformat(sep=" ", timespec="seconds")),
    )
    live_real_id = cur.lastrowid
    cur.execute(
        "INSERT INTO questions (interview_id, question, question_type, sequence_order, "
        "expected_concepts) VALUES (?, ?, 'Technical', 1, ?)",
        (live_real_id,
         "Walk me through how you would scale a message queue from one queue to a fleet.",
         json.dumps(["partitions", "consumer groups"])),
    )

    conn.commit()
    conn.close()
    print(f"[seed] Done: {EMAIL} user={uid}, live practice={live_practice_id}, "
          f"live real={live_real_id}.")


if __name__ == "__main__":
    main()