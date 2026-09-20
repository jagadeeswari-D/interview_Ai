"""Company Presets (Phase 10 / Stage 1).

A Company Preset is a predefined, server-side interview context for
preparing against the general expectations commonly associated with a
recognizable employer. Presets are static configuration (blueprint
Section L defers them as "a content/data problem ... can be added as static
config later") — they are allowlisted by key, never built from user text,
and represent a *simulated preparation context*, never an assertion of any
official hiring process.

Security contract (Phase 10 / Stage 1):
  * the browser only ever submits a preset `key`;
  * `company_preset_by_key` returns None for any unknown value (free text,
    tampered keys, or anything belonging to another user), so invalid input
    is rejected safely by callers — it is never sent to Gemini, never
    persisted, and never rendered;
  * `company_context_for` composes the AI-facing structure only from the
    static fields below, so no user-controlled text can reach a prompt;
  * "General / No Company" is the default and stores no company on a
    session, keeping pre-Phase-10 behavior identical.
"""

COMPANY_PRESETS = [
    {
        "key": "general",
        "name": "General / No Company",
        "context": "a broad, balanced preparation setting",
        "focus_areas": [],
        "short": "No company context — practice against general expectations.",
    },
    {
        "key": "amazon",
        "name": "Amazon",
        "context": (
            "a large e-commerce and cloud-services company whose interview "
            "preparation commonly emphasizes customer obsession, ownership, "
            "and decisions that scale"
        ),
        "focus_areas": ["customer obsession", "ownership", "scaling decisions"],
        "short": "Emphasizes customer obsession, ownership, and scaling thinking.",
    },
    {
        "key": "google",
        "name": "Google",
        "context": (
            "a large technology company whose interview preparation commonly "
            "emphasizes analytical problem solving and engineering fundamentals"
        ),
        "focus_areas": ["analytical problem solving", "engineering fundamentals", "clarity of thought"],
        "short": "Emphasizes analytical problem solving and engineering fundamentals.",
    },
    {
        "key": "microsoft",
        "name": "Microsoft",
        "context": (
            "a large software company whose interview preparation commonly "
            "emphasizes collaboration, adaptability, and product technology "
            "fundamentals"
        ),
        "focus_areas": ["collaboration", "adaptability", "product fundamentals"],
        "short": "Emphasizes collaboration, adaptability, and product fundamentals.",
    },
    {
        "key": "meta",
        "name": "Meta",
        "context": (
            "a company known for its global products and mission whose "
            "interview preparation commonly emphasizes product sense, "
            "problem solving, and a fast-moving engineering culture"
        ),
        "focus_areas": ["product sense", "problem solving", "impact-oriented thinking"],
        "short": "Emphasizes product sense, problem solving, and impact.",
    },
    {
        "key": "apple",
        "name": "Apple",
        "context": (
            "a company known for product design and quality whose interview "
            "preparation commonly emphasizes design thinking, craftsmanship, "
            "and attention to detail"
        ),
        "focus_areas": ["design thinking", "craftsmanship", "attention to detail"],
        "short": "Emphasizes design thinking, craftsmanship, and detail.",
    },
    {
        "key": "zoho",
        "name": "Zoho",
        "context": (
            "a global software company with a broad product suite whose "
            "interview preparation commonly emphasizes technology "
            "fundamentals, practical engineering, and product ownership"
        ),
        "focus_areas": ["technology fundamentals", "practical engineering", "product ownership"],
        "short": "Emphasizes technology fundamentals and practical engineering.",
    },
    {
        "key": "tcs",
        "name": "TCS",
        "context": (
            "a global IT services and consulting company whose interview "
            "preparation commonly emphasizes technology fundamentals, clear "
            "communication, and delivery-oriented thinking"
        ),
        "focus_areas": ["technology fundamentals", "communication", "delivery thinking"],
        "short": "Emphasizes technology fundamentals and clear communication.",
    },
    {
        "key": "infosys",
        "name": "Infosys",
        "context": (
            "a global IT services company whose interview preparation "
            "commonly emphasizes technology fundamentals, structured problem "
            "solving, and client-focused communication"
        ),
        "focus_areas": ["technology fundamentals", "structured problem solving", "communication"],
        "short": "Emphasizes technology fundamentals and structured problem solving.",
    },
    {
        "key": "accenture",
        "name": "Accenture",
        "context": (
            "a global professional services and consulting company whose "
            "interview preparation commonly emphasizes structured problem "
            "solving, technology consulting, and client communication"
        ),
        "focus_areas": ["structured problem solving", "technology consulting", "client communication"],
        "short": "Emphasizes structured problem solving and consulting skills.",
    },
]

_PRESETS_BY_KEY = {preset["key"]: preset for preset in COMPANY_PRESETS}

GENERAL_KEY = "general"


def company_preset_by_key(key):
    """Return the preset dict for an allowlisted key, or None otherwise.

    None is the safe outcome for empty, "general", unknown, or tampered
    values — the precise key is only ever matched against the static
    allowlist, so no unvalidated string can become a company context.
    """
    if not key:
        return None
    return _PRESETS_BY_KEY.get(str(key).strip().lower())


def company_context_for(preset):
    """Structured, bounded company context for the AI layer.

    Composed exclusively from the static preset fields — never from request
    input — so a prompt can only ever receive allowlisted content.
    """
    return {
        "key": preset["key"],
        "name": preset["name"],
        "context": preset["context"],
        "focus_areas": list(preset["focus_areas"]),
    }


def company_select_options():
    """Presets as (key, name, short) rows for rendering the config selector."""
    return [
        {"key": preset["key"],
         "name": preset["name"],
         "short": preset["short"]}
        for preset in COMPANY_PRESETS
    ]


def resolve_company_key(form_value, interview=None):
    """Validate a submitted company key against the server allowlist.

    Returns `(company_key, preset_or_None)`:
      * blank/"general" -> (None, None) — General / No Company (default, and
        identical to pre-Phase-10 behavior);
      * an allowlisted key -> (key, preset);
      * any other value raises ValueError so callers can reject it before it
        reaches Gemini or the database.

    When the submitted value is blank and an `interview` row is supplied, its
    stored allowlisted company key is used instead, so a continued session
    keeps its company context without trusting the browser again.
    """
    raw = (form_value or "").strip().lower()
    if not raw and interview is not None:
        # sqlite3.Row (and dict) both expose keys(); never call .get() on Row.
        if hasattr(interview, "keys") and "company_key" in interview.keys():
            raw = str(interview["company_key"] or "").strip().lower()
    if not raw or raw == GENERAL_KEY:
        return None, None
    preset = company_preset_by_key(raw)
    if preset is None:
        raise ValueError("invalid company key")
    return preset["key"], preset