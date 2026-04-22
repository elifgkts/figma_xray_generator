import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote

import requests


@dataclass
class FigmaReference:
    file_key: str
    node_id: Optional[str]


class FigmaRateLimitError(Exception):
    def __init__(
        self,
        message: str,
        retry_after: Optional[int] = None,
        plan_tier: Optional[str] = None,
        rate_limit_type: Optional[str] = None,
        upgrade_link: Optional[str] = None
    ):
        super().__init__(message)
        self.retry_after = retry_after
        self.plan_tier = plan_tier
        self.rate_limit_type = rate_limit_type
        self.upgrade_link = upgrade_link


class FigmaClient:
    def __init__(
        self,
        token: str,
        timeout: int = 30,
        max_retries: int = 2,
        max_retry_wait_seconds: int = 30
    ):
        if not token:
            raise ValueError("FIGMA_TOKEN bulunamadı.")

        self.token = token
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_retry_wait_seconds = max_retry_wait_seconds
        self.base_url = "https://api.figma.com/v1"
        self.headers = {
            "X-Figma-Token": self.token
        }

    @staticmethod
    def extract_reference(figma_url: str) -> FigmaReference:
        """
        Desteklenen örnekler:
        - https://www.figma.com/design/{file_key}/...
        - https://www.figma.com/file/{file_key}/...
        - node-id=123-456
        - node-id=123%3A456
        """
        if not figma_url:
            raise ValueError("Figma URL boş olamaz.")

        parsed = urlparse(figma_url)
        path_parts = [p for p in parsed.path.split("/") if p]

        file_key = None

        for marker in ["design", "file"]:
            if marker in path_parts:
                idx = path_parts.index(marker)
                if len(path_parts) > idx + 1:
                    file_key = path_parts[idx + 1]
                    break

        if not file_key:
            match = re.search(r"/(?:design|file)/([^/]+)", figma_url)
            if match:
                file_key = match.group(1)

        if not file_key:
            raise ValueError(
                "Figma file key bulunamadı. Link /design/{key}/ veya /file/{key}/ formatında olmalı."
            )

        query = parse_qs(parsed.query)
        node_id = None

        if "node-id" in query and query["node-id"]:
            node_id = unquote(query["node-id"][0])
        else:
            match = re.search(r"node-id=([^&]+)", figma_url)
            if match:
                node_id = unquote(match.group(1))

        if node_id:
            node_id = node_id.replace("-", ":")

        return FigmaReference(file_key=file_key, node_id=node_id)

    def _request(self, url: str, params: Optional[dict] = None) -> dict:
        attempt = 0

        while True:
            response = requests.get(
                url,
                headers=self.headers,
                params=params,
                timeout=self.timeout
            )

            if response.status_code != 429:
                response.raise_for_status()
                return response.json()

            retry_after = self._read_int_header(response, "Retry-After")
            plan_tier = response.headers.get("X-Figma-Plan-Tier")
            rate_limit_type = response.headers.get("X-Figma-Rate-Limit-Type")
            upgrade_link = response.headers.get("X-Figma-Upgrade-Link")

            if attempt >= self.max_retries:
                raise FigmaRateLimitError(
                    message=self._build_rate_limit_message(
                        retry_after=retry_after,
                        plan_tier=plan_tier,
                        rate_limit_type=rate_limit_type
                    ),
                    retry_after=retry_after,
                    plan_tier=plan_tier,
                    rate_limit_type=rate_limit_type,
                    upgrade_link=upgrade_link
                )

            wait_seconds = retry_after if retry_after is not None else 3

            if wait_seconds > self.max_retry_wait_seconds:
                raise FigmaRateLimitError(
                    message=self._build_rate_limit_message(
                        retry_after=retry_after,
                        plan_tier=plan_tier,
                        rate_limit_type=rate_limit_type
                    ),
                    retry_after=retry_after,
                    plan_tier=plan_tier,
                    rate_limit_type=rate_limit_type,
                    upgrade_link=upgrade_link
                )

            time.sleep(wait_seconds)
            attempt += 1

    @staticmethod
    def _read_int_header(response: requests.Response, header_name: str) -> Optional[int]:
        value = response.headers.get(header_name)
        if value is None:
            return None

        try:
            return int(value)
        except ValueError:
            return None

    @staticmethod
    def _build_rate_limit_message(
        retry_after: Optional[int],
        plan_tier: Optional[str],
        rate_limit_type: Optional[str]
    ) -> str:
        parts = [
            "Figma API rate limit'e takıldı. Kısa sürede çok fazla istek atılmış olabilir veya dosyanın bulunduğu plan/seat limiti dolmuş olabilir."
        ]

        if retry_after is not None:
            parts.append(f"Figma tekrar denemek için yaklaşık {retry_after} saniye beklenmesini istiyor.")

        if plan_tier:
            parts.append(f"Plan tier: {plan_tier}")

        if rate_limit_type:
            parts.append(f"Rate limit tipi: {rate_limit_type}")

        parts.append(
            "Bir süre bekleyip tekrar deneyebilirsin. Aynı linke art arda basmamak ve önce ekran listesini tarayıp tek frame seçmek daha sağlıklı olur."
        )

        return " ".join(parts)

    def get_file(self, file_key: str) -> dict:
        url = f"{self.base_url}/files/{file_key}"
        return self._request(url)

    def get_file_outline(self, file_key: str, depth: int = 2) -> dict:
        url = f"{self.base_url}/files/{file_key}"
        return self._request(
            url,
            params={"depth": depth}
        )

    def get_file_subset_by_node(
        self,
        file_key: str,
        node_id: str,
        depth: Optional[int] = None
    ) -> dict:
        url = f"{self.base_url}/files/{file_key}"

        params = {
            "ids": node_id
        }

        if depth is not None:
            params["depth"] = depth

        return self._request(
            url,
            params=params
        )

    def get_node(self, file_key: str, node_id: str) -> dict:
        url = f"{self.base_url}/files/{file_key}/nodes"
        return self._request(
            url,
            params={"ids": node_id}
        )

    def get_node_image_url(
        self,
        file_key: str,
        node_id: str,
        scale: int = 1,
        image_format: str = "png"
    ) -> Optional[str]:
        url = f"{self.base_url}/images/{file_key}"
        data = self._request(
            url,
            params={
                "ids": node_id,
                "format": image_format,
                "scale": scale
            }
        )

        images = data.get("images", {})
        return images.get(node_id)

    def get_design_outline_payload(self, figma_url: str, depth: int = 2) -> dict:
        ref = self.extract_reference(figma_url)
        outline_data = self.get_file_outline(ref.file_key, depth=depth)

        return {
            "file_key": ref.file_key,
            "node_id": ref.node_id,
            "raw": outline_data,
            "node_tree": outline_data.get("document")
        }

    def get_design_payload(
        self,
        figma_url: str,
        include_image: bool = False,
        selected_node_id: Optional[str] = None
    ) -> dict:
        ref = self.extract_reference(figma_url)
        target_node_id = selected_node_id or ref.node_id

        if target_node_id:
            file_data = self.get_file_subset_by_node(
                ref.file_key,
                target_node_id
            )

            image_url = None
            if include_image:
                image_url = self.get_node_image_url(
                    ref.file_key,
                    target_node_id
                )

            node_tree = self._find_node_by_id(
                file_data.get("document"),
                target_node_id
            )

            return {
                "file_key": ref.file_key,
                "node_id": target_node_id,
                "image_url": image_url,
                "raw": file_data,
                "node_tree": node_tree or file_data.get("document")
            }

        file_data = self.get_file(ref.file_key)

        return {
            "file_key": ref.file_key,
            "node_id": None,
            "image_url": None,
            "raw": file_data,
            "node_tree": file_data.get("document")
        }

    @staticmethod
    def _find_node_by_id(node: Optional[dict], target_id: str) -> Optional[dict]:
        if not node:
            return None

        if node.get("id") == target_id:
            return node

        for child in node.get("children", []) or []:
            found = FigmaClient._find_node_by_id(child, target_id)
            if found:
                return found

        return None
