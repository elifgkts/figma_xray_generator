import base64
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup


@dataclass
class ConfluenceAuthConfig:
    base_url: str
    deployment_type: str = "dc"  # "cloud" | "dc"
    email: Optional[str] = None
    username: Optional[str] = None
    api_token: Optional[str] = None
    pat: Optional[str] = None
    password: Optional[str] = None
    verify_ssl: bool = True
    timeout: int = 30


class ConfluenceClient:
    def __init__(self, config: ConfluenceAuthConfig):
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

    def get_page_by_id(self, page_id: str) -> Dict[str, Any]:
        if self.config.deployment_type == "cloud":
            # v2 first
            try:
                return self._request(
                    "GET",
                    f"/wiki/api/v2/pages/{page_id}",
                    params={"body-format": "storage"},
                )
            except requests.HTTPError:
                return self._request(
                    "GET",
                    f"/wiki/rest/api/content/{page_id}",
                    params={"expand": "body.storage,version,space"},
                )

        return self._request(
            "GET",
            f"/rest/api/content/{page_id}",
            params={"expand": "body.storage,version,space"},
        )

    def get_page_from_url(self, page_url: str) -> Dict[str, Any]:
        page_id = extract_confluence_page_id(page_url)
        if not page_id:
            raise ValueError("Confluence linkinden page id çıkarılamadı.")
        return self.get_page_by_id(page_id)

    def build_page_context(self, page_data: Dict[str, Any], source_url: str = "") -> Dict[str, Any]:
        title = page_data.get("title", "")
        body_html = extract_confluence_storage_html(page_data)
        text = html_to_text(body_html)
        links = extract_links_from_html(body_html)

        return {
            "source": "confluence_page",
            "source_url": source_url,
            "page_id": str(page_data.get("id", "")),
            "title": title,
            "text": text,
            "links": links,
            "summary": {
                "char_count": len(text),
                "link_count": len(links),
            },
        }


def extract_confluence_page_id(url: str) -> Optional[str]:
    parsed = urlparse(url)

    query_page_id = parse_qs(parsed.query).get("pageId")
    if query_page_id:
        return query_page_id[0]

    match = re.search(r"/pages/(\d+)", parsed.path)
    if match:
        return match.group(1)

    match = re.search(r"/pages/viewpage\.action", parsed.path)
    if match:
        query_page_id = parse_qs(parsed.query).get("pageId")
        if query_page_id:
            return query_page_id[0]

    return None


def extract_confluence_storage_html(page_data: Dict[str, Any]) -> str:
    body = page_data.get("body", {})

    if isinstance(body, dict):
        storage = body.get("storage", {})
        if isinstance(storage, dict) and storage.get("value"):
            return storage["value"]

        if body.get("value"):
            return body["value"]

    return ""


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    return soup.get_text("\n", strip=True)


def extract_links_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    links = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href and href not in links:
            links.append(href)

    return links
