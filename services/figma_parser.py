from typing import Any, Dict, List, Optional


FRAME_TYPES = {"FRAME", "SECTION"}
COMPONENT_TYPES = {"COMPONENT", "INSTANCE", "COMPONENT_SET"}
TEXT_TYPES = {"TEXT"}


def extract_candidate_frames(outline_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    node_tree = outline_payload.get("node_tree", {}) or {}
    results: List[Dict[str, Any]] = []

    def walk(node: Dict[str, Any], parents: List[str]) -> None:
        if not isinstance(node, dict):
            return

        node_type = node.get("type", "")
        node_name = (node.get("name") or "").strip()
        node_id = node.get("id", "")

        current_path = parents + ([node_name] if node_name else [])

        if node_type in FRAME_TYPES.union(COMPONENT_TYPES):
            path_text = " / ".join([p for p in current_path if p])
            label = f"{path_text} [{node_type}] - {node_id}" if path_text else f"[{node_type}] - {node_id}"
            results.append(
                {
                    "id": node_id,
                    "name": node_name,
                    "type": node_type,
                    "path": path_text,
                    "label": label,
                }
            )

        for child in node.get("children", []) or []:
            walk(child, current_path)

    walk(node_tree, [])

    # Daha temiz kullanım için duplicate label/id kırp
    seen = set()
    unique_results = []

    for item in results:
        key = (item["id"], item["label"])
        if key not in seen:
            seen.add(key)
            unique_results.append(item)

    return unique_results[:500]


def build_design_context(payload: Dict[str, Any]) -> Dict[str, Any]:
    node_tree = payload.get("node_tree", {}) or {}
    file_key = payload.get("file_key", "")
    file_name = payload.get("file_name", "") or ""
    node_id = payload.get("node_id", "")
    screen_name = (node_tree.get("name") or file_name or "Figma Screen").strip()

    texts: List[Dict[str, Any]] = []
    buttons: List[Dict[str, Any]] = []
    inputs: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []
    frames: List[Dict[str, Any]] = []
    components: List[Dict[str, Any]] = []

    total_nodes = 0

    def walk(node: Dict[str, Any], parents: List[str]) -> None:
        nonlocal total_nodes

        if not isinstance(node, dict):
            return

        total_nodes += 1

        node_type = node.get("type", "")
        node_name = (node.get("name") or "").strip()
        path = build_path(parents, node_name)
        text_value = extract_node_text(node)

        entry = {
            "id": node.get("id", ""),
            "name": node_name,
            "type": node_type,
            "text": text_value,
            "path": path,
        }

        if node_type in TEXT_TYPES and text_value:
            texts.append(entry)

        if node_type in FRAME_TYPES:
            frames.append(entry)

        if node_type in COMPONENT_TYPES:
            components.append(entry)

        if is_button_node(node, text_value):
            buttons.append(entry)

        if is_input_node(node, text_value):
            inputs.append(entry)

        if is_link_node(node, text_value):
            links.append(entry)

        for child in node.get("children", []) or []:
            walk(child, parents + ([node_name] if node_name else []))

    walk(node_tree, [])

    context = {
        "source": "figma_design",
        "file_key": file_key,
        "file_name": file_name,
        "node_id": node_id,
        "screen_name": screen_name,
        "summary": {
            "total_nodes": total_nodes,
            "text_count": len(texts),
            "button_count": len(buttons),
            "input_count": len(inputs),
            "link_count": len(links),
            "component_count": len(components),
        },
        "texts": dedupe_entries(texts, max_items=120),
        "buttons": dedupe_entries(buttons, max_items=60),
        "inputs": dedupe_entries(inputs, max_items=60),
        "links": dedupe_entries(links, max_items=60),
        "frames": dedupe_entries(frames, max_items=80),
        "components": dedupe_entries(components, max_items=100),
        "instructions": [
            "Text ve component listelerini ekranın görünen alanlarına göre yorumla.",
            "Figma'da açıkça görünmeyen business rule'ları kesin bilgi gibi yazma.",
            "Buton ve input tespiti heuristiktir; belirsizse needs_confirmation kullan.",
        ],
    }

    return context


def extract_node_text(node: Dict[str, Any]) -> str:
    if node.get("characters"):
        return str(node.get("characters")).strip()

    name = str(node.get("name") or "").strip()
    return name


def build_path(parents: List[str], node_name: str) -> str:
    parts = [p for p in parents if p]
    if node_name:
        parts.append(node_name)
    return " / ".join(parts)


def is_button_node(node: Dict[str, Any], text_value: str) -> bool:
    haystack = f"{node.get('name', '')} {text_value}".lower()
    button_keywords = [
        "button",
        "btn",
        "cta",
        "devam",
        "kaydet",
        "tamam",
        "onayla",
        "giriş",
        "login",
        "submit",
        "play",
        "oynat",
        "ileri",
        "geri",
        "close",
        "kapat",
        "more",
        "chevron",
    ]
    return any(keyword in haystack for keyword in button_keywords)


def is_input_node(node: Dict[str, Any], text_value: str) -> bool:
    haystack = f"{node.get('name', '')} {text_value}".lower()
    input_keywords = [
        "input",
        "textfield",
        "text field",
        "search",
        "textbox",
        "alan",
        "email",
        "şifre",
        "password",
        "otp",
        "phone",
        "telefon",
        "placeholder",
    ]
    return any(keyword in haystack for keyword in input_keywords)


def is_link_node(node: Dict[str, Any], text_value: str) -> bool:
    haystack = f"{node.get('name', '')} {text_value}".lower()
    link_keywords = [
        "link",
        "url",
        "web",
        "detail",
        "detay",
        "learn more",
        "daha fazla",
        "see all",
        "tümü",
    ]
    return any(keyword in haystack for keyword in link_keywords)


def dedupe_entries(items: List[Dict[str, Any]], max_items: int = 100) -> List[Dict[str, Any]]:
    seen = set()
    result = []

    for item in items:
        key = (
            str(item.get("type", "")).lower().strip(),
            str(item.get("name", "")).lower().strip(),
            str(item.get("text", "")).lower().strip(),
            str(item.get("path", "")).lower().strip(),
        )
        if key not in seen:
            seen.add(key)
            result.append(item)

    return result[:max_items]
