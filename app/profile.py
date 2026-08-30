"""Profile page (blueprint Sections B.2/D/E).

Personal info (display name), target role and skills for the signed-in
user. The target role is stored on Profiles.role, which Smart Practice and
Real Interview already read through get_profile() when building question
prompts (_role_for_user) — saving the profile therefore personalizes both
modes with no AI-layer change. Skills are stored on Profiles.skills per
Section F; no Gemini call happens on save.

Ownership: routes only ever touch g.user's own rows — no user or profile id
is accepted from the client, matching dashboard/history behavior.
"""

import json

from flask import (
    Blueprint,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from .auth import login_required
from .models import (
    get_profile,
    set_profile_details,
    update_user_name,
)
from .validation import (
    MAX_PROFILE_SKILLS,
    SKILL_MAX_LENGTH,
    parse_skills,
    validate_profile_name,
    validate_profile_role,
)

profile_bp = Blueprint("profile", __name__)


@profile_bp.route("/profile", methods=["GET", "POST"])
@login_required
def view():
    profile = get_profile(g.user["id"])
    saved_skills = _decode_skills(profile)
    current_role = (profile["role"] if profile else "") or ""

    # Form values shown in the inputs: saved data on GET, submitted data on
    # a validation error so nothing the user typed is lost.
    form = {
        "name": g.user["name"],
        "role": current_role,
        "skills": ", ".join(saved_skills),
    }

    if request.method == "POST":
        form["name"] = (request.form.get("name") or "").strip()
        form["role"] = (request.form.get("role") or "").strip()
        skills, skills_error = parse_skills(request.form.get("skills") or "")

        errors = []
        for check, value in (
            (validate_profile_name, form["name"]),
            (validate_profile_role, form["role"]),
        ):
            message = check(value)
            if message:
                errors.append(message)
        if skills_error:
            errors.append(skills_error)

        if errors:
            for message in errors:
                flash(message, "error")
        else:
            # Only intentionally changed columns are written; everything
            # else on users/profiles (including resume_path) stays as-is.
            if form["name"] != g.user["name"]:
                update_user_name(g.user["id"], form["name"])
            if form["role"] != current_role or skills != saved_skills:
                set_profile_details(g.user["id"], form["role"], skills)
            flash("Profile updated.", "success")
            # Post/redirect/get: the next GET re-reads the saved row, so the
            # page always shows what is actually persisted.
            return redirect(url_for(".view"))

    return render_template(
        "profile.html",
        active_page="profile",
        form=form,
        email=g.user["email"],
        saved_skills=saved_skills,
        has_resume=_has_resume(profile),
        max_skills=MAX_PROFILE_SKILLS,
        skill_max_length=SKILL_MAX_LENGTH,
    )


def _decode_skills(profile):
    """The decoded skills list from a profiles row ([] when unset/broken)."""
    if profile is None:
        return []
    try:
        skills = json.loads(profile["skills"] or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(skills, list):
        return []
    return [str(skill) for skill in skills]


def _has_resume(profile):
    return bool((profile["resume_path"] if profile else "") or "")
