from typing import Any, Dict, List, Optional

from services.confluence_client import ConfluenceClient
from services.figma_client import FigmaClient


def classify_url(url: str) -> str:
    lowered = url.lower()

    if "figma.com" in lowered:
        return "figma"

    if "/wiki/" in lowered or "confluence" in lowered or "viewpage.action" in lowered:
        return "confluence"

    if "/browse/" in lowered:
        return "jira"

    return "unknown"


def resolve_urls(
    urls: List[str],
    figma_client: Optional[FigmaClient] = None,
    confluence_client: Optional[ConfluenceClient] = None,
) -> Dict[str, Any]:
    figma_contexts: List[Dict[str, Any]] = []
    confluence_contexts: List[Dict[str, Any]] = []
    jira_urls: List[str] = []
    unresolved_sources: List[Dict[str, str]] = []

    for url in urls:
        source_type = classify_url(url)

        try:
            if source_type == "figma":
                if not figma_client:
                    unresolved_sources.append({"type": "figma", "url": url, "reason": "Figma client yok"})
                    continue

                payload = figma_client.get_design_payload(
                    figma_url=url,
                    include_image=False,
                    selected_node_id=None,
                )
                figma_contexts.append(
                    {
                        "source": "figma_link_from_jira",
                        "source_url": url,
                        "file_key": payload.get("file_key"),
                        "node_id": payload.get("node_id"),
                        "node_tree": payload.get("node_tree"),
                    }
                )

            elif source_type == "confluence":
                if not confluence_client:
                    unresolved_sources.append({"type": "confluence", "url": url, "reason": "Confluence client yok"})
                    continue

                page_data = confluence_client.get_page_from_url(url)
                context = confluence_client.build_page_context(page_data, source_url=url)
                confluence_contexts.append(context)

            elif source_type == "jira":
                jira_urls.append(url)

        except Exception as exc:
            unresolved_sources.append(
                {
                    "type": source_type,
                    "url": url,
                    "reason": str(exc),
                }
            )

    return {
        "figma_contexts": figma_contexts,
        "confluence_contexts": confluence_contexts,
        "jira_urls": jira_urls,
        "unresolved_sources": unresolved_sources,
    }
