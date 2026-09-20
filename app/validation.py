"""Server-side input validation for forms and API inputs.

All user input is validated and normalized here before it reaches the
database. Auth-specific rules live with the auth blueprint; shared rules live
in models.py where appropriate.
"""

import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Minimum password requirements (Stage 1 baseline; can be raised later).
MIN_PASSWORD_LENGTH = 8

# Profile page rules (blueprint Sections B.2/E): personal info, target role,
# skills. Length caps mirror the registration name cap; the skills cap keeps
# the stored JSON list (and any future prompt use) bounded.
PROFILE_ROLE_MAX_LENGTH = 80
MAX_PROFILE_SKILLS = 12
SKILL_MAX_LENGTH = 50


def normalize_email(email):
    """Trim and lowercase an email address for storage/lookup."""
    return (email or "").strip().lower()


def validate_profile_name(name):
    """Return an error message for the profile name field, or None."""
    name = (name or "").strip()
    if not name:
        return "Name is required."
    if len(name) < 2:
        return "Name must be at least 2 characters."
    if len(name) > 80:
        return "Name must be 80 characters or fewer."
    return None


def validate_profile_role(role):
    """Return an error message for the target role field, or None.

    The role is optional: an empty value is stored as '' and Smart Practice /
    Real Interview fall back to their default role label, matching the
    pre-profile behavior of _role_for_user().
    """
    if len((role or "").strip()) > PROFILE_ROLE_MAX_LENGTH:
        return (
            f"Target role must be {PROFILE_ROLE_MAX_LENGTH} characters "
            f"or fewer."
        )
    return None


def parse_skills(skills_text):
    """Parse a comma/newline-separated skills string into a clean list.

    Entries are trimmed, empties dropped, duplicates removed
    case-insensitively (the first spelling wins). Returns
    (skills, error_message) so callers can reject oversized input instead of
    silently truncating it.
    """
    seen = set()
    skills = []
    for raw in re.split(r"[,\n]", skills_text or ""):
        skill = raw.strip()
        if not skill:
            continue
        if len(skill) > SKILL_MAX_LENGTH:
            return [], (
                f"Each skill must be {SKILL_MAX_LENGTH} characters or fewer."
            )
        key = skill.lower()
        if key in seen:
            continue
        seen.add(key)
        skills.append(skill)
    if len(skills) > MAX_PROFILE_SKILLS:
        return [], (
            f"List at most {MAX_PROFILE_SKILLS} skills "
            f"(you entered {len(skills)})."
        )
    return skills, None


def password_strength_error(password):
    """Return an error message when a password is too weak, else None.

    The single source of truth for the strength rule (minimum length plus
    at least one letter and one number), used by registration and by the
    Settings → change-password flow so a user can never downgrade to a
    weaker password than registration would allow.
    """
    password = password or ""
    if not password:
        return "Password is required."
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Password must contain at least one letter and one number."
    return None


def validate_registration(name, email, password, confirm):
    """Return a list of (field, message) errors for a registration form."""
    errors = []
    name = (name or "").strip()

    if not name:
        errors.append(("name", "Name is required."))
    elif len(name) < 2:
        errors.append(("name", "Name must be at least 2 characters."))
    elif len(name) > 80:
        errors.append(("name", "Name must be 80 characters or fewer."))

    if not normalize_email(email):
        errors.append(("email", "Email is required."))
    elif not EMAIL_RE.match(normalize_email(email)):
        errors.append(("email", "Enter a valid email address."))

    strength_error = password_strength_error(password)
    if strength_error:
        errors.append(("password", strength_error))

    if confirm is None or password != confirm:
        errors.append(("confirm", "Passwords do not match."))

    return errors


def validate_login(email, password):
    """Return a list of (field, message) errors for a login form."""
    errors = []
    if not normalize_email(email):
        errors.append(("email", "Email is required."))
    if not password:
        errors.append(("password", "Password is required."))
    return errors
