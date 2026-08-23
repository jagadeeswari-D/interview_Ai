# InterviewIQ — Final Refined Technical Blueprint
*AI-Powered Mock Interview System — Capstone Source of Truth*

---

## 0. Review Summary (What Changed From the Kimi Draft and Why)

The original blueprint is solid and internally consistent — the two-mode split, the Gemini-centric AI layer, and the 12-stage roadmap are all workable for a student team. The refinements below are corrections and risk-reduction moves, not a redesign.

| # | Area | Issue in Original | Fix Applied |
|---|------|-------------------|-------------|
| 1 | Adaptive follow-up | Described as "AI decides," but no concrete mechanism given | Added an explicit decision procedure (extraction → scoring → prompt template) in Section H |
| 2 | Evaluation JSON | No schema defined, risk of inconsistent Gemini output | Added a fixed JSON contract for every Gemini call type |
| 3 | Interview Memory table | `strong_topics`/`weak_topics` stored as flat text — hard to query for trends | Normalized into the `Weaknesses`/`Performance` tables that already exist; `InterviewMemory` becomes a derived summary cache, not primary storage |
| 4 | Real Interview Mode timer | "Timer enabled" with no behavior on expiry | Defined explicit timeout behavior (auto-submit) |
| 5 | Resume parsing | No fallback for scanned/image PDFs | Added explicit scope limit: text-based PDFs only, reject or warn on scanned files (OCR excluded — scope control) |
| 6 | Readiness Score | Formula undefined | Added a transparent, explainable weighted formula instead of a black-box AI score |
| 7 | Security | Rate limiting called "basic" with no target | Specified concrete limits per endpoint type |
| 8 | Session-level vs cross-session memory | Conflated in original | Split explicitly: session context (in-request, ephemeral) vs cross-session memory (persisted, used only for practice/roadmap prioritization, never mid-interview) |
| 9 | Scope creep risk | Voice interview, gamification, AI interviewer voice listed as "optional" without a hard boundary | Added explicit Section L (Scope Control) with a "do not build in v1" list |
| 10 | Novelty claims | "Novelty" section used strong language | Reframed as integration/positioning claim, not a research novelty claim — Gemini is an orchestrated third-party service, not custom ML |

Everything else (stack, page list, must-have feature set, phase structure) is preserved as-is because it was already appropriately scoped for a 12-week student capstone.

---

## A. Final Project Architecture

```
┌─────────────────────────────────────────────────────────┐
│  PRESENTATION LAYER (HTML5 / CSS3 / vanilla JS)          │
│  - Sidebar SPA-style navigation (multi-page, JS-enhanced)│
│  - Fetch-based calls to Flask REST API                   │
│  - Chart.js for analytics, CSS variables for theming     │
└───────────────────────────┬───────────────────────────────┘
                             │ REST (JSON over HTTPS)
┌───────────────────────────▼───────────────────────────────┐
│  BACKEND API LAYER (Python + Flask)                       │
│  - Auth (session-based), route handlers, validation        │
│  - Business logic: practice engine, interview engine,      │
│    evaluation orchestration, roadmap generation            │
│  - Calls out to Gemini; persists to SQLite                 │
└──────────┬───────────────────────────────┬─────────────────┘
           │                               │
┌──────────▼──────────┐         ┌──────────▼──────────────┐
│  AI LAYER            │         │  DATA LAYER              │
│  Google Gemini API    │         │  SQLite                  │
│  (Gemini 2.5 Flash)   │         │  Users, Profiles,        │
│  - Question gen        │         │  Interviews, Questions,  │
│  - Follow-up gen       │         │  Answers, Performance,   │
│  - Evaluation (rubric) │         │  Weaknesses, Roadmaps,   │
│  - Roadmap gen         │         │  InterviewMemory         │
│  - Resume parsing input│         └───────────────────────────┘
└────────────────────────┘
           │
┌──────────▼──────────────┐   ┌───────────────────────────┐
│ SUPPORT SERVICES          │   │ REPORTING                 │
│ PyPDF2/pdfplumber (resume)│   │ ReportLab (PDF reports)   │
│ Web Speech API (optional, │   │ Chart.js (client-side)    │
│ browser-side only)        │   └───────────────────────────┘
└────────────────────────────┘
```

**Data flow contract:** Frontend never talks to Gemini directly. Every AI call is proxied through Flask so the API key stays server-side and every AI response can be validated/normalized before it reaches the client or the database.

---

## B. Complete Project Workflow

