"""PDF Interview Reports (Stage 6 Item 12, blueprint Sections C/D/E/K.12).

Reports are generated ON DEMAND and streamed straight to the browser —
nothing is persisted: no `reports` table exists in Section F, and no
generated PDF is written to disk as application data. Each request:

1. loads one COMPLETED interview owned by the signed-in user (foreign ids
   are 404; sessions still in progress redirect to where they left off,
   matching history/replay behaviour),
2. assembles interview + evaluation data already stored by earlier stages
   (Questions/Answers/dimension scores via get_transcript),
3. asks Gemini for a short narrative through the EXISTING
   generate_report_narrative contract — reused verbatim; if the AI layer is
   unavailable or fails, the report is still produced, just without the
   coach summary section,
4. renders a ReportLab PDF into memory and streams it as an attachment.

Reports contain only the owner's own practice data — never credentials or
API keys. A build failure flashes a safe error instead of streaming a
partial file.
"""

import io
from datetime import datetime

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    url_for,
)
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .ai.errors import GeminiConfigError, GeminiError, GeminiRateLimitError
from .auth import login_required
from .evaluation import SCORE_DIMENSIONS
from .interview import INTERVIEW_TYPES
from .models import get_interview, get_open_question, get_transcript, list_completed_interviews
from .ratelimit import check_gemini_limit

reports_bp = Blueprint("reports", __name__)

ANSWER_PDF_MAX_CHARS = 4000      # keep long answers from blowing up pages


@reports_bp.route("/reports", methods=["GET"])
@login_required
def index():
    interviews = list_completed_interviews(g.user["id"])
    return render_template(
        "reports.html",
        active_page="reports",
        interviews=interviews,
        type_labels=INTERVIEW_TYPES,
    )


@reports_bp.route("/reports/<int:interview_id>.pdf", methods=["GET"])
@login_required
def download(interview_id):
    # The narrative is an AI call: Section J's ~20/min/user cap applies.
    check_gemini_limit()

    interview = get_interview(interview_id)
    if interview is None or interview["user_id"] != g.user["id"]:
        abort(404)

    if interview["status"] != "completed":
        if interview["mode"] == "real":
            return redirect(url_for("interview.live",
                                    interview_id=interview_id))
        open_question = get_open_question(interview_id)
        if open_question is not None:
            return redirect(url_for("practice.question_view",
                                    question_id=open_question["id"]))
        return redirect(url_for(".index"))

    transcript = get_transcript(interview_id)
    averages = _dimension_averages(transcript)
    narrative = _narrative(interview, transcript, averages)

    try:
        pdf_bytes = build_report_pdf(
            interview=interview,
            candidate_name=g.user["name"],
            type_label=_type_label(interview),
            transcript=transcript,
            averages=averages,
            narrative=narrative,
        )
    except Exception:
        # Never stream a half-built file; surface one safe message.
        current_app.logger.warning("Report PDF build failed for interview %s",
                                   interview_id)
        flash("The report could not be generated right now. Please try again.",
              "error")
        return redirect(url_for(".index"))

    filename = f"interviewiq-report-{interview_id}.pdf"
    return (
        pdf_bytes,
        200,
        {
            "Content-Type": "application/pdf",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# PDF assembly (pure — unit-testable without HTTP)
# ---------------------------------------------------------------------------

def build_report_pdf(interview, candidate_name, type_label, transcript,
                     averages, narrative):
    """Render the full report to PDF bytes entirely in memory."""
    buffer = io.BytesIO()
    styles = _styles()
    story = []

    story.append(Paragraph("InterviewIQ", styles["brand"]))
    story.append(Paragraph("Interview Practice Report", styles["title"]))
    story.append(Spacer(1, 6))

    meta_rows = [
        ["Candidate", candidate_name],
        ["Role", interview["role"] or "\u2014"],
        ["Session", "Real interview" if interview["mode"] == "real"
                    else "Smart practice"],
        ["Type / topic", type_label],
        ["Date", (interview["date"] or "")[:16]],
        [
            "Overall score",
            f"{interview['overall_score']:g}/100"
            if interview["overall_score"] is not None else "Not graded",
        ],
    ]
    meta_table = Table(meta_rows, colWidths=[110, 300])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5b6472")),
        ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#1d2430")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(meta_table)

    graded = sum(1 for row in transcript if row["user_answer"] is not None)
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"{graded} of {len(transcript)} questions answered and graded. "
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}.",
        styles["muted"],
    ))
    story.append(Spacer(1, 14))

    if narrative:
        story.append(Paragraph("Coach summary", styles["section"]))
        story.append(Paragraph(_escape(narrative), styles["body"]))
        story.append(Spacer(1, 12))

    if averages:
        story.append(Paragraph("Dimension averages", styles["section"]))
        dimension_rows = [["Dimension", "Average"]]
        for key, label in SCORE_DIMENSIONS:
            if key in averages:
                dimension_rows.append([label, f"{averages[key]:g}"])
        dimension_table = Table(dimension_rows, colWidths=[220, 90])
        dimension_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9.5),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d7dbe2")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(dimension_table)
        story.append(Spacer(1, 14))

    story.append(Paragraph("Questions and answers", styles["section"]))
    if not transcript:
        story.append(Paragraph(
            "No questions were recorded for this session.", styles["body"]
        ))
    for index, row in enumerate(transcript, start=1):
        score_label = (
            f" \u2014 score {row['score']:g}/100"
            if row["score"] is not None else ""
        )
        story.append(Paragraph(
            f"Question {index}"
            + (f" ({row['question_type']})" if row["question_type"] else "")
            + score_label,
            styles["question"],
        ))
        story.append(Paragraph(_escape(row["question"]), styles["body"]))
        if row["user_answer"] is not None:
            story.append(Paragraph("Your answer", styles["label"]))
            story.append(Paragraph(
                _escape(row["user_answer"][:ANSWER_PDF_MAX_CHARS]),
                styles["body"],
            ))
            if row.get("feedback"):
                story.append(Paragraph("Feedback", styles["label"]))
                story.append(Paragraph(_escape(row["feedback"]), styles["body"]))
            missing = row.get("missing_points") or []
            if missing:
                story.append(Paragraph("Missing points", styles["label"]))
                for point in missing:
                    story.append(
                        Paragraph(f"\u2022 {_escape(str(point))}",
                                  styles["bullet"])
                    )
            if row.get("model_answer"):
                story.append(Paragraph("Model answer", styles["label"]))
                story.append(Paragraph(
                    _escape(row["model_answer"][:ANSWER_PDF_MAX_CHARS]),
                    styles["body"],
                ))
        else:
            story.append(Paragraph("Not answered.", styles["muted"]))
        story.append(Spacer(1, 10))

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title=f"InterviewIQ Report \u2014 session {interview['id']}",
        author="InterviewIQ",
    )
    doc.build(story)
    return buffer.getvalue()


