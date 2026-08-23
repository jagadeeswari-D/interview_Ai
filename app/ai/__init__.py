"""AI layer — Gemini integration foundation (Stage 2).

Centralized access point for the Gemini service and its typed errors. Later
stages call `get_service()` from inside a request and use `generate(task, ...)`.
"""

from flask import current_app

from .errors import (  # noqa: F401
    GeminiAPIError,
    GeminiConfigError,
    GeminiError,
    GeminiMalformedResponseError,
    GeminiRateLimitError,
    GeminiSchemaError,
    GeminiTimeoutError,
)
from .gemini import GeminiService, create_gemini_service  # noqa: F401


def get_service():
    """Return the GeminiService bound to the current Flask app.

    Returns a service with `available=False` when GEMINI_API_KEY is unset;
    calling `generate(...)` then raises GeminiConfigError.
    """
    return current_app.extensions["gemini"]
