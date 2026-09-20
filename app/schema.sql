-- InterviewIQ SQLite schema (Stage 1: Foundation).
-- Idempotent: safe to run on every startup.

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL CHECK (length(trim(name)) > 0),
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT '',
    skills      TEXT NOT NULL DEFAULT '[]',
    resume_path TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Server-side session store (Stage 1). The client cookie holds only a random
-- opaque token; the session payload lives here in SQLite.
CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);

-- Practice & interview data (Stage 2, blueprint Section F).

CREATE TABLE IF NOT EXISTS interviews (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mode          TEXT NOT NULL CHECK (mode IN ('practice', 'real')),
    role          TEXT NOT NULL DEFAULT '',
    difficulty    TEXT NOT NULL DEFAULT '',
    type          TEXT NOT NULL DEFAULT '',   -- practice mode: holds the topic
    date          TEXT NOT NULL DEFAULT (datetime('now')),
    overall_score REAL,                       -- NULL until an answer is evaluated
    status        TEXT NOT NULL DEFAULT 'in_progress'
                  CHECK (status IN ('in_progress', 'completed')),
    -- Real Interview Mode (Stage 3): server-enforced timer and question
    -- budget. Practice rows keep the defaults (0). The deadline for a real
    -- interview is `date` + duration_minutes; both values are needed on
    -- every request so expiry can be enforced server-side (blueprint B.5/H.6)
    -- even after a page reload.
    question_limit   INTEGER NOT NULL DEFAULT 0,
    duration_minutes INTEGER NOT NULL DEFAULT 0,
    -- Company Presets (Phase 10 / Stage 1): allowlisted preset key selected
    -- by the user, or NULL for General/No Company (identical to pre-Phase-10
    -- behavior). Only keys validated against the static server allowlist are
    -- ever stored here; it is never free text.
    company_key       TEXT
);

CREATE INDEX IF NOT EXISTS idx_interviews_user ON interviews(user_id);

CREATE TABLE IF NOT EXISTS questions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    interview_id      INTEGER NOT NULL REFERENCES interviews(id) ON DELETE CASCADE,
    question          TEXT NOT NULL,
    question_type     TEXT NOT NULL DEFAULT '',
    sequence_order    INTEGER NOT NULL DEFAULT 1,
    -- JSON array of concepts a strong answer should cover (from the
    -- generate_question contract); drives hints and evaluation.
    expected_concepts TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_questions_interview ON questions(interview_id);

CREATE TABLE IF NOT EXISTS answers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id    INTEGER NOT NULL UNIQUE REFERENCES questions(id) ON DELETE CASCADE,
    user_answer    TEXT NOT NULL,
    score          REAL,                      -- mean of the five dimension scores
    scores         TEXT NOT NULL DEFAULT '{}', -- JSON {five dimensions 0-100}
    feedback       TEXT NOT NULL DEFAULT '',
    missing_points TEXT NOT NULL DEFAULT '[]', -- JSON array
    model_answer   TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_answers_question ON answers(question_id);

CREATE TABLE IF NOT EXISTS performance (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    skill        TEXT NOT NULL,
    score        REAL NOT NULL,
    date         TEXT NOT NULL DEFAULT (datetime('now')),
    interview_id INTEGER REFERENCES interviews(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_performance_user ON performance(user_id);

-- Weakness detection & Interview Memory (Stage 4, blueprint Section F).
-- One row per user+skill; `frequency` accumulates weak occurrences across
-- sessions while `interview_count` increments at most once per finished
-- interview, so `interview_count >= 2` is the cross-session repeated-mistake
-- signal (blueprint C: repeated-mistake detection).
CREATE TABLE IF NOT EXISTS weaknesses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    skill             TEXT NOT NULL,
    frequency         INTEGER NOT NULL DEFAULT 0,
    interview_count   INTEGER NOT NULL DEFAULT 0,
    last_interview_id INTEGER REFERENCES interviews(id) ON DELETE SET NULL,
    updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, skill)
);

CREATE INDEX IF NOT EXISTS idx_weaknesses_user ON weaknesses(user_id);