1. **Register/Login** → session created → redirected to Dashboard.
2. **Profile Setup** (can be done later, not blocking): target role, skills, optional resume upload.
3. **Dashboard**: readiness score, recent activity, quick actions into either mode.
4. **Smart Practice Mode**: pick topic → Gemini generates question → user answers → immediate rubric-based evaluation → hints/model answer available → retry or move to next topic. Every attempt is logged to `Answers`/`Performance`.
5. **AI Real Interview Mode**: configure role/type/difficulty → timer starts → Gemini asks opening question → user answers → Gemini extracts key entities from the answer → generates a contextual follow-up → repeats for N questions or until time expires → full evaluation is computed and shown only at the end.
6. **AI Evaluation Engine**: every answer (both modes) is scored on 5 dimensions; results feed weakness detection.
7. **Weakness Detection**: aggregates low-scoring dimensions/topics across the session and historical data into the `Weaknesses` table.
8. **Personalized Learning Roadmap**: weak topics converted into a day-by-day plan; each day links back to Smart Practice questions on that topic.
9. **Re-attempt**: user works the roadmap, retries practice questions, optionally repeats a full Real Interview to check improvement — memory prioritizes previously weak areas.
10. **Performance Analytics**: trend charts, skill heatmap, readiness score recalculated after every interview.
11. **Interview Replay**: any past interview can be opened question-by-question with feedback and model answers.
12. **PDF Report**: generated on demand per interview via ReportLab, downloadable.

This closes the loop: **Practice → Interview → Evaluation → Weakness Detection → Learning → Re-attempt → Tracking.**

---

## C. Final Feature Matrix

### MUST HAVE (MVP)
- Registration / login / logout / session management
- Profile setup (role, skills)
- Smart Practice Mode (topic selection, Gemini question gen, immediate evaluation, hints, model answer, retry)
- AI Real Interview Mode (config, timer, sequential adaptive Q&A, no hints, post-interview evaluation)
- Adaptive follow-up questions (content-aware, not random)
- AI Evaluation Engine (5-dimension rubric)
- Strength/weakness analysis
- Interview history (list + detail)
- Performance dashboard (basic charts)
- Core UI shell: sidebar nav, responsive layout, light/dark theme variables

### ADVANCED / KILLER
- Cross-session AI Interview Memory (weak/strong topic persistence, repeated-mistake detection)
- Personalized Learning Roadmap (day-by-day, linked to practice)
- Interview Replay (full Q→A→feedback→model-answer walkthrough)
- Resume-based interview (PDF parsing → targeted questions)
- Placement Readiness Score (transparent weighted formula, explicitly labeled as self-assessment)
- PDF Interview Report (ReportLab)
- Long-term performance trend charts / skill heatmap

### OPTIONAL (only if MVP + Advanced are done early)
- Voice interview via Web Speech API (browser-only, no server-side speech processing)
- AI interviewer "voice" (text-to-speech playback of questions)
- Company-specific question presets
- Gamification (XP, streaks, achievements)

**Rule preserved from source doc:** optional features must never delay or compromise the MVP + Advanced tiers.

---

## D. Main Components

| Component | Responsibility |
|---|---|
| Authentication Service | Register/login/logout, password hashing, session lifecycle, timeout |
| Profile Manager | Stores role/skills/resume path, feeds context into question generation |
| Practice Engine | Orchestrates Smart Practice: topic → Gemini question → evaluation → hint/model-answer/retry |
| Interview Agent | Orchestrates Real Interview: config → timed sequential Q&A → adaptive follow-up calls → aggregated post-interview evaluation |
| Evaluation Engine | Sends answer + rubric to Gemini, parses fixed-schema JSON, normalizes scores, stores results |
| Weakness/Memory Engine | Aggregates evaluation history into weak/strong topics and repeated-mistake flags |
| Learning Recommendation Engine | Converts current weaknesses into a day-by-day roadmap, links roadmap days to practice topics |
| Performance Analytics | Computes trends, readiness score, skill heatmap data for Chart.js |
| Interview Replay Service | Reconstructs a stored interview session into a navigable Q→A→feedback view |
| Resume Analysis Service | Extracts text via PyPDF2/pdfplumber, sends to Gemini for skill/project/tech extraction |
| Reporting Service | Assembles interview + evaluation data into a ReportLab PDF |

---

## E. Website Pages

**Public**
- **Landing Page** — product pitch, how it works, CTA to register
- **Login** / **Register** — auth forms with validation states

**Authenticated (sidebar nav)**
- **Dashboard** — readiness score, stat cards, recent activity, sparkline, quick actions into Practice/Interview
- **Smart Practice** — topic picker, question panel, answer input, instant feedback panel, hint/model-answer toggles
- **Real Interview** — config screen → timed interview screen (question, timer, answer box, progress) → completion screen
- **Interview History** — filterable list of past sessions (mode, date, score, role)
- **Interview Replay** — per-question navigation: question / your answer / AI feedback / missing points / model answer
- **Resume Analysis** — upload, extracted skills/projects preview, "generate questions from this" action
- **Learning Roadmap** — day-by-day plan, progress checkboxes, links to relevant practice topics
- **Performance Analytics** — trend charts, skill heatmap, readiness score breakdown
- **Reports** — list of generated PDF reports, download links
- **Profile** — personal info, target role, skills, resume management
- **Settings** — theme toggle, account/session management

