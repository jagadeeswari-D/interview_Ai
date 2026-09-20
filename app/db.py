"""SQLite database access.

One connection per request (stored on Flask's `g`), parameterized queries
only, foreign keys enabled. The schema in schema.sql is applied idempotently
on startup so the app is always runnable.
"""

import os
import sqlite3

from flask import current_app, g

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def get_db():
    """Return a request-scoped SQLite connection."""
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE_PATH"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    """Close the request-scoped connection at teardown."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    """Create tables if they do not already exist (idempotent)."""
    with app.app_context():
        db = get_db()
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            db.executescript(f.read())
        _migrate_schema(db)
        db.commit()


def _migrate_schema(db):
    """Additive migrations for databases created before a stage's schema change.

    CREATE TABLE IF NOT EXISTS never alters existing tables, so new columns
    are added here when missing. Only constant column definitions are used.
    """
    columns = {row[1] for row in db.execute("PRAGMA table_info(interviews)")}
    additions = (
        ("question_limit", "INTEGER NOT NULL DEFAULT 0"),
        ("duration_minutes", "INTEGER NOT NULL DEFAULT 0"),
        ("company_key", "TEXT"),
    )
    for name, declaration in additions:
        if name not in columns:
            db.execute(
                f"ALTER TABLE interviews ADD COLUMN {name} {declaration}"
            )
