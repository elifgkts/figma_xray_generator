import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote

import requests


@dataclass
class FigmaReference:
    file_key: str
    node_id: Optional[str]


class FigmaClient:
    def __init__(self, token: str, timeout: int = 30):
        if not token:
            raise ValueError("FIGMA_TOKEN bulunamadı.")

        self.token = token
        self.timeout = timeout
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
        - node-id=123-456 veya node-id=123%3A456
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
                "Figma file key bulunamadı. Link /design/{key}/ veya /file/{key}/ formatında olmalı.")

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

    def get_file(self, file_key: str) -> dict:
        url = f"{self.base_url}/files/{file_key}"
        response = requests.get(
            url,
            headers=self.headers,
            timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def get_node(self, file_key: str, node_id: str) -> dict:
        url = f"{self.base_url}/files/{file_key}/nodes"
        response = requests.get(
            url,
            headers=self.headers,
            params={"ids": node_id},
            timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def get_node_image_url(
        self,
        file_key: str,
        node_id: str,
        scale: int = 2,
        image_format: str = "png"
    ) -> Optional[str]:
        url = f"{self.base_url}/images/{file_key}"
        response = requests.get(
            url,
            headers=self.headers,
            params={
                "ids": node_id,
                "format": image_format,
                "scale": scale
            },
            timeout=self.timeout
        )
        response.raise_for_status()

        data = response.json()
        images = data.get("images", {})

        return images.get(node_id)

    def get_design_payload(self, figma_url: str) -> dict:
        ref = self.extract_reference(figma_url)

        if ref.node_id:
            node_data = self.get_node(ref.file_key, ref.node_id)
            image_url = self.get_node_image_url(ref.file_key, ref.node_id)

            node_tree = None
            node_record = node_data.get("nodes", {}).get(ref.node_id)
            if node_record:
                node_tree = node_record.get("document")

            return {
                "file_key": ref.file_key,
                "node_id": ref.node_id,
                "image_url": image_url,
                "raw": node_data,
                "node_tree": node_tree
            }

        file_data = self.get_file(ref.file_key)

        return {
            "file_key": ref.file_key,
            "node_id": None,
            "image_url": None,
            "raw": file_data,
            "node_tree": file_data.get("document")
        }
