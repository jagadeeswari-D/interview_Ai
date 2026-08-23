"""Application configuration, loaded from environment variables.

Secrets are never hardcoded here. Everything configurable is read from the
environment (or an optional .env file loaded by run.py / the Flask CLI).
"""

import os
import secrets


class Config:
    # Fall back to a randomly generated key so the app is always runnable in
    # development. In production, always set SECRET_KEY in the environment.
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

    # Left as None here; resolved to the Flask instance folder at app creation.
    DATABASE_PATH = os.environ.get("DATABASE_PATH")

    # Server-side session settings.
    SESSION_COOKIE_NAME = os.environ.get("SESSION_COOKIE_NAME", "interviewiq_session")
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
    SESSION_IDLE_TIMEOUT_MINUTES = int(
        os.environ.get("SESSION_IDLE_TIMEOUT_MINUTES", "30")
    )

    ENV = os.environ.get("FLASK_ENV", "development")
    DEBUG = ENV == "development"
    TESTING = ENV == "testing"

    # ---- Deployment hardening (Stage 6, blueprint Section J/K.12) ----
    # Cap request bodies so oversized uploads are rejected by Flask itself.
    # Slightly above the per-resume cap below to leave room for form fields.
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH_BYTES", str(8 * 1024 * 1024)))

    # Resume uploads (blueprint Section J: PDF-only, ~5MB cap, stored outside
    # the web root). The directory is resolved relative to the instance path
    # at app startup when the value is not absolute.
    RESUME_UPLOAD_DIR = os.environ.get("RESUME_UPLOAD_DIR") or ""
    RESUME_MAX_BYTES = int(os.environ.get("RESUME_MAX_BYTES", str(5 * 1024 * 1024)))

    # Rate limiting (blueprint Section J concrete targets):
    #   auth endpoints ~5 requests/min/IP, Gemini-backed ~20 req/min/user.
    # Format "N per window_seconds". The limiter (app/ratelimit.py) keeps
    # counters in process memory: appropriate for this single-instance
    # Flask+SQLite deployment; it does NOT scale across multiple processes
    # and resets on restart (documented limitation).
    RATELIMIT_AUTH_LIMIT = int(os.environ.get("RATELIMIT_AUTH_LIMIT", "5"))
    RATELIMIT_AUTH_WINDOW = int(os.environ.get("RATELIMIT_AUTH_WINDOW", "60"))
    RATELIMIT_GEMINI_LIMIT = int(os.environ.get("RATELIMIT_GEMINI_LIMIT", "20"))
    RATELIMIT_GEMINI_WINDOW = int(os.environ.get("RATELIMIT_GEMINI_WINDOW", "60"))

    # ---- Google Gemini API (Stage 2: integration foundation) ----
    # The key is read from the environment only and stays server-side: it is
    # never rendered into templates, sent to the browser, committed to Git,
    # or persisted to SQLite. If unset, the app still boots and AI features
    # raise GeminiConfigError instead of making a real request.
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    GEMINI_BASE_URL = os.environ.get(
        "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
    )
    GEMINI_TIMEOUT_SECONDS = int(os.environ.get("GEMINI_TIMEOUT_SECONDS", "30"))
    # Blueprint Section G: malformed responses trigger a single retry.
    GEMINI_MAX_RETRIES = int(os.environ.get("GEMINI_MAX_RETRIES", "1"))
