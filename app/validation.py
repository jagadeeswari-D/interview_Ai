"""Server-side input validation for forms and API inputs.

All user input is validated and normalized here before it reaches the
database. Auth-specific rules live with the auth blueprint; shared rules live
in models.py where appropriate.
"""

import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Minimum password requirements (Stage 1 baseline; can be raised later).
MIN_PASSWORD_LENGTH = 8


def normalize_email(email):
    """Trim and lowercase an email address for storage/lookup."""
    return (email or "").strip().lower()


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

    if not password:
        errors.append(("password", "Password is required."))
    elif len(password) < MIN_PASSWORD_LENGTH:
        errors.append(
            (
                "password",
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
            )
        )
    elif not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        errors.append(("password", "Password must contain at least one letter and one number."))

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
