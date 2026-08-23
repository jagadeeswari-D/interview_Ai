"""Weakness/Memory Engine (Stage 4 Item 8, blueprint Sections B.7/D/F/K.8).

Aggregates evaluation history into weak/strong topics and repeated-mistake
flags, and maintains the InterviewMemory derived cache.

Detection is deterministic score aggregation, exactly as Section B.7 defines
it ("aggregates low-scoring dimensions/topics across the session and
historical data into the Weaknesses table") — transparent thresholds instead
of an extra AI call. The `detect_weaknesses` Gemini contract from Section G
stays defined in schemas.py/prompts.py for later phases that want narrative
labels; storage here never depends on it.

Rules (all thresholds in one place):
* a dimension scoring below WEAK_DIMENSION_THRESHOLD on any evaluated answer
  records one weak occurrence of that dimension label,
* if an answer's overall score is below WEAK_TOPIC_THRESHOLD, one weak
  occurrence of the session's skill context is recorded (practice topic, or
  capitalized interview type in Real Mode — matching Performance rows),
* occurrences are upserted per interview: `frequency` accumulates across
  sessions while `interview_count` increments at most once per interview,
  so interview_count >= REPEATED_MISTAKE_INTERVIEWS flags a repeated mistake,
* strong topics come from Performance averages >= STRONG_SKILL_THRESHOLD,
* InterviewMemory is recomputed from Weaknesses + Performance after every
  finished interview (Section F note: derived cache, never source of truth).
"""

from .evaluation import SCORE_DIMENSIONS
from .models import (
    get_skill_averages,
    get_weaknesses,
    record_weakness_events,
    save_interview_memory,
)

WEAK_DIMENSION_THRESHOLD = 50
WEAK_TOPIC_THRESHOLD = 50
STRONG_SKILL_THRESHOLD = 75
REPEATED_MISTAKE_INTERVIEWS = 2
MAX_MEMORY_TOPICS = 8


def skill_label_for(interview):
    """Session skill context, consistent with existing Performance rows."""
    if interview["mode"] == "real":
        return interview["type"].capitalize()
    return interview["type"]  # practice rows keep the topic in `type`


def weak_skills_for_answer(entry):
    """Dimension labels scored below the weak threshold in one answer."""
    scores = entry.get("scores") or {}
    return [
        label for key, label in SCORE_DIMENSIONS
        if scores.get(key) is not None and scores[key] < WEAK_DIMENSION_THRESHOLD
    ]


def record_session_results(user_id, interview, entries):
    """Aggregate evaluated answer entries into Weaknesses + memory cache.

    `entries` are transcript-style dicts of the evaluations recorded SINCE
    the previous call (routes pass only new answers — practice completes
    per question — or the full transcript once at Real Mode finalization).
    Entries without an answer/score are skipped.
    """
    skill_events = {}
    topic_label = skill_label_for(interview)

    for entry in entries:
        if entry.get("user_answer") is None or entry.get("score") is None:
            continue
        for label in weak_skills_for_answer(entry):
            skill_events[label] = skill_events.get(label, 0) + 1
        if entry["score"] < WEAK_TOPIC_THRESHOLD:
            skill_events[topic_label] = skill_events.get(topic_label, 0) + 1

    if skill_events:
        record_weakness_events(user_id, skill_events, interview["id"])

    refresh_interview_memory(user_id)


def refresh_interview_memory(user_id):
    """Recompute the InterviewMemory derived cache from its sources."""
    weakness_rows = get_weaknesses(user_id)
    weak_topics = [row["skill"] for row in weakness_rows[:MAX_MEMORY_TOPICS]]
    repeated_mistakes = [
        row["skill"] for row in weakness_rows
        if row["interview_count"] >= REPEATED_MISTAKE_INTERVIEWS
    ][:MAX_MEMORY_TOPICS]
    strong_topics = [
        row["skill"] for row in get_skill_averages(user_id)
        if row["average_score"] >= STRONG_SKILL_THRESHOLD
    ][:MAX_MEMORY_TOPICS]

    save_interview_memory(user_id, strong_topics, weak_topics,
                          repeated_mistakes)


def on_interview_completed(user_id, interview, transcript=None):
    """Single post-completion hook for both modes (weaknesses + cache)."""
    from .models import get_transcript

    if transcript is None:
        transcript = get_transcript(interview["id"])
    record_session_results(user_id, interview, transcript)