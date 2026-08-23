"""Prompt templates for the Gemini task types (blueprint Section G).

Each template:
* describes exactly one task,
* embeds the JSON output contract it must satisfy (the same schema that
  `schemas.validate` enforces server-side),
* is pure data — no API calls happen here.

Later stages call these via the registry: `PROMPT_BUILDERS[task](inputs)`.
"""

import json
from dataclasses import dataclass

from .schemas import SCHEMAS

# One consistent "coach voice" for AI-generated text, per blueprint Section I
# (typography/voice direction) — the actual font styling is handled by CSS.
COACH_SYSTEM = (
    "You are InterviewIQ, an expert interview coach for students and fresh "
    "graduates. You help users practice, evaluate their answers, and improve. "
    "Be constructive, specific, and professional."
)


@dataclass(frozen=True)
class Prompt:
    """A completed prompt ready to send to Gemini."""

    system: str
    user: str


def _require(inputs, *keys):
    """Fail fast on missing prompt inputs (programmer error, not user error)."""
    missing = [k for k in keys if k not in inputs or inputs[k] in (None, "")]
    if missing:
        raise ValueError(f"Missing required input(s) for prompt: {', '.join(missing)}")
    return inputs


def _with_schema(text, task):
    schema = SCHEMAS[task]
    return (
        text
        + "\n\nReturn ONLY valid JSON matching this exact structure "
          "(no extra text, no code fences):\n"
        + json.dumps(schema, indent=2)
    )


# ---------------------------------------------------------------------------
# Task templates
# ---------------------------------------------------------------------------

def build_generate_question(inputs):
    _require(inputs, "role", "topic", "difficulty")
    text = (
        f"Create one interview question for a {inputs['role']} role about "
        f"the topic \"{inputs['topic']}\" at {inputs['difficulty']} difficulty. "
        f"Set question_type to one of: conceptual, practical, behavioral, "
        f"scenario. List 2-4 concepts a strong answer should cover."
    )
    return Prompt(system=COACH_SYSTEM, user=_with_schema(text, "generate_question"))


def build_extract_entities(inputs):
    _require(inputs, "question", "answer")
    text = (
        f"Interview question: {inputs['question']}\n"
        f"Candidate's answer: {inputs['answer']}\n\n"
        "Extract the specific technologies, projects, and notable claims the "
        "candidate mentioned in the answer. Classify each entity with kind: "
        "technology, project, claim, or other. Return every distinct mention, "
        "or an empty list if there are none."
    )
    return Prompt(system=COACH_SYSTEM, user=_with_schema(text, "extract_entities"))


def build_generate_follow_up(inputs):
    _require(inputs, "question", "answer", "entity", "depth")
    depth_instruction = {
        "foundational": "ask a more foundational question on the same topic, "
                        "because the previous answer scored low on technical accuracy",
        "deeper": "ask a deeper, edge-case question that probes mastery, "
                  "because the previous answer scored high",
    }.get(inputs["depth"], "ask a natural follow-up")
    text = (
        f"Original question: {inputs['question']}\n"
        f"Candidate's answer: {inputs['answer']}\n"
        f"Follow up on this mention: {inputs['entity']}\n"
        f"Depth: {depth_instruction}.\n"
        f"Ask ONE concise follow-up question. The 'reasoning' field is internal "
        f"only — it must never be shown to the candidate."
    )
    # Blueprint Section H.5: keep the last 2–3 Q&A pairs of this interview
    # session in the prompt so follow-ups stay coherent. Ephemeral context —
    # it is never persisted beyond what Questions/Answers already store.
    history = inputs.get("context") or []
    if history:
        lines = [
            f"- Q: {pair.get('question', '')} | A: {pair.get('answer', '')}"
            for pair in history
        ]
        text += (
            "\nEarlier in this same interview (for coherence only):\n"
            + "\n".join(lines)
        )
    return Prompt(system=COACH_SYSTEM, user=_with_schema(text, "generate_follow_up"))


def build_evaluate_answer(inputs):
    _require(inputs, "question", "answer", "expected_concepts")
    concepts = ", ".join(inputs["expected_concepts"]) if isinstance(
        inputs["expected_concepts"], list
    ) else str(inputs["expected_concepts"])
    text = (
        f"Interview question: {inputs['question']}\n"
        f"Expected concepts: {concepts}\n"
        f"Candidate's answer: {inputs['answer']}\n\n"
        f"Evaluate this answer. Score each of the five dimensions from 0 to 100. "
        f"Give specific feedback, list the missing points a strong answer would "
        f"have included, and provide a model answer."
    )
    return Prompt(system=COACH_SYSTEM, user=_with_schema(text, "evaluate_answer"))


def build_detect_weaknesses(inputs):
    _require(inputs, "evaluations")
    text = (
        "Here are the dimension scores from a candidate's recent interview "
        "answers:\n"
        + json.dumps(inputs["evaluations"], indent=2)
        + "\nIdentify recurring weak skills (low scores across answers) and "
          "strong skills (consistently high scores). Return skill names as "
          "short labels."
    )
    return Prompt(system=COACH_SYSTEM, user=_with_schema(text, "detect_weaknesses"))


def build_generate_roadmap(inputs):
    _require(inputs, "weak_skills")
    skills = ", ".join(inputs["weak_skills"]) if isinstance(
        inputs["weak_skills"], list
    ) else str(inputs["weak_skills"])
    text = (
        f"The candidate's weak skills are: {skills}.\n"
        f"Build a day-by-day learning roadmap. Each day covers one topic and a "
        f"clear practice focus. Start with the most foundational skill and "
        f"progress in dependency order."
    )
    return Prompt(system=COACH_SYSTEM, user=_with_schema(text, "generate_roadmap"))


def build_analyze_resume(inputs):
    _require(inputs, "resume_text")
    text = (
        "Extract structured information from this resume text:\n"
        "----------------------------------------\n"
        + inputs["resume_text"]
        + "\n----------------------------------------\n"
        "Return the candidate's skills, projects, technologies, and "
        "certifications as arrays of short labels."
    )
    return Prompt(system=COACH_SYSTEM, user=_with_schema(text, "analyze_resume"))


def build_generate_report_narrative(inputs):
    _require(inputs, "summary")
    text = (
        "Write a short, encouraging narrative summary (2-3 sentences) of this "
        "interview result for a student's practice report:\n"
        + json.dumps(inputs["summary"], indent=2)
    )
    return Prompt(system=COACH_SYSTEM, user=_with_schema(text, "generate_report_narrative"))


PROMPT_BUILDERS = {
    "generate_question": build_generate_question,
    "extract_entities": build_extract_entities,
    "generate_follow_up": build_generate_follow_up,
    "evaluate_answer": build_evaluate_answer,
    "detect_weaknesses": build_detect_weaknesses,
    "generate_roadmap": build_generate_roadmap,
    "analyze_resume": build_analyze_resume,
    "generate_report_narrative": build_generate_report_narrative,
}


def build_prompt(task, inputs):
    """Build a Prompt for a known task, validating inputs exist."""
    if task not in PROMPT_BUILDERS:
        raise ValueError(f"Unknown Gemini task: {task}")
    return PROMPT_BUILDERS[task](inputs)
