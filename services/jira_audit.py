from datetime import datetime
from typing import Any, Dict, List, Optional

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
from services.link_resolver import resolve_urls


DEFAULT_ISSUE_TYPES = ["Story", "Task", "Sub-task"]
TEST_STATUSES = {"ready to test", "test"}


def build_audit_jql(
    project_keys: List[str],
    filter_mode: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    issue_types: Optional[List[str]] = None,
    sprint_ids: Optional[List[int]] = None,
) -> str:
    issue_types = issue_types or DEFAULT_ISSUE_TYPES
    sprint_ids = sprint_ids or []

    if not project_keys:
        raise ValueError("En az bir proje key gerekli.")

    if len(project_keys) == 1:
        project_clause = f'project = "{project_keys[0]}"'
    else:
        projects = ", ".join([f'"{x}"' for x in project_keys])
        project_clause = f"project in ({projects})"

    types_str = ", ".join([f'"{x}"' for x in issue_types])
    clauses = [
        project_clause,
        f"issuetype in ({types_str})",
    ]

    if filter_mode in {"Tarih Aralığı", "Tarih + Sprint"}:
        if not start_date or not end_date:
            raise ValueError("Tarih filtreli kullanımda start_date ve end_date gerekli.")
        clauses.append(f'created <= "{end_date} 23:59"')
        clauses.append(f'updated >= "{start_date}"')

    if filter_mode in {"Sprint", "Tarih + Sprint"}:
        if not sprint_ids:
            raise ValueError("Sprint filtreli kullanımda en az bir sprint gerekli.")
        sprint_str = ", ".join([str(x) for x in sprint_ids])
        clauses.append(f"Sprint in ({sprint_str})")

    return " AND ".join(clauses) + " ORDER BY updated DESC"


def _parse_jira_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None

    patterns = [
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
    ]

    for pattern in patterns:
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue

    return None


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


def issue_entered_test_status_during(issue_payload: Dict, start_date: str, end_date: str) -> bool:
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

    changelog = issue_payload.get("changelog", {}) or {}
    histories = changelog.get("histories", []) or []

    for history in histories:
        history_created = _parse_jira_datetime(history.get("created", ""))
        if history_created is None:
            continue

        history_naive = history_created.replace(tzinfo=None)

        if not (start_dt <= history_naive <= end_dt):
            continue

        for item in history.get("items", []) or []:
            field_name = (item.get("field") or "").strip().lower()
            to_string = (item.get("toString") or "").strip().lower()

            if field_name == "status" and to_string in TEST_STATUSES:
                return True

    return False


def _summarize_unresolved(unresolved_sources: List[Any]) -> str:
    if not unresolved_sources:
        return ""

    messages = []
    for item in unresolved_sources[:5]:
        if isinstance(item, dict):
            source = item.get("source_url") or item.get("url") or item.get("source") or ""
            reason = item.get("reason") or item.get("message") or item.get("error") or "çözümlenemedi"
            messages.append(f"{source} -> {reason}" if source else str(reason))
        else:
            messages.append(str(item))

    return " | ".join(messages)


def _match_unresolved(unresolved_sources: List[Any], keyword: str) -> bool:
    keyword = keyword.lower()
    for item in unresolved_sources:
        text = str(item).lower()
        if keyword in text:
            return True
    return False


def _deep_link_status(
    link_exists: bool,
    resolved_count: int,
    unresolved_sources: List[Any],
    keyword: str,
    client_available: bool,
) -> str:
    if not link_exists:
        return "Link yok"
    if resolved_count > 0:
        return "Erişim var"
    if not client_available:
        return "Client / token yok"
    if _match_unresolved(unresolved_sources, keyword):
        return "Erişim yok veya yetki yetersiz"
    if unresolved_sources:
        return "Çözümlenemedi"
    return "İçerik okunamadı"


def _overall_deep_status(resolved: Dict[str, Any], url_count: int) -> str:
    if url_count == 0:
        return "İlgili link yok"

    figma_count = len(resolved.get("figma_contexts", []) or [])
    conf_count = len(resolved.get("confluence_contexts", []) or [])
    unresolved = resolved.get("unresolved_sources", []) or []

    if (figma_count or conf_count) and not unresolved:
        return "Başarılı"
    if (figma_count or conf_count) and unresolved:
        return "Kısmi"
    if unresolved:
        return "Erişim/Çözümleme Sorunu"
    return "Link bulundu ama içerik okunamadı"


