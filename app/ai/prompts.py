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


def _preset_company_fields(inputs):
    """Bounded allowlisted company fields for a prompt, or None.

    Mirrors the optional company block used by `build_generate_question`:
    context is only ever appended when `company` is a dict with a non-empty
    `name` (composed server-side from `company_context_for`), so the base
    prompt text stays byte-for-byte identical whenever General / No Company
    is selected.
    """
    company = inputs.get("company")
    if not isinstance(company, dict):
        return None
    name = str(company.get("name") or "").strip()
    if not name:
        return None
    context = str(company.get("context") or "").strip()
    focus = [
        str(item) for item in (company.get("focus_areas") or [])
        if str(item).strip()
    ]
    return name, context, focus


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
    # Company Presets (Phase 10 / Stage 1): when a validated preset context is
    # provided it is appended as bounded, static prep context. It is composed
    # only from `company_context_for` (allowlisted fields, never user text) and
    # is optional, so pre-Phase-10 prompts are byte-for-byte unchanged.
    company = inputs.get("company")
    if isinstance(company, dict) and (company.get("name") or "").strip():
        name = str(company["name"]).strip()
        context = str(company.get("context") or "").strip()
        focus = [str(item) for item in (company.get("focus_areas") or []) if str(item).strip()]
        text += (
            f"\nCompany context: the candidate is preparing for an interview "
            f"at {name}, {context}."
        )
        if focus:
            text += (
                " Weave in these focus areas where relevant: "
                + ", ".join(focus)
                + "."
            )
        text += (
            "\nThis is simulated preparation practice only — ask questions "
            "about the role and topic above; do not reproduce or claim any "
            "official or proprietary interview questions."
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
    # Company Presets (Stage 16): same bounded static context as question
    # generation — an additional signal that only steers the follow-up the
    # candidate's own mention already opened, never replacing it.
    fields = _preset_company_fields(inputs)
    if fields is not None:
        name, context, focus = fields
        text += (
            f"\nCompany context: the candidate is preparing for an interview "
            f"at {name}, {context}."
        )
        if focus:
            text += (
                " Where the candidate's mention allows, steer the follow-up "
                "toward: " + ", ".join(focus) + "."
            )
        text += (
            "\nThis is simulated preparation practice only — never reproduce "
            "or claim official or proprietary interview questions."
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
    # Company Presets (Stage 16): allows the selected company's emphasis to
    # shape how relevance/completeness are judged, without touching the fixed
    # five-dimension rubric or score ranges.
    fields = _preset_company_fields(inputs)
    if fields is not None:
        name, context, focus = fields
        text += (
            f"\nCompany context: the candidate is preparing for an interview "
            f"at {name}, {context}."
        )
        if focus:
            text += (
                " Weight these focus areas when judging relevance and "
                "completeness: " + ", ".join(focus) + "."
            )
        text += (
            "\nKeep the five scoring dimensions and 0-100 ranges unchanged. "
            "This is simulated preparation practice only — evaluate against "
            "the role, topic and dimensions above; do not invent company-"
            "specific or proprietary criteria."
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
    # Company Presets (Stage 16): lets a selected company's emphasis nudge
    # learning priorities, but the plan stays a genuine day-by-day plan driven
    # by the candidate's actual weak skills.
    fields = _preset_company_fields(inputs)
    if fields is not None:
        name, context, focus = fields
        text += (
            f"\nCompany context: the candidate is preparing for interviews at "
            f"{name}, {context}."
        )
        if focus:
            text += (
                " Where relevant to the weak skills above, give learning "
                "priority to: " + ", ".join(focus) + "."
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
