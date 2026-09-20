"""Personalized Learning Roadmap (Stage 5 Item 10, blueprint Sections
B.8/B.9/D/E/F/G/K.10).

Converts the user's current weaknesses into a day-by-day learning plan and
links each day back to Smart Practice on that topic (Section B.8), closing
the loop: Practice → Interview → Evaluation → Weakness Detection → Learning.

Input selection (Section B.7 + Section 0 row 8): weak skills come from the
Weaknesses table (worst first), prioritized by the cross-session InterviewMemory
cache — repeated-mistake skills are planned first, because persisted memory is
used ONLY for practice/roadmap prioritization, never mid-interview.

Generation reuses the existing `generate_roadmap` contract verbatim
(schemas.py / prompts.py / GeminiService) — no second AI integration. Every
response is schema-validated server-side before storage; failures map to the
same typed-error flash UX as practice/interview routes.

Regeneration replaces all of the user's roadmap rows in one transaction and
resets progress: the plan is always derived from CURRENT weaknesses (B.8).
"""

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from .ai.errors import (
    GeminiConfigError,
    GeminiError,
    GeminiRateLimitError,
)
from .auth import login_required
from .company_presets import (
    GENERAL_KEY,
    company_context_for,
    company_select_options,
    resolve_company_key,
)
from .models import (
    get_interview_memory,
    get_roadmap,
    get_roadmap_day,
    get_weaknesses,
    replace_roadmap,
    toggle_roadmap_day,
)
from .ratelimit import check_gemini_limit

roadmap_bp = Blueprint("roadmap", __name__)

# Keep plans focused (and API cost bounded, Section J): at most this many
# weak skills feed one generation.
MAX_ROADMAP_SKILLS = 6


@roadmap_bp.route("/roadmap", methods=["GET"])
@login_required
def view():
    days = get_roadmap(g.user["id"])
    memory = get_interview_memory(g.user["id"])
    weaknesses = get_weaknesses(g.user["id"])
    completed = sum(1 for day in days if day["status"] == "completed")

    return render_template(
        "roadmap.html",
        active_page="roadmap",
        days=days,
        total=len(days),
        completed=completed,
        percent=round(100 * completed / len(days)) if days else 0,
        skills_context=days[0]["skill"] if days else "",
        has_weaknesses=bool(weaknesses),
        memory=memory,
        companies=company_select_options(),
        selected_company=GENERAL_KEY,
    )


@roadmap_bp.route("/roadmap/generate", methods=["POST"])
@login_required
def generate():
    """Generate (or regenerate) the plan from current weaknesses."""
    check_gemini_limit()
    selected = _prioritized_weak_skills(g.user["id"])
    if not selected:
        flash(
            "No weak spots detected yet. Finish some practice questions or "
            "a real interview — once the coach spots weaknesses, your plan "
            "can be built from them.",
            "info",
        )
        return redirect(url_for(".view"))

    # Company Presets (Stage 16): validate the submitted key server-side
    # against the static allowlist before it can reach Gemini. Blank/"general"
    # -> General (None); unlisted values (free text, tampered keys) are
    # rejected outright. The company is a transient generation-time input —
    # the roadmap keeps no company column.
    try:
        _, company_preset = resolve_company_key(request.form.get("company"))
    except ValueError:
        flash("Choose a valid company context.", "error")
        return redirect(url_for(".view"))

    # Capture before regeneration wipes the previous plan.
    had_existing_plan = bool(get_roadmap(g.user["id"]))

    service = current_app.extensions["gemini"]
    try:
        inputs = {"weak_skills": selected}
        if company_preset is not None:
            inputs["company"] = company_context_for(company_preset)
        payload = service.generate("generate_roadmap", inputs)
    except GeminiConfigError:
        flash(
            "AI features are not configured yet. Ask an operator to set the "
            "GEMINI_API_KEY on the server.",
            "warning",
        )
        return redirect(url_for(".view"))
    except GeminiRateLimitError:
        flash(
            "The AI coach is rate limited right now. Please wait a moment "
            "and try again.",
            "warning",
        )
        return redirect(url_for(".view"))
    except GeminiError:
        flash(
            "The AI coach could not build your roadmap right now. "
            "Please try again.",
            "error",
        )
        return redirect(url_for(".view"))

    # Normalize the contract payload into sequential days: sort by the model's
    # day hint, then renumber 1..N so day_number is always a clean 1-based
    # order even when the model skips or repeats numbers.
    generated = sorted(payload["roadmap"], key=lambda entry: entry["day"])
    skills_context = ", ".join(selected)
    replace_roadmap(
        g.user["id"],
        [
            (
                skills_context,
                index + 1,
                entry["topic"],
                entry["practice_focus"],
            )
            for index, entry in enumerate(generated)
        ],
    )

    verb = "Regenerated" if had_existing_plan else "Generated"
    flash(f"{verb}: a {len(generated)}-day learning roadmap is ready.", "success")
    return redirect(url_for(".view"))


@roadmap_bp.route("/roadmap/day/<int:day_id>/toggle", methods=["POST"])
@login_required
def toggle(day_id):
    """Progress checkbox: flip one owned day between pending/completed."""
    day = get_roadmap_day(day_id)
    if day is None or day["user_id"] != g.user["id"]:
        abort(404)
    toggle_roadmap_day(day_id)
    return redirect(url_for(".view", _anchor=f"day-{day_id}"))


def _prioritized_weak_skills(user_id):
    """Weak-skill labels for roadmap generation, most urgent first.

    Repeated-mistake skills (InterviewMemory cache) come first — cross-session
    memory exists to prioritize practice/roadmap content — then remaining
    weaknesses in `get_weaknesses` order (frequency DESC, skill ASC). Capped
    at MAX_ROADMAP_SKILLS.
    """
    repeated = []
    memory = get_interview_memory(user_id)
    if memory:
        repeated = memory.get("repeated_mistakes") or []

    def sort_key(row):
        skill = row["skill"]
        return (0 if skill in repeated else 1, -row["frequency"], skill)

    ranked = sorted(get_weaknesses(user_id), key=sort_key)
    return [row["skill"] for row in ranked][:MAX_ROADMAP_SKILLS]
