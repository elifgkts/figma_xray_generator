import csv
import io
import json
import os
from typing import Any, Dict, List
from xml.sax.saxutils import escape

import pandas as pd

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


XRAY_COLUMNS = [
    "Summary",
    "Test Type",
    "Priority",
    "Precondition",
    "Labels",
    "Manual Test Step (Action)",
    "Manual Test Step (Data)",
    "Manual Test Step (Expected Result)",
]


def normalize_result(result: Dict[str, Any]) -> Dict[str, Any]:
    result.setdefault("analysis_document", {})
    result.setdefault("test_cases", [])
    result.setdefault("generation_notes", [])

    analysis = result["analysis_document"]
    analysis.setdefault("title", "")
    analysis.setdefault("project_summary", "")
    analysis.setdefault("scope", "")
    analysis.setdefault("user_roles", [])
    analysis.setdefault("screens", [])
    analysis.setdefault("functional_requirements", [])
    analysis.setdefault("business_rules", [])
    analysis.setdefault("screen_flows", [])
    analysis.setdefault("open_questions", [])
    analysis.setdefault("qa_notes", [])

    return result


def to_markdown(result: Dict[str, Any]) -> str:
    result = normalize_result(result)

    analysis = result["analysis_document"]
    lines = []

    lines.append(f"# {analysis.get('title') or 'Sistem Analiz ve Gereksinim Dokümanı'}")
    lines.append("")

    lines.append("## 1. Proje Özeti")
    lines.append(analysis.get("project_summary", ""))
    lines.append("")

    lines.append("## 2. Kapsam")
    lines.append(analysis.get("scope", ""))
    lines.append("")

    lines.append("## 3. Kullanıcı Rolleri")
    for role in analysis.get("user_roles", []):
        lines.append(f"- {role}")
    lines.append("")

    lines.append("## 4. Ekran Analizi")
    for screen in analysis.get("screens", []):
        lines.append(f"### {screen.get('name', '')}")
        lines.append(f"**Amaç:** {screen.get('purpose', '')}")
        lines.append("")
        lines.append("**Görünen Elementler:**")
        for item in screen.get("visible_elements", []):
            lines.append(f"- {item}")
        lines.append("")
        lines.append("**Etkileşimler:**")
        for item in screen.get("interactions", []):
            lines.append(f"- {item}")
        lines.append("")

    lines.append("## 5. Fonksiyonel Gereksinimler")
    for req in analysis.get("functional_requirements", []):
        lines.append(f"### {req.get('id', '')} - {req.get('title', '')}")
        lines.append(req.get("description", ""))
        lines.append(f"**Kaynak Güveni:** {req.get('source_confidence', '')}")
        lines.append("")

    lines.append("## 6. İş Kuralları")
    for rule in analysis.get("business_rules", []):
        lines.append(
            f"- **{rule.get('id', '')}:** {rule.get('rule', '')} "
            f"_({rule.get('source_confidence', '')})_"
        )
    lines.append("")

    lines.append("## 7. Ekran Akışları")
    for flow in analysis.get("screen_flows", []):
        lines.append(f"### {flow.get('flow_name', '')}")
        for index, step in enumerate(flow.get("steps", []), start=1):
            lines.append(f"{index}. {step}")
        lines.append("")

    lines.append("## 8. Açık Noktalar / Analist Onayı Gerekenler")
    for question in analysis.get("open_questions", []):
        lines.append(f"- {question}")
    lines.append("")

    lines.append("## 9. QA Notları")
    for note in analysis.get("qa_notes", []):
        lines.append(f"- {note}")
    lines.append("")

    lines.append("## 10. Üretilen Test Case Özeti")
    for case in result.get("test_cases", []):
        lines.append(f"### {case.get('summary', '')}")
        lines.append(f"- **Priority:** {case.get('priority', '')}")
        lines.append(f"- **Precondition:** {case.get('precondition', '')}")
        lines.append(f"- **Source Confidence:** {case.get('source_confidence', '')}")
        lines.append("")
        lines.append("| Step | Action | Data | Expected Result |")
        lines.append("|---:|---|---|---|")

        for idx, step in enumerate(case.get("steps", []), start=1):
            action = _escape_md(step.get("action", ""))
            data = _escape_md(step.get("data", ""))
            expected = _escape_md(step.get("expected_result", ""))
            lines.append(f"| {idx} | {action} | {data} | {expected} |")

        lines.append("")

    lines.append("## 11. Üretim Notları")
    for note in result.get("generation_notes", []):
        lines.append(f"- {note}")
    lines.append("")

    return "\n".join(lines)


