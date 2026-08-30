"""Centralized Gemini service (Gemini 2.5 Flash, blueprint Section G).

Every AI call in the app flows through this module and this module alone. It:

* keeps the API key server-side — the official google-genai client transmits
  it as a request header, never in URLs, templates, JS, logs, or the database,
* requests strict JSON output (`response_mime_type="application/json"`),
* validates every response against the fixed schemas in `schemas.py`,
* retries malformed responses once (blueprint Section G),
* maps every failure to a typed error from `errors.py`.

The default transport wraps the official `google.genai` SDK client; tests
inject a fake transport instead, so the whole service is testable with no
API key and no network access.
"""

import json
import re
import socket

from .errors import (
    GeminiAPIError,
    GeminiConfigError,
    GeminiMalformedResponseError,
    GeminiRateLimitError,
    GeminiSchemaError,
    GeminiTimeoutError,
)
from .prompts import COACH_SYSTEM, Prompt, build_prompt
from .schemas import validate

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"

# finishReason values that mean the model stopped for a policy/cost reason.
_BLOCKED_FINISH_REASONS = {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKED", "OTHER"}

# Health-check contract used by ping() / the /ai/health endpoint.
PING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}
PING_PROMPT_TEXT = (
    'Connectivity check. Reply with exactly this JSON object and nothing '
    'else: {"ok": true}'
)


def _enum_name(value):
    """Normalize enum-ish values (FinishReason.SAFETY / 'SAFETY') to 'SAFETY'."""
    if value is None:
        return ""
    return str(getattr(value, "value", value)).rsplit(".", 1)[-1]


def _extract_text(response):
    """Pull the generated text out of a GenerateContentResponse.

    Works on real SDK response objects and on plain namespace fakes used in
    tests. Raises typed errors for blocked prompts/generations.
    """
    feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(feedback, "block_reason", None)
    if block_reason:
        raise GeminiAPIError(400, f"Gemini blocked the request: {_enum_name(block_reason)}")
    candidates = list(getattr(response, "candidates", None) or [])
    if not candidates:
        raise GeminiMalformedResponseError("Gemini response contained no candidates.")
    candidate = candidates[0]
    finish_reason = _enum_name(getattr(candidate, "finish_reason", None))
    if finish_reason in _BLOCKED_FINISH_REASONS:
        raise GeminiAPIError(400, f"Gemini stopped generation: {finish_reason}")
    content = getattr(candidate, "content", None)
    parts = getattr(content, "parts", None) or []
    text = "".join(getattr(p, "text", "") or "" for p in parts)
    if not isinstance(text, str) or not text.strip():
        raise GeminiMalformedResponseError("Gemini returned an empty generation.")
    return text


def _normalize_base_url(value):
    """Map the configured base URL onto what the SDK expects.

    The SDK composes its own version path, so a legacy '/v1beta' suffix is
    stripped and the Google default collapses to None (SDK default).
    """
    url = (value or "").strip().rstrip("/")
    if url.endswith("/v1beta"):
        url = url[: -len("/v1beta")]
    if not url or url == DEFAULT_BASE_URL:
        return None
    return url


def _sdk_transport(api_key, model, base_url=None, timeout_seconds=None):
    """Build the default transport backed by the official google-genai SDK.

    Returns a callable `(system, user, temperature, max_output_tokens) -> text`.
    The API key is handed to the SDK client only; it is never placed in a URL.
    """
    from google import genai
    from google.genai import types

    try:
        from httpx import (
            HTTPError as _httpx_http_error,
            TimeoutException as _httpx_timeout,
        )
    except ImportError:  # pragma: no cover - httpx ships with the SDK
        _httpx_http_error = _httpx_timeout = None
    timeout_errors = (socket.timeout, TimeoutError) + ((_httpx_timeout,) if _httpx_timeout else ())

    options = {}
    normalized_base_url = _normalize_base_url(base_url)
    if normalized_base_url:
        options["base_url"] = normalized_base_url
    if timeout_seconds:
        options["timeout"] = int(timeout_seconds * 1000)
    client_kwargs = {"api_key": api_key}
    if options:
        client_kwargs["http_options"] = options
    client = genai.Client(**client_kwargs)

    def call(system, user, temperature, max_output_tokens):
        from google.genai import errors as genai_errors

        try:
            response = client.models.generate_content(
                model=model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system or None,
                    temperature=float(temperature),
                    max_output_tokens=int(max_output_tokens),
                    response_mime_type="application/json",
                ),
            )
        except genai_errors.APIError as exc:
            status = int(getattr(exc, "code", 0) or 0)
            message = str(getattr(exc, "message", "") or exc)
            if status == 429:
                raise GeminiRateLimitError(
                    status, f"Gemini API error (HTTP {status}): {message}"
                )
            raise GeminiAPIError(status, f"Gemini API error (HTTP {status}): {message}")
        except timeout_errors:
            raise GeminiTimeoutError("Gemini request timed out.")
        except _httpx_http_error as exc:
            raise GeminiAPIError(0, f"Network error reaching Gemini: {exc}")
        except OSError as exc:
            raise GeminiAPIError(0, f"Network error reaching Gemini: {exc}")
        return _extract_text(response)

    return call


