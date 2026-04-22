import csv
import io
import json
from typing import Any, Dict, List

import pandas as pd


XRAY_COLUMNS = [
    "Summary",
    "Test Type",
    "Priority",
    "Precondition",
    "Labels",
    "Manual Test Step (Action)",
    "Manual Test Step (Data)",
    "Manual Test Step (Expected Result)"
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


def to_xray_csv_bytes(result: Dict[str, Any]) -> bytes:
    result = normalize_result(result)

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=XRAY_COLUMNS,
        delimiter=";",
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n"
    )

    writer.writeheader()

    for case in result.get("test_cases", []):
        steps = case.get("steps", []) or [
            {
                "action": "",
                "data": "",
                "expected_result": ""
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
                    "Manual Test Step (Expected Result)": step.get("expected_result", "")
                }
            )

    return output.getvalue().encode("utf-8-sig")


def to_json_bytes(result: Dict[str, Any]) -> bytes:
    return json.dumps(
        result,
        ensure_ascii=False,
        indent=2
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
                    "Expected Result": step.get("expected_result", "")
                }
            )

    return rows


def test_cases_to_dataframe(result: Dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(test_cases_to_rows(result))


def _escape_md(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