def _narrative(interview, transcript, averages):
    """Coach summary via the existing contract; None on any AI failure."""
    graded_scores = [row["score"] for row in transcript
                     if row["score"] is not None]
    summary = {
        "role": interview["role"],
        "session": "real interview" if interview["mode"] == "real"
                   else "smart practice",
        "type": _type_label(interview),
        "overall_score": interview["overall_score"],
        "questions_total": len(transcript),
        "questions_graded": len(graded_scores),
        "average_dimension_scores": averages,
    }
    service = current_app.extensions["gemini"]
    try:
        payload = service.generate(
            "generate_report_narrative", {"summary": summary}
        )
    except (GeminiConfigError, GeminiRateLimitError, GeminiError):
        return None
    return str(payload.get("narrative_summary") or "").strip() or None


def _dimension_averages(transcript):
    totals = {}
    for row in transcript:
        for key, value in (row["scores"] or {}).items():
            totals.setdefault(key, []).append(value)
    return {
        key: round(sum(values) / len(values), 1)
        for key, values in totals.items()
    }


def _type_label(interview):
    if interview["mode"] == "real":
        return INTERVIEW_TYPES.get(interview["type"], interview["type"])
    return interview["type"]


def _escape(value):
    from xml.sax.saxutils import escape as sax_escape

    return sax_escape(str(value))


def _styles():
    base = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle(
            "Brand", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=11, textColor=colors.HexColor("#3452d9"),
        ),
        "title": ParagraphStyle(
            "TitleX", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=19, alignment=TA_CENTER, spaceAfter=10,
        ),
        "section": ParagraphStyle(
            "SectionX", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=13, spaceBefore=8, spaceAfter=4,
            textColor=colors.HexColor("#1d2430"),
        ),
        "question": ParagraphStyle(
            "QuestionX", parent=base["Heading3"], fontName="Helvetica-Bold",
            fontSize=10.5, spaceBefore=4, spaceAfter=2,
        ),
        "label": ParagraphStyle(
            "LabelX", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=9, textColor=colors.HexColor("#5b6472"),
            spaceBefore=4, spaceAfter=1,
        ),
        "body": ParagraphStyle(
            "BodyX", parent=base["BodyText"], fontSize=9.5, leading=13,
            spaceAfter=2,
        ),
        "bullet": ParagraphStyle(
            "BulletX", parent=base["BodyText"], fontSize=9.5, leading=13,
            leftIndent=12, spaceAfter=1,
        ),
        "muted": ParagraphStyle(
            "MutedX", parent=base["Normal"], fontSize=8.5, leading=12,
            textColor=colors.HexColor("#5b6472"),
        ),
    }
