"""Tests for the Stage 2 Gemini integration foundation.

The model transport is replaced with a fake, so these tests make NO real API
calls and do NOT need a GEMINI_API_KEY. The mocked responses are synthetic
test fixtures only — never mistaken for real AI output.

Run:  .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import httpx

from interview_Ai.app.ai.errors import (
    GeminiAPIError,
    GeminiConfigError,
    GeminiMalformedResponseError,
    GeminiRateLimitError,
    GeminiSchemaError,
    GeminiTimeoutError,
)
from interview_Ai.app.ai.gemini import (
    DEFAULT_MODEL,
    GeminiService,
    _extract_text,
    _sdk_transport,
)

from interview_Ai.app.ai.errors import (
    GeminiAPIError,
    GeminiConfigError,
    GeminiMalformedResponseError,
    GeminiRateLimitError,
    GeminiSchemaError,
    GeminiTimeoutError,
)
from interview_Ai.app.ai.gemini import GeminiService, _extract_text
from interview_Ai.app.ai.prompts import COACH_SYSTEM, PROMPT_BUILDERS, build_prompt
from interview_Ai.app.ai.schemas import SCHEMAS, SchemaValidationError, ensure, validate


def _sdk_response(text="{}", finish_reason="STOP", n_candidates=1):
    """Fake google-genai response object (snake_case attrs, matching what
    _extract_text reads from real SDK responses). Synthetic fixture only."""
    if n_candidates == 0:
        return SimpleNamespace(prompt_feedback=None, candidates=[])
    return SimpleNamespace(
        prompt_feedback=None,
        candidates=[
            SimpleNamespace(
                finish_reason=finish_reason,
                content=SimpleNamespace(parts=[SimpleNamespace(text=text)]),
            )
        ],
    )


def _fake_transport(responses):
    """Return (transport, calls) for the SDK-backed transport contract:
    (system, user, temperature, max_output_tokens) -> response text.
    Entries of `responses` are consumed in order:
      * str          -> returned as the generated text,
      * Exception    -> raised (mimics the SDK wrapper's error mapping),
      * SimpleNamespace -> passed through _extract_text like a real response,
      * callable     -> invoked with the accumulated calls.
    """
    calls = []
    queue = list(responses)

    def transport(system, user, temperature, max_output_tokens):
        calls.append({
            "system": system,
            "user": user,
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        })
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, SimpleNamespace):
            return _extract_text(item)
        if callable(item):
            return item(calls)
        return item

    return transport, calls


class MissingKeyTests(unittest.TestCase):
    def test_service_reports_unavailable(self):
        svc = GeminiService(api_key="")
        self.assertFalse(svc.available)

    def test_generate_raises_config_error_without_key(self):
        svc = GeminiService(api_key="")
        with self.assertRaises(GeminiConfigError):
            svc.generate_content(build_prompt("generate_question", {
                "role": "Backend Engineer", "topic": "SQL", "difficulty": "medium"
            }), SCHEMAS["generate_question"])

    def test_generate_routing_raises_config_error_without_key(self):
        svc = GeminiService(api_key="  ")
        with self.assertRaises(GeminiConfigError):
            svc.generate("generate_question", {"role": "x", "topic": "y", "difficulty": "easy"})


class PromptTests(unittest.TestCase):
    def test_all_tasks_build_with_required_inputs(self):
        fixtures = {
            "generate_question": {"role": "SWE", "topic": "SQL", "difficulty": "medium"},
            "generate_follow_up": {"question": "q", "answer": "a", "entity": "Flask", "depth": "deeper"},
            "evaluate_answer": {"question": "q", "answer": "a", "expected_concepts": ["c1", "c2"]},
            "detect_weaknesses": {"evaluations": [{"technical_accuracy": 40}]},
            "generate_roadmap": {"weak_skills": ["SQL", "APIs"]},
            "analyze_resume": {"resume_text": "Experienced in Python."},
            "generate_report_narrative": {"summary": {"role": "SWE", "score": 70}},
        }
        for task, inputs in fixtures.items():
            prompt = build_prompt(task, inputs)
            self.assertIn("Return ONLY valid JSON", prompt.user)
            # The embedded schema should describe the contract keys.
            self.assertIn(SCHEMAS[task]["type"], prompt.user)

    def test_missing_inputs_raise_value_error(self):
        with self.assertRaises(ValueError):
            build_prompt("generate_question", {"role": "SWE"})

    def test_unknown_task_raises(self):
        with self.assertRaises(ValueError):
            build_prompt("nope", {})

    def test_registry_matches_schemas(self):
        self.assertEqual(set(PROMPT_BUILDERS), set(SCHEMAS))


class SchemaValidatorTests(unittest.TestCase):
    def test_valid_question_passes(self):
        data = {"question": "What is an index?", "question_type": "conceptual",
                "expected_concepts": ["B-tree", "query plans"]}
        self.assertEqual(validate(data, SCHEMAS["generate_question"]), [])
        ensure(data, SCHEMAS["generate_question"])

    def test_missing_required_key_reported(self):
        errors = validate({"question": "q", "question_type": "x"}, SCHEMAS["generate_question"])
        self.assertTrue(any("expected_concepts" in e and "required" in e for e in errors))

    def test_wrong_type_reported(self):
        errors = validate({"question": "q", "question_type": "x", "expected_concepts": "nope"},
                          SCHEMAS["generate_question"])
        self.assertTrue(any("expected_concepts" in e and "array" in e for e in errors))

    def test_evaluation_scores_bounds_and_extra_keys(self):
        data = {"scores": {"technical_accuracy": 90, "relevance": 50, "completeness": 10,
                           "clarity": 20, "communication": 30, "extra": 5},
                "feedback": "Good", "missing_points": ["m"], "model_answer": "m"}
        errors = validate(data, SCHEMAS["evaluate_answer"])
        self.assertTrue(any("unexpected property" in e for e in errors))
        data["scores"].pop("extra")
        data["scores"]["relevance"] = 150
        errors = validate(data, SCHEMAS["evaluate_answer"])
        self.assertTrue(any("relevance" in e and "100" in e for e in errors))

    def test_roadmap_day_must_be_integer(self):
        data = {"roadmap": [{"day": 1.5, "topic": "SQL", "practice_focus": "indexes"}]}
        errors = validate(data, SCHEMAS["generate_roadmap"])
        self.assertTrue(any("day" in e and "integer" in e for e in errors))

    def test_ensure_raises_on_invalid(self):
        with self.assertRaises(SchemaValidationError):
            ensure({"question": "q", "question_type": "x", "expected_concepts": "nope"},
                   SCHEMAS["generate_question"])


class ServiceCallTests(unittest.TestCase):
    def _svc(self, responses, api_key="test-secret-key"):
        transport, calls = _fake_transport(responses)
        svc = GeminiService(api_key=api_key, max_retries=1, transport=transport)
        return svc, calls

    def test_successful_call_returns_validated_dict(self):
        payload = {"question": "What is an index?", "question_type": "conceptual",
                   "expected_concepts": ["B-tree"]}
        svc, calls = self._svc([json.dumps(payload)])
        result = svc.generate("generate_question",
                             {"role": "SWE", "topic": "SQL", "difficulty": "medium"})
        self.assertEqual(result, payload)

        call = calls[0]
        # Coach persona as system turn; task inputs + JSON-only directive in
        # the user turn.
        self.assertEqual(call["system"], COACH_SYSTEM)
        self.assertIn("SQL", call["user"])
        self.assertIn("Return ONLY valid JSON", call["user"])
        self.assertEqual(call["temperature"], 0.7)
        self.assertEqual(call["max_tokens"], 1024)
        # Key hygiene: the API key is handed to the service only — it never
        # travels inside prompt text.
        self.assertNotIn("test-secret-key", call["system"])
        self.assertNotIn("test-secret-key", call["user"])

    def test_json_code_fence_is_stripped(self):
        payload = {"follow_up_question": "Tell me more.", "reasoning": "probe depth"}
        wrapped = "```json\n" + json.dumps(payload) + "\n```"
        svc, _ = self._svc([wrapped])
        result = svc.generate("generate_follow_up",
                              {"question": "q", "answer": "a", "entity": "Flask", "depth": "deeper"})
        self.assertEqual(result, payload)

    def test_malformed_json_retries_once_then_raises(self):
        svc, calls = self._svc(["not json at all {{{", "not json at all {{{"])
        with self.assertRaises(GeminiMalformedResponseError):
            svc.generate("generate_question",
                         {"role": "SWE", "topic": "SQL", "difficulty": "medium"})
        self.assertEqual(len(calls), 2)  # single retry per blueprint

    def test_schema_mismatch_retries_once_then_raises(self):
        # Missing required key -> schema error on both attempts.
        bad = json.dumps({"question": "q"})
        svc, calls = self._svc([bad, bad])
        with self.assertRaises(GeminiSchemaError):
            svc.generate("generate_question",
                         {"role": "SWE", "topic": "SQL", "difficulty": "medium"})
        self.assertEqual(len(calls), 2)

    def test_retry_can_recover(self):
        good = {"question": "ok?", "question_type": "conceptual", "expected_concepts": []}
        svc, _ = self._svc([json.dumps({"question": "q"}), json.dumps(good)])
        result = svc.generate("generate_question",
                             {"role": "SWE", "topic": "SQL", "difficulty": "medium"})
        self.assertEqual(result, good)

    def test_timeout_propagates_without_retry(self):
        def transport(system, user, temperature, max_output_tokens):
            raise GeminiTimeoutError("Gemini request timed out.")
        svc = GeminiService(api_key="k", transport=transport)
        with self.assertRaises(GeminiTimeoutError):
            svc.generate("generate_question",
                         {"role": "SWE", "topic": "SQL", "difficulty": "medium"})

    def test_rate_limit_error(self):
        err = GeminiRateLimitError(429, "Gemini API error (HTTP 429): Quota exceeded")
        svc, calls = self._svc([err])
        with self.assertRaises(GeminiRateLimitError) as ctx:
            svc.generate("generate_question",
                         {"role": "SWE", "topic": "SQL", "difficulty": "medium"})
        self.assertIn("429", str(ctx.exception))
        self.assertEqual(len(calls), 1)  # no retry burn on API errors

    def test_api_error_with_status(self):
        err = GeminiAPIError(403, "Gemini API error (HTTP 403): Invalid API key")
        svc, _ = self._svc([err])
        with self.assertRaises(GeminiAPIError) as ctx:
            svc.generate("generate_question",
                         {"role": "SWE", "topic": "SQL", "difficulty": "medium"})
        self.assertEqual(ctx.exception.status_code, 403)

    def test_blocked_finish_reason(self):
        response = _sdk_response(text="no", finish_reason="SAFETY")
        svc, _ = self._svc([response])
        with self.assertRaises(GeminiAPIError):
            svc.generate("generate_question",
                         {"role": "SWE", "topic": "SQL", "difficulty": "medium"})

    def test_no_candidates_is_malformed(self):
        svc, calls = self._svc([_sdk_response(n_candidates=0),
                                _sdk_response(n_candidates=0)])
        with self.assertRaises(GeminiMalformedResponseError):
            svc.generate("generate_question",
                         {"role": "SWE", "topic": "SQL", "difficulty": "medium"})
        self.assertEqual(len(calls), 2)

    def test_from_app_reads_config(self):
        os.environ.pop("GEMINI_API_KEY", None)
        tmp = tempfile.mkdtemp()
        os.environ["DATABASE_PATH"] = os.path.join(tmp, "test.db")
        from interview_Ai.app import create_app

        app = create_app()
        app.config["GEMINI_API_KEY"] = "env-key"
        app.config["GEMINI_TIMEOUT_SECONDS"] = 7
        app.config["GEMINI_MAX_RETRIES"] = 0
        svc = GeminiService.from_app(app)
        self.assertEqual(svc.api_key, "env-key")
        self.assertEqual(svc.timeout_seconds, 7)
        self.assertEqual(svc.max_retries, 0)
        self.assertTrue(svc.available)

    def test_app_boots_and_serves_without_key(self):
        os.environ.pop("GEMINI_API_KEY", None)
        tmp = tempfile.mkdtemp()
        os.environ["DATABASE_PATH"] = os.path.join(tmp, "test.db")
        from interview_Ai.app import create_app

        app = create_app()
        app.config["TESTING"] = True
        client = app.test_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        # The bound service exists but is not usable without a key.
        service = app.extensions["gemini"]
        self.assertFalse(service.available)
        with self.assertRaises(GeminiConfigError):
            service.generate("generate_question",
                             {"role": "SWE", "topic": "SQL", "difficulty": "medium"})


class TransportErrorMappingTests(unittest.TestCase):
    """httpx transport failures must map onto the typed Gemini errors."""

    def _sdk_call(self, side_effect):
        import google.genai as genai

        client = mock.MagicMock()
        client.models.generate_content.side_effect = side_effect
        with mock.patch.object(genai, "Client", return_value=client):
            transport = _sdk_transport(
                api_key="test-secret-key", model=DEFAULT_MODEL
            )
        return transport

    def test_connect_error_maps_to_gemini_api_error(self):
        transport = self._sdk_call(
            httpx.ConnectError("connection refused to Gemini")
        )
        with self.assertRaises(GeminiAPIError) as ctx:
            transport(COACH_SYSTEM, "user prompt", 0.7, 1024)
        self.assertEqual(ctx.exception.status_code, 0)
        self.assertIn("Network error reaching Gemini", str(ctx.exception))

    def test_timeout_still_maps_to_timeout_error(self):
        transport = self._sdk_call(
            httpx.ReadTimeout("request timed out")
        )
        with self.assertRaises(GeminiTimeoutError):
            transport(COACH_SYSTEM, "user prompt", 0.7, 1024)


if __name__ == "__main__":
    unittest.main(verbosity=2)
