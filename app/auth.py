"""Authentication blueprint: register, login, logout.

Passwords are hashed with Werkzeug's scrypt (a memory-hard KDF built into
Python's hashlib) and are never stored or logged in plaintext. Authenticated
state lives in the server-side session store.
"""

from functools import wraps

from flask import (
    Blueprint,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from .models import create_profile, create_user, get_user_by_email
from .ratelimit import check_auth_limit
from .validation import normalize_email, validate_login, validate_registration

auth_bp = Blueprint("auth", __name__)


@auth_bp.before_request
def _rate_limit_auth_writes():
    """Blueprint Section J: ~5 auth POSTs/min/IP (Stage 6 hardening).

    Only login/register-style writes are counted — logging out must never
    be rate limited.
    """
    if request.endpoint == "auth.logout":
        return
    check_auth_limit()


def login_required(view):
    """Redirect unauthenticated users to the login page."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            flash("Please log in to continue.", "info")
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def _safe_next_url(target):
    """Only allow relative paths for the `next` redirect target."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if g.get("user"):
        return redirect(url_for("main.dashboard"))

    form = {"name": "", "email": "", "password": "", "confirm": ""}
    if request.method == "POST":
        form = {
            "name": (request.form.get("name") or "").strip(),
            "email": normalize_email(request.form.get("email")),
            "password": request.form.get("password") or "",
            "confirm": request.form.get("confirm") or "",
        }
        errors = validate_registration(
            form["name"], form["email"], form["password"], form["confirm"]
        )

        if get_user_by_email(form["email"]) is not None:
            errors.append(("email", "An account with that email already exists."))

        if errors:
            for field, message in errors:
                flash(message, "error")
        else:
            password_hash = generate_password_hash(form["password"], method="scrypt")
            user = create_user(form["name"], form["email"], password_hash)
            create_profile(user["id"])

            session.clear()
            session["user_id"] = user["id"]
            flash(f"Welcome to InterviewIQ, {user['name']}!", "success")
            return redirect(url_for("main.dashboard"))

    return render_template("auth/register.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user"):
        return redirect(url_for("main.dashboard"))

    form = {"email": "", "password": ""}
    if request.method == "POST":
        form = {
            "email": normalize_email(request.form.get("email")),
            "password": request.form.get("password") or "",
        }
        errors = validate_login(form["email"], form["password"])
        user = None if errors else get_user_by_email(form["email"])

        if errors:
            for field, message in errors:
                flash(message, "error")
        elif user is None or not check_password_hash(user["password_hash"], form["password"]):
            flash("Invalid email or password.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            flash(f"Welcome back, {user['name']}!", "success")
            next_url = _safe_next_url(request.args.get("next"))
            return redirect(next_url or url_for("main.dashboard"))

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.landing"))
