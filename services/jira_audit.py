from typing import Dict, List, Optional

import pandas as pd

from services.jira_parser import build_jira_context
from services.jira_quality_rules import (
    has_confluence_link,
    has_figma_link,
    has_description,
    has_acceptance_like_content,
    has_attachments,
    has_comments,
    description_quality,
    compute_readiness_score,
    build_missing_items,
    build_recommendation,
    is_risky_for_analysis,
)


DEFAULT_ISSUE_TYPES = ["Story", "Task"]
TEST_STATUSES = {"ready to test", "test"}


def build_audit_jql(
    project_key: str,
    start_date: str,
    end_date: str,
    issue_types: Optional[List[str]] = None,
) -> str:
    issue_types = issue_types or DEFAULT_ISSUE_TYPES
    types_str = ", ".join([f'"{x}"' for x in issue_types])

    return (
        f'project = "{project_key}" '
        f'AND issuetype in ({types_str}) '
        f'AND created >= "{start_date}" '
        f'AND created <= "{end_date} 23:59" '
        f"ORDER BY created DESC"
    )


def issue_entered_test_status(issue_payload: Dict) -> bool:
    changelog = issue_payload.get("changelog", {}) or {}
    histories = changelog.get("histories", []) or []

    for history in histories:
        for item in history.get("items", []) or []:
            field_name = (item.get("field") or "").strip().lower()
            to_string = (item.get("toString") or "").strip().lower()

            if field_name == "status" and to_string in TEST_STATUSES:
                return True

    return False


def audit_issues_from_search_results(
    jira_client,
    issues: List[Dict],
    include_attachment_contents: bool = False,
) -> pd.DataFrame:
    rows = []

    for issue_stub in issues:
        issue_key = issue_stub.get("key", "")
        if not issue_key:
            continue

        try:
            issue_bundle = jira_client.get_issue_bundle(
                issue_key=issue_key,
                include_attachment_contents=include_attachment_contents,
                include_changelog=True,
            )
            issue_payload = issue_bundle.get("issue", {})

            if not issue_entered_test_status(issue_payload):
                continue

            jira_context = build_jira_context(issue_bundle)

            fields = issue_payload.get("fields", {})
            desc_quality = description_quality(jira_context)
            readiness_score = compute_readiness_score(jira_context)
            missing_items = build_missing_items(jira_context)

            issue_link = f"{jira_client.base_url}/browse/{issue_key}"

            row = {
                "Issue Key": issue_key,
                "Issue Link": issue_link,
                "Summary": fields.get("summary", ""),
                "Issue Type": (fields.get("issuetype") or {}).get("name", ""),
                "Status": (fields.get("status") or {}).get("name", ""),
                "Priority": (fields.get("priority") or {}).get("name", ""),
                "Created": fields.get("created", ""),
                "Ready/Test'e En Az Bir Kez Girmiş": "Evet",
                "Confluence Link Var": "Evet" if has_confluence_link(jira_context) else "Hayır",
                "Figma Link Var": "Evet" if has_figma_link(jira_context) else "Hayır",
                "Description Var": "Evet" if has_description(jira_context) else "Hayır",
                "Description Kalitesi": desc_quality["level"],
                "Description Yorumu": desc_quality["reason"],
                "Acceptance Benzeri İçerik Var": "Evet" if has_acceptance_like_content(jira_context) else "Hayır",
                "Attachment Var": "Evet" if has_attachments(jira_context) else "Hayır",
                "Comment Var": "Evet" if has_comments(jira_context) else "Hayır",
                "Readiness Score": readiness_score,
                "Analiz Açısından Riskli": "Evet" if is_risky_for_analysis(jira_context) else "Hayır",
                "Eksik Alanlar": " | ".join(missing_items),
                "Öneri": build_recommendation(jira_context),
            }

            rows.append(row)

        except Exception as exc:
            rows.append(
                {
                    "Issue Key": issue_key,
                    "Issue Link": f"{jira_client.base_url}/browse/{issue_key}",
                    "Summary": issue_stub.get("fields", {}).get("summary", ""),
                    "Issue Type": (issue_stub.get("fields", {}).get("issuetype") or {}).get("name", ""),
                    "Status": (issue_stub.get("fields", {}).get("status") or {}).get("name", ""),
                    "Priority": (issue_stub.get("fields", {}).get("priority") or {}).get("name", ""),
                    "Created": issue_stub.get("fields", {}).get("created", ""),
                    "Ready/Test'e En Az Bir Kez Girmiş": "",
                    "Confluence Link Var": "",
                    "Figma Link Var": "",
                    "Description Var": "",
                    "Description Kalitesi": "error",
                    "Description Yorumu": f"Okunamadı: {exc}",
                    "Acceptance Benzeri İçerik Var": "",
                    "Attachment Var": "",
                    "Comment Var": "",
                    "Readiness Score": 0,
                    "Analiz Açısından Riskli": "Evet",
                    "Eksik Alanlar": "Issue okunamadı",
                    "Öneri": "Jira erişimi veya parse hatası kontrol edilmeli.",
                }
            )

    df = pd.DataFrame(rows)

    if not df.empty and "Readiness Score" in df.columns:
        df = df.sort_values(
            by=["Readiness Score", "Created"],
            ascending=[True, False],
        ).reset_index(drop=True)

    return df
