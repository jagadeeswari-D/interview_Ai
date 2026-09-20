"""AI blueprint: backend health/test endpoint for the Gemini foundation.

GET /ai/health performs one minimal Gemini round-trip so operators can
confirm the configured key and model work end-to-end. It requires login
and is rate limited like every other Gemini-triggering route (each check
consumes quota) and never echoes the API key anywhere.
"""

from flask import Blueprint, current_app, jsonify

from ..auth import login_required
from ..ratelimit import check_gemini_limit
from .errors import GeminiConfigError, GeminiError, GeminiRateLimitError

ai_bp = Blueprint("ai", __name__)


@ai_bp.route("/ai/health")
@login_required
def gemini_health():
    """Verify the Gemini client can complete a simple API request."""
    check_gemini_limit()
    service = current_app.extensions["gemini"]
    if not service.available:
        return jsonify(
            status="unconfigured",
            error="GEMINI_API_KEY is not configured; AI features are disabled.",
        ), 503

    try:
        ok = service.ping()
    except GeminiConfigError as exc:
        return jsonify(status="unconfigured", error=str(exc)), 503
    except GeminiRateLimitError as exc:
        return jsonify(status="error", error=str(exc)), 429
    except GeminiError as exc:
        return jsonify(status="error", error=str(exc)), 502

    if not ok:
        return jsonify(
            status="error", error="Gemini responded with an unexpected payload."
        ), 502
    return jsonify(status="ok", model=service.model)