def to_pdf_bytes(result: Dict[str, Any]) -> bytes:
    result = normalize_result(result)
    analysis = result["analysis_document"]

    buffer = io.BytesIO()
    fonts = _register_pdf_fonts()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.6 * cm,
        leftMargin=1.6 * cm,
        topMargin=1.4 * cm,
        bottomMargin=1.4 * cm,
        title=analysis.get("title") or "Sistem Analiz ve Gereksinim Dokümanı",
    )

    styles = _build_pdf_styles(fonts)
    story = []

    title = analysis.get("title") or "Sistem Analiz ve Gereksinim Dokümanı"

    story.append(Paragraph(_xml(title), styles["Title"]))
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("1. Proje Özeti", styles["Heading1"]))
    story.append(Paragraph(_xml(analysis.get("project_summary", "")), styles["Body"]))
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph("2. Kapsam", styles["Heading1"]))
    story.append(Paragraph(_xml(analysis.get("scope", "")), styles["Body"]))
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph("3. Kullanıcı Rolleri", styles["Heading1"]))
    _add_bullets(story, analysis.get("user_roles", []), styles)
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph("4. Ekran Analizi", styles["Heading1"]))
    for screen in analysis.get("screens", []):
        story.append(Paragraph(_xml(screen.get("name", "")), styles["Heading2"]))

        purpose_text = f"Amaç: {screen.get('purpose', '')}"
        story.append(Paragraph(_xml(purpose_text), styles["Body"]))

        story.append(Paragraph("Görünen Elementler:", styles["SmallBold"]))
        _add_bullets(story, screen.get("visible_elements", []), styles)

        story.append(Paragraph("Etkileşimler:", styles["SmallBold"]))
        _add_bullets(story, screen.get("interactions", []), styles)

        story.append(Spacer(1, 0.2 * cm))

    story.append(Paragraph("5. Fonksiyonel Gereksinimler", styles["Heading1"]))
    for req in analysis.get("functional_requirements", []):
        heading = f"{req.get('id', '')} - {req.get('title', '')}"
        story.append(Paragraph(_xml(heading), styles["Heading2"]))
        story.append(Paragraph(_xml(req.get("description", "")), styles["Body"]))

        confidence = f"Kaynak Güveni: {req.get('source_confidence', '')}"
        story.append(Paragraph(_xml(confidence), styles["Small"]))

        story.append(Spacer(1, 0.2 * cm))

    story.append(Paragraph("6. İş Kuralları", styles["Heading1"]))
    for rule in analysis.get("business_rules", []):
        text = f"{rule.get('id', '')}: {rule.get('rule', '')} ({rule.get('source_confidence', '')})"
        story.append(Paragraph(f"• {_xml(text)}", styles["Body"]))
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph("7. Ekran Akışları", styles["Heading1"]))
    for flow in analysis.get("screen_flows", []):
        story.append(Paragraph(_xml(flow.get("flow_name", "")), styles["Heading2"]))

        for index, step in enumerate(flow.get("steps", []), start=1):
            story.append(Paragraph(f"{index}. {_xml(step)}", styles["Body"]))

        story.append(Spacer(1, 0.2 * cm))

    story.append(Paragraph("8. Açık Noktalar / Analist Onayı Gerekenler", styles["Heading1"]))
    _add_bullets(story, analysis.get("open_questions", []), styles)
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph("9. QA Notları", styles["Heading1"]))
    _add_bullets(story, analysis.get("qa_notes", []), styles)
    story.append(Spacer(1, 0.3 * cm))

    story.append(PageBreak())

    story.append(Paragraph("10. Üretilen Test Case Özeti", styles["Heading1"]))

    for case_index, case in enumerate(result.get("test_cases", []), start=1):
        story.append(
            Paragraph(
                f"{case_index}. {_xml(case.get('summary', ''))}",
                styles["Heading2"],
            )
        )

        meta_lines = [
            f"Priority: {case.get('priority', '')}",
            f"Precondition: {case.get('precondition', '')}",
            f"Confidence: {case.get('source_confidence', '')}",
        ]

        for meta in meta_lines:
            story.append(Paragraph(_xml(meta), styles["Small"]))

        story.append(Spacer(1, 0.15 * cm))

        table_data = [
            [
                Paragraph("Step", styles["TableHeader"]),
                Paragraph("Action", styles["TableHeader"]),
                Paragraph("Data", styles["TableHeader"]),
                Paragraph("Expected Result", styles["TableHeader"]),
            ]
        ]

        for step_index, step in enumerate(case.get("steps", []), start=1):
            table_data.append(
                [
                    Paragraph(str(step_index), styles["TableCell"]),
                    Paragraph(_xml(step.get("action", "")), styles["TableCell"]),
                    Paragraph(_xml(step.get("data", "")), styles["TableCell"]),
                    Paragraph(_xml(step.get("expected_result", "")), styles["TableCell"]),
                ]
            )

        table = Table(
            table_data,
            colWidths=[1.2 * cm, 5.0 * cm, 4.0 * cm, 6.3 * cm],
            repeatRows=1,
        )

        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#263238")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B0BEC5")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#FAFAFA")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )

        story.append(table)
        story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("11. Üretim Notları", styles["Heading1"]))
    _add_bullets(story, result.get("generation_notes", []), styles)

    doc.build(story)

    pdf_value = buffer.getvalue()
    buffer.close()

    return pdf_value


