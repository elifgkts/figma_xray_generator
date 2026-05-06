import re
from typing import Any, Dict, List

from services.jira_client import extract_text_from_jira_value


URL_RE = re.compile(r"https?://[^\s<>\]\"')]+", re.IGNORECASE)


def build_jira_context(issue_bundle: Dict[str, Any]) -> Dict[str, Any]:
    issue = issue_bundle.get("issue", {})
    fields = issue.get("fields", {})
    comments = issue_bundle.get("comments", [])
    remote_links = issue_bundle.get("remote_links", [])
    attachment_contents = issue_bundle.get("attachment_contents", [])

    description_text = extract_text_from_jira_value(fields.get("description"))
    comment_texts = [extract_text_from_jira_value(c.get("body")) for c in comments]

    custom_field_texts = []
    for key, value in fields.items():
        if str(key).startswith("customfield_"):
            text = extract_text_from_jira_value(value).strip()
            if text:
                custom_field_texts.append({"field": key, "text": text})

    attachment_names = [
        att.get("filename", "")
        for att in fields.get("attachment", []) or []
        if att.get("filename")
    ]

    extracted_attachment_texts = []
    for item in attachment_contents:
        if item.get("supported") and item.get("text"):
            extracted_attachment_texts.append(
                {
                    "filename": item.get("filename", ""),
                    "mime_type": item.get("mime_type", ""),
                    "size_bytes": item.get("size_bytes", 0),
                    "text": item.get("text", ""),
                }
            )

    attachment_processing_summary = [
        {
            "filename": item.get("filename", ""),
            "supported": item.get("supported", False),
            "reason": item.get("reason", ""),
        }
        for item in attachment_contents
    ]

    issuelink_summaries = []
    for link in fields.get("issuelinks", []) or []:
        inward = link.get("inwardIssue")
        outward = link.get("outwardIssue")
        linked = inward or outward
        if linked:
            linked_fields = linked.get("fields", {})
            issuelink_summaries.append(
                {
                    "key": linked.get("key"),
                    "summary": linked_fields.get("summary", ""),
                    "status": (linked_fields.get("status") or {}).get("name", ""),
                    "type": (linked_fields.get("issuetype") or {}).get("name", ""),
                }
            )

    remote_urls = []
    for rl in remote_links:
        obj = rl.get("object", {})
        url = obj.get("url")
        title = obj.get("title", "")
        if url:
            remote_urls.append({"url": url, "title": title})

    all_text_blocks = [
        fields.get("summary", ""),
        description_text,
        "\n".join(comment_texts),
        "\n".join(item["text"] for item in custom_field_texts),
        "\n".join(attachment_names),
        "\n".join(item["text"] for item in extracted_attachment_texts if item.get("text")),
        "\n".join(f"{x['key']} {x['summary']}" for x in issuelink_summaries),
        "\n".join(x["url"] for x in remote_urls),
    ]

    extracted_urls = extract_urls("\n".join([block for block in all_text_blocks if block]))

    context = {
        "source": "jira_issue",
        "issue_key": issue.get("key", ""),
        "summary": fields.get("summary", ""),
        "issue_type": (fields.get("issuetype") or {}).get("name", ""),
        "priority": (fields.get("priority") or {}).get("name", ""),
        "status": (fields.get("status") or {}).get("name", ""),
        "labels": fields.get("labels", []) or [],
        "components": [c.get("name", "") for c in fields.get("components", []) or [] if c.get("name")],
        "description": description_text,
        "comments": [text for text in comment_texts if text.strip()],
        "custom_fields": custom_field_texts,
        "attachments": attachment_names,
        "attachment_texts": extracted_attachment_texts,
        "attachment_processing_summary": attachment_processing_summary,
        "linked_issues": issuelink_summaries,
        "remote_links": remote_urls,
        "extracted_urls": extracted_urls,
        "summary_block": {
            "comment_count": len(comment_texts),
            "custom_field_count": len(custom_field_texts),
            "attachment_count": len(attachment_names),
            "attachment_text_count": len(extracted_attachment_texts),
            "linked_issue_count": len(issuelink_summaries),
            "remote_link_count": len(remote_urls),
            "url_count": len(extracted_urls),
        },
    }

    return context


def extract_urls(text: str) -> List[str]:
    seen = set()
    urls = []

    for match in URL_RE.findall(text or ""):
        clean = match.rstrip(".,);]")
        if clean not in seen:
            seen.add(clean)
            urls.append(clean)

    return urls