---

## F. Database Design

| Table | Key Fields | Relationships |
|---|---|---|
| Users | id, name, email, password_hash | 1—1 Profiles |
| Profiles | user_id, role, skills, resume_path | belongs to Users |
| Interviews | id, user_id, mode, role, difficulty, type, date, overall_score, status | belongs to Users; has many Questions |
| Questions | id, interview_id, question, question_type, sequence_order | belongs to Interviews; has one Answer |
| Answers | id, question_id, user_answer, score, feedback, missing_points, model_answer | belongs to Questions |
| Performance | user_id, skill, score, date, interview_id | belongs to Users + Interviews |
| Weaknesses | user_id, skill, frequency, last_interview_id | belongs to Users |
| Roadmaps | user_id, skill, day_number, topic, status, created_at | belongs to Users |
| InterviewMemory | user_id, strong_topics (derived), weak_topics (derived), repeated_mistakes, updated_at | **derived cache**, recomputed from Performance + Weaknesses — not a source of truth on its own |

**Note on InterviewMemory:** treat it as a materialized summary refreshed after each interview, not as the primary record — this avoids the two tables drifting out of sync, which was a latent bug risk in the original design.

---

## G. Gemini AI Workflow

Every call goes through the Flask backend. All calls request strict JSON output with a fixed schema so the frontend never has to parse free text.

| Task | Input | Output Contract (JSON keys) |
|---|---|---|
| Question generation | role, topic, difficulty | `question`, `question_type`, `expected_concepts[]` |
| Adaptive follow-up | previous question, previous answer, extracted entities | `follow_up_question`, `reasoning` (internal only, not shown to user) |
| Answer evaluation | question, user answer, expected_concepts | `scores: {technical_accuracy, relevance, completeness, clarity, communication}`, `feedback`, `missing_points[]`, `model_answer` |
| Weakness detection | aggregated evaluation results | `weak_skills[]`, `strong_skills[]` |
| Roadmap generation | weak_skills[] | `roadmap: [{day, topic, practice_focus}]` |
| Resume analysis | extracted resume text | `skills[]`, `projects[]`, `technologies[]`, `certifications[]` |
| Report narrative | interview summary data | `narrative_summary` (short paragraph for the PDF) |

Every response is validated against its schema server-side before being stored or returned; malformed responses trigger a single retry, then a graceful error state on the frontend.

---

## H. Adaptive Interview Logic

This was the vaguest part of the original draft, so it's made concrete here:

1. **Entity extraction**: after the user submits an answer, the backend sends the answer text to Gemini asking it to extract mentioned technologies, projects, and claims (e.g., "Flask," "built a recommendation engine," "led a team of 3").
2. **Relevance scoring**: extracted entities are compared against the role/topic context to pick the most interview-worthy mention (prioritize project/technology mentions over generic statements).
3. **Follow-up prompt construction**: the backend builds a follow-up prompt containing (a) the original question, (b) the user's answer, (c) the selected entity, and asks Gemini for one natural follow-up question that probes depth on that entity.
4. **Depth adjustment**: if the previous answer scored low on technical_accuracy, the next follow-up is generated with an instruction to ask a slightly more foundational question on the same topic; if it scored high, the instruction asks for a deeper/edge-case question. This is the "adaptive depth" mechanism — a prompt-level instruction, not a custom model.
5. **Session memory**: within one interview session, the last 2–3 Q&A pairs are kept in the prompt context so follow-ups stay coherent; this is ephemeral and not persisted beyond the session record already saved in `Questions`/`Answers`.
6. **Timer expiry behavior**: if the timer runs out mid-answer, the current answer is auto-submitted as-is and the interview moves to evaluation — this must be explicit so it doesn't need to be improvised during implementation.

---

## I. UI/UX Design System

- **Visual direction**: professional SaaS aesthetic — generous whitespace, 8–16px spacing scale, 10px border-radius, subtle 1px borders instead of heavy shadows.
- **Theming**: CSS custom properties for all colors/spacing; dark/light mode toggled by swapping a root class, not duplicating stylesheets.
- **Typography**: one clean sans-serif for UI text, a distinct (but not decorative) font or weight for AI-generated question/feedback text so it reads as "the coach speaking."
- **Component library** (reusable, hand-built — no framework needed at this scale): buttons, cards, stat tiles, progress bars, badges, modal, toast/notification, skeleton loaders, empty-state illustrationless placeholders, timer ring.
- **Mode differentiation**: Practice Mode uses a calmer, tutor-like layout (question + answer + feedback panel side-by-side); Real Interview Mode uses a focused, minimal, timer-forward layout to create realistic pressure — this distinction should be visually obvious at a glance, per the requirement.
- **States to design for every async action**: loading (skeleton or spinner with a short "AI is thinking…" microcopy), empty (first-time user guidance), error (retry affordance), success (subtle confirmation, not intrusive).
- **Accessibility**: semantic HTML, sufficient color contrast in both themes, keyboard navigability for forms and the interview flow, ARIA live regions for AI feedback appearing asynchronously.
- **Micro-interactions**: kept CSS-only where possible (transitions, hover/focus states, progress fills) — no animation library required, keeping the stack justified.