-- Derived summary cache (Section F note): recomputed from Weaknesses +
-- Performance after every finished interview — never a source of truth.
-- Topics are JSON arrays of skill labels.
CREATE TABLE IF NOT EXISTS interview_memory (
    user_id           INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    strong_topics     TEXT NOT NULL DEFAULT '[]',
    weak_topics       TEXT NOT NULL DEFAULT '[]',
    repeated_mistakes TEXT NOT NULL DEFAULT '[]',
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Personalized Learning Roadmap (Stage 5 Item 10, blueprint Sections F/G/K.10).
-- One row per planned day of the day-by-day plan. `skill` records the
-- weakness snapshot that produced the plan (the weak_skills[] input of the
-- Section G generate_roadmap contract — identical on every row of one
-- generation). `status` drives the progress checkboxes; regeneration
-- replaces all of a user's rows in a single transaction.
CREATE TABLE IF NOT EXISTS roadmaps (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    skill          TEXT NOT NULL DEFAULT '',
    day_number     INTEGER NOT NULL CHECK (day_number >= 1),
    topic          TEXT NOT NULL,
    practice_focus TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'completed')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, day_number)
);

CREATE INDEX IF NOT EXISTS idx_roadmaps_user ON roadmaps(user_id);

-- User preferences (Settings page). Single JSON blob per user keeping the
-- per-user preference surface small and easy to extend without migrations.
CREATE TABLE IF NOT EXISTS user_settings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    settings   TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Question Bookmarks (Stage 8, Phase 8). One row per user+question;
-- UNIQUE(user_id, question_id) prevents duplicates at the DB level and backs
-- every user-scoped lookup. CASCADE keeps bookmarks deleted with the user or
-- the question. Ownership (question belongs to the signed-in user's own
-- interview) is enforced in the application layer, matching Replay guards.
CREATE TABLE IF NOT EXISTS bookmarks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, question_id)
);

-- Personal Notes (Stage 8, Phase 8). A private user-owned note attached to
-- one question. One row per user+question: UNIQUE(user_id, question_id)
-- prevents duplicates and backs lookups. Content is never exposed outside the
-- question's own Replay walkthrough; ownership (interview+question) is
-- enforced in the application layer exactly like bookmarks and Replay.
CREATE TABLE IF NOT EXISTS personal_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    content     TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, question_id)
);

-- Daily Challenge (Phase 9 / Stage 1). One deterministic challenge per user
-- per calendar day. UNIQUE(user_id, challenge_date) is the one-per-day /
-- one-per-user guarantee and backs every lookup; the question is written by
-- the server the first time the user opens the page, so a refresh returns the
-- same challenge and never creates a duplicate. `status`, `answer` and
-- `completed_at` are only ever changed by the CSRF-protected answer route;
-- ownership follows the users row (CASCADE keeps rows with their user).
CREATE TABLE IF NOT EXISTS daily_challenges (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    challenge_date    TEXT NOT NULL,                 -- YYYY-MM-DD (local day)
    question          TEXT NOT NULL,
    question_type     TEXT NOT NULL DEFAULT '',
    expected_concepts TEXT NOT NULL DEFAULT '[]',    -- JSON array of concepts
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'completed')),
    answer            TEXT,                          -- NULL until submitted
    completed_at      TEXT,
    UNIQUE (user_id, challenge_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_challenges_user ON daily_challenges(user_id);

-- Achievements / Badges (Phase 9 / Stage 3). One row per user+achievement
-- unlock. Achievement definitions live in code (app/achievements.py);
-- this table records the server-authoritative moment an achievement was
-- unlocked (never a value the browser can write). UNIQUE(user_id,
-- achievement_key) plus INSERT OR IGNORE keep unlocks idempotent and
-- monotonic; CASCADE keeps badges with their user.
CREATE TABLE IF NOT EXISTS user_achievements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    achievement_key TEXT NOT NULL,
    unlocked_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, achievement_key)
);

CREATE INDEX IF NOT EXISTS idx_user_achievements_user ON user_achievements(user_id);

-- XP ledger (Stage 14). One append-only row per XP award. `source` names the
-- completion system ('practice_answer' | 'real_interview' |
-- 'daily_challenge' | 'achievement') and `event_key` is that system's
-- authoritative unique id (question id / interview id / challenge id /
-- achievement key). UNIQUE(user_id, source, event_key) is the
-- database-enforced idempotency boundary: a duplicate, retried, or raced
-- award of the same logical event is an INSERT OR IGNORE no-op, so XP can
-- never be granted twice. `xp` is a positive server-derived amount; the
-- browser never supplies values here.
CREATE TABLE IF NOT EXISTS xp_ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source     TEXT NOT NULL,
    event_key  TEXT NOT NULL,
    xp         INTEGER NOT NULL CHECK (xp > 0),
    awarded_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, source, event_key)
);

CREATE INDEX IF NOT EXISTS idx_xp_ledger_user ON xp_ledger(user_id);