class GeminiService:
    """Server-side client for the Gemini API via the official SDK."""

    def __init__(
        self,
        api_key="",
        model=DEFAULT_MODEL,
        timeout_seconds=30,
        max_retries=1,
        transport=None,
        base_url=None,
    ):
        self.api_key = (api_key or "").strip()
        self.model = model
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, int(max_retries))
        self._transport = transport
        self.available = bool(self.api_key)

    @classmethod
    def from_app(cls, app):
        """Build a service from Flask app config."""
        cfg = app.config
        return cls(
            api_key=cfg.get("GEMINI_API_KEY", ""),
            model=cfg.get("GEMINI_MODEL", DEFAULT_MODEL),
            base_url=cfg.get("GEMINI_BASE_URL"),
            timeout_seconds=cfg.get("GEMINI_TIMEOUT_SECONDS", 30),
            max_retries=cfg.get("GEMINI_MAX_RETRIES", 1),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, task, inputs, **generation_options):
        """Run a known task end-to-end: prompt -> request -> validate -> dict.

        `inputs` are the template variables for the task's prompt (see
        prompts.py). Returns the validated JSON payload as a dict.
        """
        schema = generation_options.pop("schema", None) or _schema_for(task)
        prompt = build_prompt(task, inputs)
        return self.generate_content(prompt, schema, **generation_options)

    def generate_content(self, prompt, schema, temperature=0.7, max_output_tokens=1024):
        """Send a prompt, request JSON, validate against `schema`, return dict.

        Malformed or schema-invalid responses are retried up to `max_retries`
        times, then a typed error is raised (blueprint Section G).
        """
        if not self.available:
            raise GeminiConfigError(
                "GEMINI_API_KEY is not configured. AI features are disabled "
                "until the key is set in the environment."
            )
        if not isinstance(prompt, Prompt):
            raise TypeError("prompt must be a Prompt instance")

        attempts = 1 + self.max_retries
        last_error = None
        for attempt in range(attempts):
            try:
                text = self._transport_call(prompt.system, prompt.user, temperature, max_output_tokens)
                parsed = self._parse_json(text)
                problems = validate(parsed, schema)
                if problems:
                    raise GeminiSchemaError(
                        "Gemini response failed schema validation: " + "; ".join(problems)
                    )
                return parsed
            except (GeminiMalformedResponseError, GeminiSchemaError) as exc:
                last_error = exc
                if attempt == attempts - 1:
                    raise last_error
        raise last_error  # pragma: no cover - loop always returns or raises

    def ping(self):
        """Minimal round-trip verifying key + model + JSON pipeline work.

        Used by the /ai/health endpoint. Returns True when Gemini answers the
        fixed check prompt with {"ok": true}; otherwise raises a typed error.
        """
        data = self.generate_content(
            Prompt(system=COACH_SYSTEM, user=PING_PROMPT_TEXT),
            PING_SCHEMA,
            temperature=0,
            max_output_tokens=64,
        )
        return data.get("ok") is True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_transport(self):
        """Lazily construct the SDK transport on first real call."""
        if self._transport is None:
            self._transport = _sdk_transport(
                self.api_key,
                self.model,
                base_url=self.base_url,
                timeout_seconds=self.timeout_seconds,
            )
        return self._transport

    def _transport_call(self, system, user, temperature, max_output_tokens):
        return self._ensure_transport()(system, user, temperature, max_output_tokens)

    @staticmethod
    def _parse_json(text):
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise GeminiMalformedResponseError(f"Gemini output was not valid JSON: {exc}") from exc


def _schema_for(task):
    """Late import keeps gemini.py free of a hard schema-dependency cycle."""
    from .schemas import SCHEMAS

    if task not in SCHEMAS:
        raise ValueError(f"Unknown Gemini task: {task}")
    return SCHEMAS[task]


def create_gemini_service(app):
    """Create a GeminiService from a Flask app (used by the app factory)."""
    return GeminiService.from_app(app)
