import base64
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests

from services.analysis_doc_parser import extract_text_from_upload


ALLOWED_ATTACHMENT_SUFFIXES = {
    ".txt",
    ".md",
    ".docx",
    ".pdf",
    ".html",
    ".htm",
    ".csv",
}


@dataclass
class JiraAuthConfig:
    base_url: str
    deployment_type: str = "dc"  # "cloud" | "dc"
    email: Optional[str] = None
    username: Optional[str] = None
    api_token: Optional[str] = None
    pat: Optional[str] = None
    password: Optional[str] = None
    verify_ssl: bool = True
    timeout: int = 30


class JiraClient:
    def __init__(self, config: JiraAuthConfig):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        self._apply_auth()

    def _apply_auth(self) -> None:
        if self.config.pat:
            self.session.headers["Authorization"] = f"Bearer {self.config.pat}"
            return

        if self.config.deployment_type == "cloud" and self.config.email and self.config.api_token:
            token = f"{self.config.email}:{self.config.api_token}".encode("utf-8")
            encoded = base64.b64encode(token).decode("utf-8")
            self.session.headers["Authorization"] = f"Basic {encoded}"
            return

        if self.config.username and (self.config.password or self.config.api_token):
            secret = self.config.password or self.config.api_token
            token = f"{self.config.username}:{secret}".encode("utf-8")
            encoded = base64.b64encode(token).decode("utf-8")
            self.session.headers["Authorization"] = f"Basic {encoded}"

    def _api_prefix(self) -> str:
        if self.config.deployment_type == "cloud":
            return "/rest/api/3"
        return "/rest/api/2"

    def _agile_prefix(self) -> str:
        return "/rest/agile/1.0"

    def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        response = self.session.request(
            method=method,
            url=url,
            timeout=self.config.timeout,
            verify=self.config.verify_ssl,
            **kwargs,
        )
        response.raise_for_status()
        if not response.text:
            return {}
        return response.json()

    def _request_bytes(self, url_or_path: str) -> bytes:
        if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
            url = url_or_path
        else:
            url = urljoin(self.base_url + "/", url_or_path.lstrip("/"))

        response = self.session.get(
            url,
            timeout=self.config.timeout,
            verify=self.config.verify_ssl,
        )
        response.raise_for_status()
        return response.content

    def get_issue(self, issue_key: str, expand: Optional[List[str]] = None) -> Dict[str, Any]:
        fields = [
            "summary",
            "description",
            "issuetype",
            "priority",
            "labels",
            "components",
            "assignee",
            "reporter",
            "status",
            "parent",
            "subtasks",
            "attachment",
            "issuelinks",
            "comment",
            "created",
            "updated",
            "sprint",
            "*all",
        ]
        params: Dict[str, Any] = {"fields": ",".join(fields)}
        if expand:
            params["expand"] = ",".join(expand)

        path = f"{self._api_prefix()}/issue/{issue_key}"
        return self._request("GET", path, params=params)

    def get_issue_with_changelog(self, issue_key: str) -> Dict[str, Any]:
        return self.get_issue(issue_key, expand=["changelog"])

    def get_comments(self, issue_key: str) -> List[Dict[str, Any]]:
        path = f"{self._api_prefix()}/issue/{issue_key}/comment"
        data = self._request("GET", path)
        return data.get("comments", [])

    def get_remote_links(self, issue_key: str) -> List[Dict[str, Any]]:
        path = f"{self._api_prefix()}/issue/{issue_key}/remotelink"
        data = self._request("GET", path)
        if isinstance(data, list):
            return data
        return data.get("values", [])

    def search_issues(
        self,
        jql: str,
        fields: Optional[List[str]] = None,
        start_at: int = 0,
        max_results: int = 50,
    ) -> Dict[str, Any]:
        path = f"{self._api_prefix()}/search"
        params = {
            "jql": jql,
            "startAt": start_at,
            "maxResults": max_results,
        }
        if fields:
            params["fields"] = ",".join(fields)

        return self._request("GET", path, params=params)

    def search_issues_paginated(
        self,
        jql: str,
        fields: Optional[List[str]] = None,
        batch_size: int = 50,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        start_at = 0

        while True:
            data = self.search_issues(
                jql=jql,
                fields=fields,
                start_at=start_at,
                max_results=min(batch_size, max(1, limit - len(issues))),
            )

            batch = data.get("issues", []) or []
            issues.extend(batch)

            total = int(data.get("total", 0) or 0)
            start_at += len(batch)

            if not batch:
                break

            if len(issues) >= limit:
                break

            if start_at >= total:
                break

        return issues[:limit]

    def list_projects(self) -> List[Dict[str, Any]]:
        path = f"{self._api_prefix()}/project"
        data = self._request("GET", path)

        if isinstance(data, list):
            return data

        return data.get("values", [])

    def list_boards(self, project_keys: List[str], board_type: Optional[str] = None) -> List[Dict[str, Any]]:
        values: List[Dict[str, Any]] = []

        for project_key in project_keys:
            start_at = 0
            while True:
                params: Dict[str, Any] = {
                    "projectKeyOrId": project_key,
                    "startAt": start_at,
                    "maxResults": 50,
                }
                if board_type:
                    params["type"] = board_type

                data = self._request("GET", f"{self._agile_prefix()}/board", params=params)
                batch = data.get("values", []) or []
                values.extend(batch)

                if data.get("isLast", True):
                    break

                start_at += len(batch)

        return values

    def list_board_sprints(self, board_id: int, state: str = "active,closed,future") -> List[Dict[str, Any]]:
        values: List[Dict[str, Any]] = []
        start_at = 0

        while True:
            data = self._request(
                "GET",
                f"{self._agile_prefix()}/board/{board_id}/sprint",
                params={
                    "state": state,
                    "startAt": start_at,
                    "maxResults": 50,
                },
            )
            batch = data.get("values", []) or []
            values.extend(batch)

            if data.get("isLast", True):
                break

            start_at += len(batch)

        return values

    def get_issue_bundle(
        self,
        issue_key: str,
        include_attachment_contents: bool = True,
        max_attachments: int = 5,
        max_attachment_size_bytes: int = 5 * 1024 * 1024,
        max_attachment_text_chars: int = 15000,
        include_changelog: bool = False,
    ) -> Dict[str, Any]:
        if include_changelog:
            issue = self.get_issue_with_changelog(issue_key)
        else:
            issue = self.get_issue(issue_key)

        fields = issue.get("fields", {})
        if not fields.get("comment"):
            comments = self.get_comments(issue_key)
        else:
            comments = fields.get("comment", {}).get("comments", [])

        try:
            remote_links = self.get_remote_links(issue_key)
        except requests.HTTPError:
            remote_links = []

        attachment_contents = []
        if include_attachment_contents:
            attachment_contents = self.extract_supported_attachment_contents(
                issue=issue,
                max_attachments=max_attachments,
                max_attachment_size_bytes=max_attachment_size_bytes,
                max_attachment_text_chars=max_attachment_text_chars,
            )

        return {
            "issue": issue,
            "comments": comments,
            "remote_links": remote_links,
            "attachment_contents": attachment_contents,
        }

    def extract_supported_attachment_contents(
        self,
        issue: Dict[str, Any],
        max_attachments: int = 5,
        max_attachment_size_bytes: int = 5 * 1024 * 1024,
        max_attachment_text_chars: int = 15000,
    ) -> List[Dict[str, Any]]:
        fields = issue.get("fields", {})
        attachments = fields.get("attachment", []) or []

        extracted: List[Dict[str, Any]] = []

        for att in attachments[:max_attachments]:
            filename = att.get("filename", "") or ""
            mime_type = att.get("mimeType", "") or ""
            size_bytes = int(att.get("size", 0) or 0)
            content_url = att.get("content", "") or ""

            suffix = self._suffix(filename)

            if suffix not in ALLOWED_ATTACHMENT_SUFFIXES:
                extracted.append(
                    {
                        "filename": filename,
                        "mime_type": mime_type,
                        "size_bytes": size_bytes,
                        "supported": False,
                        "reason": "unsupported_extension",
                        "text": "",
                    }
                )
                continue

            if size_bytes > max_attachment_size_bytes:
                extracted.append(
                    {
                        "filename": filename,
                        "mime_type": mime_type,
                        "size_bytes": size_bytes,
                        "supported": False,
                        "reason": "file_too_large",
                        "text": "",
                    }
                )
                continue

            try:
                content_bytes = self._request_bytes(content_url)
                extracted_text = extract_text_from_upload(filename, content_bytes)

                if len(extracted_text) > max_attachment_text_chars:
                    extracted_text = extracted_text[:max_attachment_text_chars] + "\n...[TRUNCATED]..."

                extracted.append(
                    {
                        "filename": filename,
                        "mime_type": mime_type,
                        "size_bytes": size_bytes,
                        "supported": True,
                        "reason": "",
                        "text": extracted_text.strip(),
                    }
                )
            except Exception as exc:
                extracted.append(
                    {
                        "filename": filename,
                        "mime_type": mime_type,
                        "size_bytes": size_bytes,
                        "supported": False,
                        "reason": f"read_failed: {exc}",
                        "text": "",
                    }
                )

        return extracted

    @staticmethod
    def _suffix(filename: str) -> str:
        filename = filename.lower().strip()
        if "." not in filename:
            return ""
        return "." + filename.split(".")[-1]


def extract_text_from_jira_value(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    if isinstance(value, list):
        return "\n".join([extract_text_from_jira_value(item) for item in value if item is not None])

    if isinstance(value, dict):
        if value.get("type") == "doc":
            return _flatten_adf_node(value)

        text_parts = []
        for key, item in value.items():
            if key in {"self", "id", "accountId", "avatarUrls", "iconUrl"}:
                continue
            item_text = extract_text_from_jira_value(item)
            if item_text:
                text_parts.append(item_text)

        return "\n".join(text_parts)

    return str(value)


def _flatten_adf_node(node: Any) -> str:
    if node is None:
        return ""

    if isinstance(node, str):
        return node

    if isinstance(node, list):
        return "".join(_flatten_adf_node(item) for item in node)

    if not isinstance(node, dict):
        return str(node)

    node_type = node.get("type")
    text = node.get("text", "")
    content = node.get("content", [])

    if node_type in {"paragraph", "heading", "blockquote", "listItem"}:
        inner = "".join(_flatten_adf_node(child) for child in content).strip()
        return f"{inner}\n" if inner else ""

    if node_type in {"bulletList", "orderedList"}:
        return "".join(_flatten_adf_node(child) for child in content)

    if node_type == "hardBreak":
        return "\n"

    if node_type == "text":
        return text

    if content:
        return "".join(_flatten_adf_node(child) for child in content)

    return text or ""
