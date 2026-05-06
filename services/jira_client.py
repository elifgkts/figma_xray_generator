import base64
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests


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

    def get_issue(self, issue_key: str) -> Dict[str, Any]:
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
            "*all",
        ]
        path = f"{self._api_prefix()}/issue/{issue_key}"
        return self._request(
            "GET",
            path,
            params={
                "fields": ",".join(fields),
            },
        )

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

    def get_issue_bundle(self, issue_key: str) -> Dict[str, Any]:
        issue = self.get_issue(issue_key)

        comments = []
        remote_links = []

        fields = issue.get("fields", {})
        if not fields.get("comment"):
            comments = self.get_comments(issue_key)
        else:
            comments = fields.get("comment", {}).get("comments", [])

        try:
            remote_links = self.get_remote_links(issue_key)
        except requests.HTTPError:
            remote_links = []

        return {
            "issue": issue,
            "comments": comments,
            "remote_links": remote_links,
        }


def extract_text_from_jira_value(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    if isinstance(value, list):
        return "\n".join([extract_text_from_jira_value(item) for item in value if item is not None])

    if isinstance(value, dict):
        # Atlassian Document Format
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