def to_xray_csv_bytes(result: Dict[str, Any]) -> bytes:
    result = normalize_result(result)

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=XRAY_COLUMNS,
        delimiter=";",
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
    )

    writer.writeheader()

    for case in result.get("test_cases", []):
        steps = case.get("steps", []) or [
            {
                "action": "",
                "data": "",
                "expected_result": "",
            }
        ]

        labels = case.get("labels", [])
        labels_text = ",".join(labels) if isinstance(labels, list) else str(labels)

        for step in steps:
            writer.writerow(
                {
                    "Summary": case.get("summary", ""),
                    "Test Type": case.get("test_type", "Manual"),
                    "Priority": case.get("priority", "Medium"),
                    "Precondition": case.get("precondition", ""),
                    "Labels": labels_text,
                    "Manual Test Step (Action)": step.get("action", ""),
                    "Manual Test Step (Data)": step.get("data", ""),
                    "Manual Test Step (Expected Result)": step.get("expected_result", ""),
                }
            )

    return output.getvalue().encode("utf-8-sig")


def to_json_bytes(result: Dict[str, Any]) -> bytes:
    return json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")


def test_cases_to_rows(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []

    for case_no, case in enumerate(result.get("test_cases", []), start=1):
        steps = case.get("steps", [])

        for step_no, step in enumerate(steps, start=1):
            rows.append(
                {
                    "Case #": case_no,
                    "Step #": step_no,
                    "Summary": case.get("summary", ""),
                    "Priority": case.get("priority", ""),
                    "Precondition": case.get("precondition", ""),
                    "Confidence": case.get("source_confidence", ""),
                    "Action": step.get("action", ""),
                    "Data": step.get("data", ""),
                    "Expected Result": step.get("expected_result", ""),
                }
            )

    return rows


def test_cases_to_dataframe(result: Dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(test_cases_to_rows(result))


def _register_pdf_fonts() -> Dict[str, str]:
    """
    Türkçe karakterler için fontları ayrı ayrı register eder.
    Font family mapping kullanmaz; doğrudan font adlarıyla çalışır.
    Bu yüzden ReportLab'in bold/italic mapping hatasına düşmez.
    """

    font_sets = [
        {
            "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        },
        {
            "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
            "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
        },
        {
            "regular": "C:/Windows/Fonts/arial.ttf",
            "bold": "C:/Windows/Fonts/arialbd.ttf",
        },
        {
            "regular": "C:/Windows/Fonts/calibri.ttf",
            "bold": "C:/Windows/Fonts/calibrib.ttf",
        },
        {
            "regular": "/Library/Fonts/Arial.ttf",
            "bold": "/Library/Fonts/Arial Bold.ttf",
        },
    ]

    for font_set in font_sets:
        regular_path = font_set["regular"]
        bold_path = font_set["bold"]

        if os.path.exists(regular_path) and os.path.exists(bold_path):
            regular_name = "AppUnicodeFont-Regular"
            bold_name = "AppUnicodeFont-Bold"

            registered = pdfmetrics.getRegisteredFontNames()

            if regular_name not in registered:
                pdfmetrics.registerFont(TTFont(regular_name, regular_path))

            if bold_name not in registered:
                pdfmetrics.registerFont(TTFont(bold_name, bold_path))

            return {
                "regular": regular_name,
                "bold": bold_name,
            }

    raise RuntimeError(
        "PDF için Türkçe karakter destekli font bulunamadı. "
        "Streamlit Cloud kullanıyorsan repo ana dizinine packages.txt ekleyip içine fonts-dejavu-core yaz."
    )


def _build_pdf_styles(fonts: Dict[str, str]) -> Dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    regular_font = fonts["regular"]
    bold_font = fonts["bold"]

    return {
        "Title": ParagraphStyle(
            "CustomTitle",
            parent=base["Normal"],
            fontName=bold_font,
            fontSize=18,
            leading=24,
            alignment=TA_CENTER,
            spaceAfter=14,
            textColor=colors.HexColor("#263238"),
        ),
        "Heading1": ParagraphStyle(
            "CustomHeading1",
            parent=base["Normal"],
            fontName=bold_font,
            fontSize=14,
            leading=18,
            spaceBefore=10,
            spaceAfter=8,
            textColor=colors.HexColor("#1A237E"),
        ),
        "Heading2": ParagraphStyle(
            "CustomHeading2",
            parent=base["Normal"],
            fontName=bold_font,
            fontSize=11,
            leading=14,
            spaceBefore=8,
            spaceAfter=5,
            textColor=colors.HexColor("#37474F"),
        ),
        "Body": ParagraphStyle(
            "CustomBody",
            parent=base["Normal"],
            fontName=regular_font,
            fontSize=9,
            leading=13,
            alignment=TA_LEFT,
            spaceAfter=5,
        ),
        "Small": ParagraphStyle(
            "CustomSmall",
            parent=base["Normal"],
            fontName=regular_font,
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#546E7A"),
            spaceAfter=5,
        ),
        "SmallBold": ParagraphStyle(
            "CustomSmallBold",
            parent=base["Normal"],
            fontName=bold_font,
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#37474F"),
            spaceBefore=4,
            spaceAfter=3,
        ),
        "TableHeader": ParagraphStyle(
            "CustomTableHeader",
            parent=base["Normal"],
            fontName=bold_font,
            fontSize=8,
            leading=10,
            textColor=colors.white,
        ),
        "TableCell": ParagraphStyle(
            "CustomTableCell",
            parent=base["Normal"],
            fontName=regular_font,
            fontSize=7.5,
            leading=10,
        ),
    }


def _add_bullets(story: list, items: List[str], styles: Dict[str, ParagraphStyle]) -> None:
    if not items:
        story.append(Paragraph("Belirtilmemiş.", styles["Body"]))
        return

    for item in items:
        story.append(Paragraph(f"• {_xml(item)}", styles["Body"]))


def _xml(value: Any) -> str:
    text = "" if value is None else str(value)
    text = escape(text, {"\"": "&quot;", "'": "&#39;"})
    text = text.replace("\n", "<br/>")
    return text


def _escape_md(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
