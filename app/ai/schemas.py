"""Structured JSON output contracts for Gemini calls.

Every contract is the exact schema defined in blueprint Section G. All
response payloads are validated against these schemas server-side before they
are stored or returned. The validator below implements the small JSON-Schema
subset we actually need (object / array / string / number / integer, required
keys, item schemas, ranges, enums) — deliberately hand-built to keep the
stack lean and fully testable.
"""


class SchemaValidationError(ValueError):
    """Raised by `ensure` when data does not match the schema."""


# ---------------------------------------------------------------------------
# Output contracts (blueprint Section G)
# ---------------------------------------------------------------------------

SCORES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "technical_accuracy",
        "relevance",
        "completeness",
        "clarity",
        "communication",
    ],
    "properties": {
        "technical_accuracy": {"type": "number", "minimum": 0, "maximum": 100},
        "relevance": {"type": "number", "minimum": 0, "maximum": 100},
        "completeness": {"type": "number", "minimum": 0, "maximum": 100},
        "clarity": {"type": "number", "minimum": 0, "maximum": 100},
        "communication": {"type": "number", "minimum": 0, "maximum": 100},
    },
}

SCHEMAS = {
    "generate_question": {
        "type": "object",
        "required": ["question", "question_type", "expected_concepts"],
        "properties": {
            "question": {"type": "string", "minLength": 1},
            "question_type": {"type": "string", "minLength": 1},
            "expected_concepts": {"type": "array", "items": {"type": "string"}},
        },
    },
    # Blueprint Section H.1: entities mentioned in an answer feed adaptive
    # follow-up selection. `kind` drives relevance prioritization (H.2):
    # project/technology mentions beat generic claims.
    "extract_entities": {
        "type": "object",
        "required": ["entities"],
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["text", "kind"],
                    "properties": {
                        "text": {"type": "string", "minLength": 1},
                        "kind": {
                            "type": "string",
                            "enum": ["technology", "project", "claim", "other"],
                        },
                    },
                },
            }
        },
    },
    "generate_follow_up": {
        "type": "object",
        "required": ["follow_up_question", "reasoning"],
        "properties": {
            "follow_up_question": {"type": "string", "minLength": 1},
            "reasoning": {"type": "string", "minLength": 1},
        },
    },
    "evaluate_answer": {
        "type": "object",
        "required": ["scores", "feedback", "missing_points", "model_answer"],
        "properties": {
            "scores": SCORES_SCHEMA,
            "feedback": {"type": "string", "minLength": 1},
            "missing_points": {"type": "array", "items": {"type": "string"}},
            "model_answer": {"type": "string", "minLength": 1},
        },
    },
    "detect_weaknesses": {
        "type": "object",
        "required": ["weak_skills", "strong_skills"],
        "properties": {
            "weak_skills": {"type": "array", "items": {"type": "string"}},
            "strong_skills": {"type": "array", "items": {"type": "string"}},
        },
    },
    "generate_roadmap": {
        "type": "object",
        "required": ["roadmap"],
        "properties": {
            "roadmap": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["day", "topic", "practice_focus"],
                    "properties": {
                        "day": {"type": "integer", "minimum": 1},
                        "topic": {"type": "string", "minLength": 1},
                        "practice_focus": {"type": "string", "minLength": 1},
                    },
                },
            }
        },
    },
    "analyze_resume": {
        "type": "object",
        "required": ["skills", "projects", "technologies", "certifications"],
        "properties": {
            "skills": {"type": "array", "items": {"type": "string"}},
            "projects": {"type": "array", "items": {"type": "string"}},
            "technologies": {"type": "array", "items": {"type": "string"}},
            "certifications": {"type": "array", "items": {"type": "string"}},
        },
    },
    "generate_report_narrative": {
        "type": "object",
        "required": ["narrative_summary"],
        "properties": {
            "narrative_summary": {"type": "string", "minLength": 1},
        },
    },
}


# ---------------------------------------------------------------------------
# Minimal JSON-Schema validator
# ---------------------------------------------------------------------------

def validate(data, schema):
    """Return a list of human-readable error strings.

    An empty list means `data` conforms to `schema`.
    """
    errors = []
    _check(data, schema, "$", errors)
    return errors


def ensure(data, schema):
    """Raise SchemaValidationError if `data` does not conform to `schema`."""
    errors = validate(data, schema)
    if errors:
        raise SchemaValidationError("; ".join(errors))
    return data


def _add(errors, path, message):
    errors.append(f"{path} {message}")


def _check(value, schema, path, errors):
    expected = schema.get("type")

    if expected == "object":
        if not isinstance(value, dict):
            _add(errors, path, f"must be an object, got {type(value).__name__}")
            return
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                _add(errors, f"{path}.{key}", "is required")
        properties = schema.get("properties", {})
        for key, subschema in properties.items():
            if key in value:
                _check(value[key], subschema, f"{path}.{key}", errors)
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    _add(errors, path, f"has unexpected property {key!r}")

    elif expected == "array":
        if not isinstance(value, list):
            _add(errors, path, f"must be an array, got {type(value).__name__}")
            return
        if "minItems" in schema and len(value) < schema["minItems"]:
            _add(errors, path, f"must have at least {schema['minItems']} items")
        if "items" in schema:
            for index, item in enumerate(value):
                _check(item, schema["items"], f"{path}[{index}]", errors)

    elif expected == "string":
        if not isinstance(value, str):
            _add(errors, path, f"must be a string, got {type(value).__name__}")
            return
        if "minLength" in schema and len(value) < schema["minLength"]:
            _add(errors, path, f"must be at least {schema['minLength']} characters")
        if "enum" in schema and value not in schema["enum"]:
            _add(errors, path, f"must be one of {schema['enum']!r}")

    elif expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _add(errors, path, f"must be a number, got {type(value).__name__}")
            return
        if "minimum" in schema and value < schema["minimum"]:
            _add(errors, path, f"must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            _add(errors, path, f"must be <= {schema['maximum']}")

    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            _add(errors, path, f"must be an integer, got {type(value).__name__}")
            return
        if "minimum" in schema and value < schema["minimum"]:
            _add(errors, path, f"must be >= {schema['minimum']}")

    elif expected == "boolean":
        if not isinstance(value, bool):
            _add(errors, path, f"must be a boolean, got {type(value).__name__}")

    elif expected is None:
        return

    else:
        _add(errors, path, f"uses unsupported schema type {expected!r}")
