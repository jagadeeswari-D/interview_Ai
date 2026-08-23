"""Typed error hierarchy for the Gemini service.

Every failure mode from the blueprint (Section G) maps to a distinct
exception so callers can handle them explicitly instead of string-matching:

* missing API key        -> GeminiConfigError
* API failure            -> GeminiAPIError
* timeout                -> GeminiTimeoutError
* rate/API errors        -> GeminiRateLimitError (subclass of GeminiAPIError)
* malformed response     -> GeminiMalformedResponseError
* schema mismatch        -> GeminiSchemaError
"""


class GeminiError(Exception):
    """Base class for all Gemini integration errors."""


class GeminiConfigError(GeminiError):
    """Configuration problem — e.g. GEMINI_API_KEY is missing."""


class GeminiTimeoutError(GeminiError):
    """The request exceeded the configured timeout."""


class GeminiAPIError(GeminiError):
    """The Gemini API returned an error status code."""

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class GeminiRateLimitError(GeminiAPIError):
    """HTTP 429 — rate limit / quota exceeded. Raised so callers can slow down
    or surface a cost-control message without consuming more quota."""


class GeminiMalformedResponseError(GeminiError):
    """The response could not be parsed as the expected JSON."""


class GeminiSchemaError(GeminiError):
    """The response parsed as JSON but did not match the expected schema."""
