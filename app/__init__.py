"""InterviewIQ application factory."""

import os
import secrets

from flask import Flask, g, render_template, session

from .config import Config
from .csrf import init_csrf
from .db import close_db, init_db
from .models import get_user_by_id
from .session_store import SQLiteSessionInterface


def create_app(config_object=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object or Config)

    if not os.environ.get("SECRET_KEY"):
        # Ephemeral development key: sessions reset on restart, never persists.
        app.config["SECRET_KEY"] = secrets.token_hex(32)

    if not app.config.get("DATABASE_PATH"):
        app.config["DATABASE_PATH"] = os.path.join(app.instance_path, "interviewiq.db")
    os.makedirs(os.path.dirname(app.config["DATABASE_PATH"]), exist_ok=True)

    # Resume uploads live outside the web root (blueprint Section J):
    # default to <instance>/resumes unless an absolute path is configured.
    upload_dir = app.config.get("RESUME_UPLOAD_DIR") or "resumes"
    if not os.path.isabs(upload_dir):
        upload_dir = os.path.join(app.instance_path, upload_dir)
    app.config["RESUME_UPLOAD_DIR"] = os.path.abspath(upload_dir)
    os.makedirs(app.config["RESUME_UPLOAD_DIR"], exist_ok=True)

    # Server-side session store (SQLite-backed).
    app.session_interface = SQLiteSessionInterface()

    # Database.
    app.teardown_appcontext(close_db)
    init_db(app)

    # Security.
    init_csrf(app)
    _init_security_headers(app)

    # AI layer: Gemini integration foundation. Boots fine without an API key;
    # `available` is False and calls raise GeminiConfigError until the key is set.
    from .ai.gemini import create_gemini_service

    app.extensions["gemini"] = create_gemini_service(app)

    # Blueprints.
    from .ai.routes import ai_bp
    from .analytics import analytics_bp
    from .auth import auth_bp
    from .history import history_bp
    from .interview import interview_bp
    from .main import main_bp
    from .practice import practice_bp
    from .profile import profile_bp
    from .reports import reports_bp
    from .replay import replay_bp
    from .resume import resume_bp
    from .roadmap import roadmap_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(practice_bp)
    app.register_blueprint(interview_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(replay_bp)
    app.register_blueprint(roadmap_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(resume_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(profile_bp)

    # Load the authenticated user for every request.
    @app.before_request
    def load_user():
        user_id = session.get("user_id")
        g.user = get_user_by_id(user_id) if user_id else None

    _init_error_handlers(app)
    return app


def _init_security_headers(app):
    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self'; "
            "script-src 'self'; "
            "img-src 'self' data:; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        return response


def _init_error_handlers(app):
    @app.errorhandler(400)
    def bad_request(e):
        return (
            render_template(
                "error.html", code=400,
                message=getattr(e, "description", None) or "Bad request.",
            ),
            400,
        )

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403, message="Access denied."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, message="Page not found."), 404

    @app.errorhandler(413)
    def too_large(e):
        return (
            render_template(
                "error.html", code=413,
                message="That upload is too large.",
            ),
            413,
        )

    # Rate limiting (Stage 6): one generic, safe message — no internals.
    @app.errorhandler(429)
    def too_many_requests(e):
        return (
            render_template(
                "error.html", code=429,
                message="Too many requests. Please wait a minute and try again.",
            ),
            429,
        )

    @app.errorhandler(500)
    def internal_error(e):
        return render_template("error.html", code=500, message="Something went wrong."), 500
