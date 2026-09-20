"""Settings page (product preferences + account).

Appearance theme, interview preferences and notification prefs are stored as
one JSON blob in user_settings and returned on GET so the page reflects what
is actually persisted. Theme itself is applied by the existing client theme
system (localStorage `interviewiq-theme`); the stored `theme` preference here
mirrors that choice so it survives across devices and can be re-applied.

Account: the change-password action reuses the existing Werkzeug scrypt
hashing. No destructive delete button exists because account deletion is not
a feature of this product.
"""

import json

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .auth import login_required
from .models import (
    DEFAULT_SETTINGS,
    get_user_settings,
    set_user_settings,
    update_password_hash,
)
from .validation import MIN_PASSWORD_LENGTH, password_strength_error

settings_bp = Blueprint("settings", __name__)

ALLOWED_THEMES = {"dark", "light", "system"}
ALLOWED_MODES = {"real", "practice"}
ALLOWED_DIFFICULTY = {"easy", "medium", "hard"}
ALLOWED_DURATIONS = {"15", "30", "45", "60"}
ALLOWED_FOCUS = {
    "communication",
    "technical",
    "problem_solving",
    "confidence",
    "time_management",
}


@settings_bp.route("/settings", methods=["GET"])
@login_required
def view():
    settings = get_user_settings(g.user["id"])
    return render_template(
        "settings.html",
        active_page="settings",
        user_name=g.user["name"],
        email=g.user["email"],
        user_initial=(g.user["name"][:1] or "U").upper(),
        settings=settings,
        min_password_length=MIN_PASSWORD_LENGTH,
    )


@settings_bp.route("/settings/preferences", methods=["POST"])
@login_required
def save_preferences():
    """Merge validated preference changes into the stored settings blob."""
    current = get_user_settings(g.user["id"])
    form = request.form

    theme = form.get("theme")
    if theme in ALLOWED_THEMES:
        current["theme"] = theme

    mode = form.get("preferred_mode", "real")
    if mode in ALLOWED_MODES:
        current["preferred_mode"] = mode

    difficulty = form.get("difficulty", "medium")
    if difficulty in ALLOWED_DIFFICULTY:
        current["difficulty"] = difficulty

    duration = form.get("duration", "30")
    if duration in ALLOWED_DURATIONS:
        current["duration"] = duration

    focus_areas = [
        item for item in request.form.getlist("focus_areas") if item in ALLOWED_FOCUS
    ]
    current["focus_areas"] = focus_areas

    notifications = current.get("notifications", {})
    for key in ("interview_reminders", "progress_updates", "practice_reminders"):
        notifications[key] = bool(form.get(key))

    set_user_settings(g.user["id"], current)
    flash("Preferences saved.", "success")
    return redirect(url_for("settings.view"))


@settings_bp.route("/settings/password", methods=["POST"])
@login_required
def change_password():
    current = request.form.get("current_password") or ""
    new_password = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""

    if not check_password_hash(g.user["password_hash"], current):
        flash("Your current password is incorrect.", "error")
        return redirect(url_for("settings.view", section="account"))

    strength_error = password_strength_error(new_password)
    if strength_error:
        flash(strength_error, "error")
        return redirect(url_for("settings.view", section="account"))

    if new_password != confirm:
        flash("New passwords do not match.", "error")
        return redirect(url_for("settings.view", section="account"))

    update_password_hash(g.user["id"], generate_password_hash(new_password, method="scrypt"))
    flash("Password updated.", "success")
    return redirect(url_for("settings.view", section="account"))