def audit_issues_from_search_results(
    jira_client,
    issues: List[Dict],
    include_attachment_contents: bool = False,
    filter_mode: str = "Tarih Aralığı",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    deep_link_analysis: bool = True,
    figma_client=None,
    confluence_client=None,
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

            if filter_mode in {"Tarih Aralığı", "Tarih + Sprint"}:
                if not start_date or not end_date:
                    continue
                if not issue_entered_test_status_during(issue_payload, start_date, end_date):
                    continue
                entered_test_value = "Evet (tarih aralığında)"
            else:
                if not issue_entered_test_status(issue_payload):
                    continue
                entered_test_value = "Evet"

            jira_context = build_jira_context(issue_bundle)
            fields = issue_payload.get("fields", {})
            desc_quality = description_quality(jira_context)
            readiness_score = compute_readiness_score(jira_context)
            missing_items = build_missing_items(jira_context)
            issue_link = f"{jira_client.base_url}/browse/{issue_key}"

            url_count = len(jira_context.get("extracted_urls", []) or [])
            figma_link_exists = has_figma_link(jira_context)
            confluence_link_exists = has_confluence_link(jira_context)

            deep_status = "Kapalı"
            figma_content_status = "Analiz kapalı"
            confluence_content_status = "Analiz kapalı"
            linked_context = "Hayır"
            link_note = ""

            if deep_link_analysis:
                try:
                    resolved = resolve_urls(
                        urls=jira_context.get("extracted_urls", []) or [],
                        figma_client=figma_client,
                        confluence_client=confluence_client,
                    )
                except Exception as exc:
                    resolved = {
                        "figma_contexts": [],
                        "confluence_contexts": [],
                        "unresolved_sources": [f"resolve_urls_error: {exc}"],
                    }

                figma_count = len(resolved.get("figma_contexts", []) or [])
                conf_count = len(resolved.get("confluence_contexts", []) or [])
                unresolved = resolved.get("unresolved_sources", []) or []

                deep_status = _overall_deep_status(resolved, url_count)
                figma_content_status = _deep_link_status(
                    figma_link_exists,
                    figma_count,
                    unresolved,
                    "figma",
                    figma_client is not None,
                )
                confluence_content_status = _deep_link_status(
                    confluence_link_exists,
                    conf_count,
                    unresolved,
                    "confluence",
                    confluence_client is not None,
                )
                linked_context = "Evet" if (figma_count > 0 or conf_count > 0) else "Hayır"
                link_note = _summarize_unresolved(unresolved)

                if figma_link_exists and figma_count == 0:
                    missing_items.append("Figma link var ama içerik okunamadı")
                if confluence_link_exists and conf_count == 0:
                    missing_items.append("Confluence link var ama içerik okunamadı")

            row = {
                "Issue Key": issue_key,
                "Issue Link": issue_link,
                "Summary": fields.get("summary", ""),
                "Issue Type": (fields.get("issuetype") or {}).get("name", ""),
                "Status": (fields.get("status") or {}).get("name", ""),
                "Priority": (fields.get("priority") or {}).get("name", ""),
                "Created": fields.get("created", ""),
                "Updated": fields.get("updated", ""),
                "Ready/Test'e En Az Bir Kez Girmiş": entered_test_value,
                "Confluence Link Var": "Evet" if confluence_link_exists else "Hayır",
                "Figma Link Var": "Evet" if figma_link_exists else "Hayır",
                "Description Var": "Evet" if has_description(jira_context) else "Hayır",
                "Description Kalitesi": desc_quality["level"],
                "Description Yorumu": desc_quality["reason"],
                "Acceptance Benzeri İçerik Var": "Evet" if has_acceptance_like_content(jira_context) else "Hayır",
                "Attachment Var": "Evet" if has_attachments(jira_context) else "Hayır",
                "Comment Var": "Evet" if has_comments(jira_context) else "Hayır",
                "Derin Link Analizi": "Evet" if deep_link_analysis else "Hayır",
                "Derin Link Analizi Durumu": deep_status,
                "Confluence İçerik Durumu": confluence_content_status,
                "Figma İçerik Durumu": figma_content_status,
                "Linkten Ek Bağlam Var": linked_context,
                "Link Analiz Notu": link_note,
                "Readiness Score": readiness_score,
                "Analiz Açısından Riskli": "Evet" if is_risky_for_analysis(jira_context) else "Hayır",
                "Eksik Alanlar": " | ".join(dict.fromkeys(missing_items)),
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
                    "Updated": issue_stub.get("fields", {}).get("updated", ""),
                    "Ready/Test'e En Az Bir Kez Girmiş": "",
                    "Confluence Link Var": "",
                    "Figma Link Var": "",
                    "Description Var": "",
                    "Description Kalitesi": "error",
                    "Description Yorumu": f"Okunamadı: {exc}",
                    "Acceptance Benzeri İçerik Var": "",
                    "Attachment Var": "",
                    "Comment Var": "",
                    "Derin Link Analizi": "Evet" if deep_link_analysis else "Hayır",
                    "Derin Link Analizi Durumu": "Hata",
                    "Confluence İçerik Durumu": "",
                    "Figma İçerik Durumu": "",
                    "Linkten Ek Bağlam Var": "",
                    "Link Analiz Notu": "",
                    "Readiness Score": 0,
                    "Analiz Açısından Riskli": "Evet",
                    "Eksik Alanlar": "Issue okunamadı",
                    "Öneri": "Jira erişimi veya parse hatası kontrol edilmeli.",
                }
            )

    df = pd.DataFrame(rows)

    if not df.empty and "Readiness Score" in df.columns:
        df = df.sort_values(
            by=["Readiness Score", "Updated"],
            ascending=[True, False],
        ).reset_index(drop=True)

    return df