---

## J. Security

| Concern | Implementation |
|---|---|
| Passwords | Hashed with bcrypt/argon2, never stored or logged in plaintext |
| Sessions | Server-side session store, secure/HttpOnly cookies, idle timeout |
| Input validation | Server-side validation/sanitization on every form and API input |
| XSS | Escape all rendered output; Content-Security-Policy headers |
| CSRF | CSRF tokens on all state-changing POST/PUT/DELETE routes |
| File upload | PDF-only for resumes, strict MIME + magic-byte check, file size cap (e.g., 5MB), stored outside the web root |
| API keys | Gemini key in environment variables/secret config, never exposed client-side or in version control |
| SQL injection | Parameterized queries / ORM only, no string-built SQL |
| Rate limiting | Concrete targets: auth endpoints ~5 requests/min/IP; Gemini-backed endpoints ~20 requests/min/user, to control both abuse and API cost |

---

## K. 12-Stage Implementation Roadmap

**Phase 1 — Foundation (Weeks 1–2)**
1. Project setup & authentication: Flask structure, venv, Git, SQLite schema (Users, Profiles), register/login/logout, landing page.
2. Core UI shell: sidebar layout, routing, responsive CSS variables, dashboard placeholders.

**Phase 2 — AI Integration (Weeks 3–4)**
3. Gemini API integration: client setup, prompt templates, JSON schema parsing, error handling.
4. Smart Practice Mode (MVP): topic selection, question display, answer input, evaluation display, hints, model answer, retry.

**Phase 3 — Interview Engine (Weeks 5–6)**
5. AI Real Interview Mode: config screen, timer (with expiry auto-submit), sequential Q&A, post-interview evaluation, hint/model-answer suppression.
6. Adaptive follow-up system: entity extraction, follow-up prompt construction, session-level context window.

**Phase 4 — Intelligence Layer (Weeks 7–8)**
7. AI Evaluation Engine: 5-dimension scoring, feedback/missing-points generation, score normalization and storage.
8. Interview Memory & History: Weaknesses table population, repeated-mistake detection, history list/detail views, InterviewMemory cache computation.

**Phase 5 — Personalization (Weeks 9–10)**
9. Interview Replay: replay UI with per-question navigation.
10. Personalized Learning Roadmap: weakness-to-roadmap generation, day-by-day UI, progress tracking, links back to practice.

**Phase 6 — Analytics & Polish (Weeks 11–12)**
11. Performance analytics & resume: Chart.js trend/skill charts, readiness score formula, resume upload/parsing, resume-based questions.
12. Reports & final polish: ReportLab PDF generation, UI/UX consistency pass, edge-case handling, testing, deployment prep.

---

## L. Scope Control — What NOT to Build in v1

To protect the MVP + Advanced tiers, explicitly defer:
- Voice interview and AI voice playback (Web Speech API) — browser support is inconsistent and it adds testing surface for no core-loop value.
- Gamification (XP, streaks, achievements) — purely retention polish, zero impact on the core evaluation loop.
- Company-specific presets — a content/data problem, not an architecture one; can be added as static config later.
- OCR for scanned resumes — text-based PDF parsing only; scanned/image resumes should be rejected with a clear message, not silently mishandled.
- Any custom ML model training — the project should not claim or attempt this; Gemini via API is the entire AI layer.
- Real-time bidirectional features (WebSockets) — the interview flow is request/response per question, not truly real-time, so standard REST calls are sufficient.

---

## M. Final Project Definition

**InterviewIQ** is a web-based AI interview coaching platform for students and fresh graduates that combines two complementary practice modes — a learning-focused **Smart Practice Mode** and a pressure-realistic **AI Real Interview Mode** — with a Gemini-powered evaluation engine that scores answers across five dimensions, detects recurring weaknesses over time, and converts them into a personalized day-by-day learning roadmap. Unlike fragmented tools that offer only question generation, only speech scoring, or only coding practice, InterviewIQ closes the loop end-to-end: **practice → simulate → evaluate → identify gaps → learn → re-attempt → track improvement** — positioning it as a continuous AI coach rather than a one-shot quiz generator, built achievably on a Flask + SQLite + Gemini stack suited to a single-semester student capstone.
