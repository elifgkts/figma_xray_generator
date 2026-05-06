import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import requests


class FigmaRateLimitError(Exception):
    def __init__(
        self,
        message: str,
        retry_after: Optional[int] = None,
        upgrade_link: Optional[str] = None,
        plan_tier: Optional[str] = None,
        rate_limit_type: Optional[str] = None,
    ):
        super().__init__(message)
        self.retry_after = retry_after
        self.upgrade_link = upgrade_link
        self.plan_tier = plan_tier
        self.rate_limit_type = rate_limit_type


class FigmaClient:
    def __init__(self, token: str, timeout: int = 30):
        if not token:
            raise ValueError("Figma token gerekli.")
        self.token = token
        self.timeout = timeout
        self.base_url = "https://api.figma.com/v1"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-Figma-Token": token,
                "Accept": "application/json",
            }
        )

    def _request_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        response = self.session.get(url, params=params, timeout=self.timeout)

        if response.status_code == 429:
            raise self._build_rate_limit_error(response)

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            body = ""
            try:
                body = response.text[:1000]
            except Exception:
                body = ""
            raise RuntimeError(
                f"Figma API hatası: HTTP {response.status_code}. {body}"
            ) from exc

        return response.json()

    def _build_rate_limit_error(self, response: requests.Response) -> FigmaRateLimitError:
        retry_after_raw = response.headers.get("Retry-After")
        retry_after = None
        if retry_after_raw:
            try:
                retry_after = int(float(retry_after_raw))
            except Exception:
                retry_after = None

        plan_tier = response.headers.get("X-Figma-Plan-Tier")
        rate_limit_type = response.headers.get("X-Figma-Rate-Limit-Type")
        upgrade_link = (
            response.headers.get("X-Figma-Upgrade-Link")
            or response.headers.get("X-Upgrade-Link")
            or None
        )

        msg_parts = [
            "Figma API rate limit'e takıldı.",
            "Kısa sürede çok fazla istek atılmış olabilir veya dosyanın bulunduğu plan/seat limiti dolmuş olabilir.",
        ]

        if retry_after is not None:
            msg_parts.append(f"Figma tekrar denemek için yaklaşık {retry_after} saniye beklenmesini istiyor.")

        if plan_tier:
            msg_parts.append(f"Plan tier: {plan_tier}")

        if rate_limit_type:
            msg_parts.append(f"Rate limit tipi: {rate_limit_type}")

        msg_parts.append(
            "Bir süre bekleyip tekrar deneyebilirsin. Aynı linke art arda basmamak ve önce ekran listesini tarayıp tek frame seçmek daha sağlıklı olur."
        )

        return FigmaRateLimitError(
            message=" ".join(msg_parts),
            retry_after=retry_after,
            upgrade_link=upgrade_link,
            plan_tier=plan_tier,
            rate_limit_type=rate_limit_type,
        )

    @staticmethod
    def extract_file_key_and_node_id(figma_url: str) -> Tuple[str, Optional[str]]:
        if not figma_url:
            raise ValueError("Figma linki boş olamaz.")

        parsed = urlparse(figma_url)
        path = parsed.path or ""

        match = re.search(r"/(?:design|file|proto)/([a-zA-Z0-9]+)", path)
        if not match:
            raise ValueError("Figma linkinden file key çıkarılamadı.")

        file_key = match.group(1)

        query = parse_qs(parsed.query)
        node_id = None

        if "node-id" in query and query["node-id"]:
            raw = unquote(query["node-id"][0]).strip()
            node_id = raw.replace("-", ":") if ":" not in raw else raw

        return file_key, node_id

    def get_file(self, file_key: str, depth: Optional[int] = None) -> Dict[str, Any]:
        params = {}
        if depth is not None:
            params["depth"] = depth
        return self._request_json(f"/files/{file_key}", params=params)

    def get_nodes(
        self,
        file_key: str,
        node_ids: List[str],
        depth: Optional[int] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "ids": ",".join(node_ids),
        }
        if depth is not None:
            params["depth"] = depth
        return self._request_json(f"/files/{file_key}/nodes", params=params)

    def get_image_url(
        self,
        file_key: str,
        node_id: str,
        scale: int = 2,
        fmt: str = "png",
    ) -> Optional[str]:
        data = self._request_json(
            f"/images/{file_key}",
            params={
                "ids": node_id,
                "scale": scale,
                "format": fmt,
            },
        )
        images = data.get("images", {})
        return images.get(node_id)

    def get_design_outline_payload(self, figma_url: str, depth: int = 3) -> Dict[str, Any]:
        file_key, node_id = self.extract_file_key_and_node_id(figma_url)
        file_data = self.get_file(file_key, depth=depth)

        return {
            "file_key": file_key,
            "file_name": file_data.get("name", ""),
            "node_id": node_id,
            "node_tree": file_data.get("document", {}),
            "raw": file_data,
        }

    def get_design_payload(
        self,
        figma_url: str,
        include_image: bool = False,
        selected_node_id: Optional[str] = None,
        depth: Optional[int] = None,
    ) -> Dict[str, Any]:
        file_key, url_node_id = self.extract_file_key_and_node_id(figma_url)
        node_id = selected_node_id or url_node_id
        image_url = None

        if node_id:
            nodes_data = self.get_nodes(
                file_key=file_key,
                node_ids=[node_id],
                depth=depth,
            )
            node_tree = self._extract_single_node_document(nodes_data, node_id)

            if include_image:
                try:
                    image_url = self.get_image_url(file_key, node_id)
                except Exception:
                    image_url = None

            return {
                "file_key": file_key,
                "file_name": "",
                "node_id": node_id,
                "node_tree": node_tree,
                "image_url": image_url,
                "raw": nodes_data,
            }

        file_data = self.get_file(file_key, depth=depth)
        node_tree = file_data.get("document", {})

        return {
            "file_key": file_key,
            "file_name": file_data.get("name", ""),
            "node_id": None,
            "node_tree": node_tree,
            "image_url": image_url,
            "raw": file_data,
        }

    @staticmethod
    def _extract_single_node_document(nodes_data: Dict[str, Any], node_id: str) -> Dict[str, Any]:
        nodes = nodes_data.get("nodes", {})
        node_entry = nodes.get(node_id)

        if not node_entry:
            if nodes:
                first_entry = next(iter(nodes.values()))
                if isinstance(first_entry, dict) and first_entry.get("document"):
                    return first_entry["document"]
            raise ValueError(f"Figma nodes response içinde {node_id} bulunamadı.")

        document = node_entry.get("document")
        if not document:
            raise ValueError(f"Figma node document alanı boş döndü: {node_id}")

        return document
