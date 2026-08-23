"""CSRF protection.

Every state-changing request (POST/PUT/PATCH/DELETE) must carry a token that
matches the one stored in the server-side session. A per-session token is
generated lazily and injected into templates via the `csrf_token` global.
"""

import hmac
import secrets

from flask import abort, request, session
from markupsafe import Markup

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def generate_csrf_token():
    """Generate (once per session) and return the CSRF token."""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]


def render_csrf_field():
    """Render a hidden CSRF input for use inside forms.

    The token is urlsafe base64 (A-Za-z0-9_-), safe to embed in an attribute.
    Registered as the `csrf_token` Jinja global: {{ csrf_token() }}
    """
    return Markup(
        '<input type="hidden" name="csrf_token" value="{0}">'.format(
            generate_csrf_token()
        )
    )


def validate_csrf():
    """Reject state-changing requests that lack a valid CSRF token."""
    if request.method in _SAFE_METHODS:
        return
    token = session.get("_csrf_token")
    submitted = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not token or not submitted or not hmac.compare_digest(token, submitted):
        abort(400, description="Your form session has expired. Please try again.")


def init_csrf(app):
    app.before_request(validate_csrf)
    app.jinja_env.globals["csrf_token"] = render_csrf_field
