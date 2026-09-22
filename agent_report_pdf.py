from __future__ import annotations

import io
import os
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_FONT_READY = False


def _register_fonts() -> tuple[str, str]:
    global _FONT_READY
    regular_name = "ReportSans"
    bold_name = "ReportSansBold"
    if _FONT_READY:
        return regular_name, bold_name

    candidates = [
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
        (
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ),
        (
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ),
    ]
    for regular, bold in candidates:
        if os.path.exists(regular) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont(regular_name, regular))
            pdfmetrics.registerFont(TTFont(bold_name, bold))
            _FONT_READY = True
            return regular_name, bold_name
    raise RuntimeError(
        "Не найден шрифт с поддержкой кириллицы. Установите fonts-dejavu-core "
        "или fonts-liberation2."
    )


def _rub(kop: int) -> str:
    return f"{int(kop) / 100:,.2f}".replace(",", " ") + " руб."


def _p(text: object, style):
    value = str(text if text is not None else "")
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(value, style)


def build_report_pdf(report: dict, items: Iterable[dict]) -> bytes:
    regular, bold = _register_fonts()
    styles = getSampleStyleSheet()
    normal = ParagraphStyle(
        "ReportNormal",
        parent=styles["Normal"],
        fontName=regular,
        fontSize=8.5,
        leading=11,
        spaceAfter=2,
    )
    small = ParagraphStyle(
        "ReportSmall",
        parent=normal,
        fontSize=7.5,
        leading=9.5,
    )
    title = ParagraphStyle(
        "ReportTitle",
        parent=normal,
        fontName=bold,
        fontSize=13,
        leading=16,
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    section = ParagraphStyle(
        "ReportSection",
        parent=normal,
        fontName=bold,
        fontSize=9,
        leading=11,
    )
    right = ParagraphStyle(
        "ReportRight",
        parent=normal,
        alignment=TA_RIGHT,
    )
    right_bold = ParagraphStyle(
        "ReportRightBold",
        parent=right,
        fontName=bold,
    )

    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=10 * mm,
        leftMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=f"Отчет агента {report['report_key']}",
        author=str(report.get("agent_name") or ""),
    )

    story = [
        _p("ОТЧЕТ АГЕНТА О ПРОВЕДЕННЫХ ЗАНЯТИЯХ", title),
        _p(f"№ {report['report_key']}", section),
        _p(f"Отчетный период: {report['period_label']}", normal),
        Spacer(1, 2 * mm),
    ]

    parties = [
        [
            _p("Агент", section),
            _p(report.get("agent_name") or "-", normal),
            _p(f"ИНН {report.get('agent_inn') or '-'}", normal),
        ],
        [
            _p("Принципал", section),
            _p(report.get("tutor_name") or "-", normal),
            _p(f"ИНН {report.get('tutor_inn') or '-'}", normal),
        ],
    ]
    party_table = Table(parties, colWidths=[30 * mm, 130 * mm, 65 * mm])
    party_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.extend([party_table, Spacer(1, 4 * mm)])

    header = [
        "№",
        "Дата",
        "Время",
        "Ученик",
        "Предмет",
        "Результат",
        "Стоимость",
        "Комиссия",
        "Вознаграждение агента",
        "К перечислению",
    ]
    rows = [[_p(value, section) for value in header]]
    for index, item in enumerate(items, start=1):
        rows.append([
            _p(index, normal),
            _p(item.get("lesson_date") or "", normal),
            _p(item.get("time_slot") or "", normal),
            _p(item.get("student_name") or "", small),
            _p(item.get("subject") or "", small),
            _p(item.get("outcome") or "", small),
            _p(_rub(item.get("gross_kop") or 0), right),
            _p(f"{float(item.get('commission_percent') or 0):g}%", right),
            _p(_rub(item.get("commission_kop") or 0), right),
            _p(_rub(item.get("tutor_kop") or 0), right),
        ])

    lesson_table = Table(
        rows,
        repeatRows=1,
        colWidths=[
            8 * mm,
            20 * mm,
            22 * mm,
            42 * mm,
            35 * mm,
            42 * mm,
            25 * mm,
            18 * mm,
            31 * mm,
            28 * mm,
        ],
    )
    lesson_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDEDED")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#777777")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.extend([lesson_table, Spacer(1, 5 * mm)])

    adjustment = int(report.get("commission_adjustment_kop") or 0)
    summary_rows = [
        [
            _p("Количество засчитанных занятий", normal),
            _p(report.get("lessons_count") or 0, right_bold),
        ],
        [
            _p("Стоимость услуг за период", normal),
            _p(_rub(report.get("gross_kop") or 0), right_bold),
        ],
    ]
    if adjustment:
        sign = "+" if adjustment > 0 else "-"
        summary_rows.append([
            _p("Корректировка агентского вознаграждения за период 1-15", normal),
            _p(f"{sign}{_rub(abs(adjustment))}", right_bold),
        ])
    summary_rows.extend([
        [
            _p("Агентское вознаграждение", normal),
            _p(_rub(report.get("commission_kop") or 0), right_bold),
        ],
        [
            _p("К перечислению принципалу", section),
            _p(_rub(report.get("tutor_kop") or 0), right_bold),
        ],
    ])
    summary = Table(summary_rows, colWidths=[170 * mm, 50 * mm], hAlign="RIGHT")
    summary.setStyle(TableStyle([
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.extend([
        summary,
        Spacer(1, 6 * mm),
        _p(
            "Отчет сформирован автоматически на основании зафиксированных в системе "
            "занятий и их финансовых параметров. Бесплатные пробные занятия в расчет не включаются.",
            small,
        ),
        _p(f"Дата формирования: {report.get('created_label') or '-'}", small),
    ])

    doc.build(story)
    return output.getvalue()
