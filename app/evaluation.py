"""AI Evaluation Engine (Stage 4 Item 7, blueprint Sections B.6/D/G/K.7).

Single home for answer evaluation across both modes. Previously this logic
was duplicated inline in practice.py and interview.py; both now delegate
here, so there is exactly one evaluation system.

Responsibilities (Section D):
* send answer + rubric to Gemini via the shared `evaluate_answer` contract
  (schemas.py / prompts.py are reused verbatim — no second contract),
* parse and normalize the fixed-schema JSON (five dimensions clamped to
  0–100),
* normalize the overall score as the mean of the five dimensions rounded to
  one decimal,
* store the result to Answers + Performance.

Route handlers keep their own user-facing error UX: this module raises the
typed GeminiError subclasses from ai.errors untouched.
"""

from .models import add_performance, save_answer

# The five rubric dimensions (blueprint Section G evaluate_answer contract).
SCORE_DIMENSIONS = [
    ("technical_accuracy", "Technical accuracy"),
    ("relevance", "Relevance"),
    ("completeness", "Completeness"),
    ("clarity", "Clarity"),
    ("communication", "Communication"),
]

DIMENSION_KEYS = [key for key, _ in SCORE_DIMENSIONS]


def normalize_scores(scores):
    """Validate and clamp the five dimension scores to floats in [0, 100].

    Raises ValueError on a missing or non-numeric dimension — a programmer
    or transport-contract error, not user input, so routes do not catch it.
    """
    if not isinstance(scores, dict):
        raise ValueError("scores must be a dict of the five dimensions")
    normalized = {}
    for key in DIMENSION_KEYS:
        if key not in scores:
            raise ValueError(f"missing score dimension: {key}")
        value = scores[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"score dimension {key} must be numeric")
        normalized[key] = float(min(100.0, max(0.0, value)))
    return normalized


def mean_score(values):
    """Mean of the non-None values rounded to one decimal, or None."""
    values = [float(v) for v in values if v is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def overall_score(scores):
    """Blueprint normalization: mean of the five dimensions, one decimal."""
    return mean_score(list(scores.values()))


def evaluate_answer(service, question_text, answer_text, expected_concepts):
    """Evaluate one answer through the shared Gemini contract.

    Returns the validated payload with normalized scores added. Raises
    GeminiConfigError / GeminiRateLimitError / GeminiError on failure.
    """
    # Thinking models (e.g. gemini-3.6-flash) spend hidden reasoning tokens
    # from this budget before emitting the visible JSON, so the shared 1024
    # default truncates the evaluation payload mid-string. 4096 leaves room
    # for thoughts + the full contract (scores/feedback/missing/model answer).
    payload = service.generate(
        "evaluate_answer",
        {
            "question": question_text,
            "answer": answer_text,
            "expected_concepts": list(expected_concepts or []),
        },
        max_output_tokens=4096,
    )
    payload["scores"] = normalize_scores(payload["scores"])
    return payload


def store_evaluation(question_id, user_answer, evaluation,
                     user_id, skill_label, interview_id):
    """Persist an evaluation to Answers + Performance (blueprint B.6).

    `skill_label` is the Performance skill context: the practice topic in
    Smart Practice Mode, the capitalized interview type in Real Interview
    Mode (same convention the routes already used). Returns
    `(overall_score, technical_accuracy)` so Real Mode can drive adaptive
    depth without re-reading the row.
    """
    scores = evaluation["scores"]
    overall = overall_score(scores)
    save_answer(
        question_id,
        user_answer,
        overall,
        scores,
        evaluation["feedback"],
        evaluation["missing_points"],
        evaluation["model_answer"],
    )
    add_performance(user_id, skill_label, overall, interview_id)
    return overall, scores.get("technical_accuracy")
