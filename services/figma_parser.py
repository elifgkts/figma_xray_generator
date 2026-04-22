from typing import Any, Dict, List, Optional


BUTTON_KEYWORDS = [
    "button",
    "btn",
    "cta",
    "buton",
    "devam",
    "giriş",
    "giris",
    "kaydet",
    "onayla",
    "iptal",
    "ara",
    "başla",
    "basla",
    "satın al",
    "satin al",
    "tamam",
    "submit",
    "continue",
    "save",
    "cancel"
]

INPUT_KEYWORDS = [
    "input",
    "field",
    "textfield",
    "text field",
    "form",
    "telefon",
    "phone",
    "email",
    "e-posta",
    "eposta",
    "şifre",
    "sifre",
    "password",
    "arama",
    "search",
    "tarih",
    "date",
    "adres",
    "address"
]

LINK_KEYWORDS = [
    "link",
    "forgot",
    "şifremi unuttum",
    "sifremi unuttum",
    "detay",
    "tümünü gör",
    "tumunu gor",
    "yardım",
    "yardim",
    "terms",
    "privacy",
    "kvkk"
]


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _node_name(node: dict) -> str:
    return _safe_str(node.get("name"))


def _node_type(node: dict) -> str:
    return _safe_str(node.get("type"))


def _visible_text(node: dict) -> Optional[str]:
    if node.get("type") == "TEXT":
        characters = _safe_str(node.get("characters"))
        if characters:
            return characters
    return None


def _looks_like_button(name: str, text: str) -> bool:
    combined = f"{name} {text}".lower()
    return any(keyword in combined for keyword in BUTTON_KEYWORDS)


def _looks_like_input(name: str, text: str) -> bool:
    combined = f"{name} {text}".lower()
    return any(keyword in combined for keyword in INPUT_KEYWORDS)


def _looks_like_link(name: str, text: str) -> bool:
    combined = f"{name} {text}".lower()
    return any(keyword in combined for keyword in LINK_KEYWORDS)


def _compact_node(node: dict, depth: int = 0, max_depth: int = 5) -> Optional[dict]:
    if depth > max_depth:
        return None

    node_type = _node_type(node)
    name = _node_name(node)
    text = _visible_text(node)

    children = []
    for child in node.get("children", []) or []:
        compact_child = _compact_node(child, depth + 1, max_depth)
        if compact_child:
            children.append(compact_child)

    has_useful_data = bool(name or text or children)
    if not has_useful_data:
        return None

    result = {
        "id": node.get("id", ""),
        "name": name,
        "type": node_type,
        "text": text or "",
        "children": children
    }

    box = node.get("absoluteBoundingBox")
    if box:
        result["position"] = {
            "x": box.get("x"),
            "y": box.get("y"),
            "width": box.get("width"),
            "height": box.get("height")
        }

    return result


def _walk(node: dict, depth: int = 0) -> List[dict]:
    rows = []

    current = {
        "id": node.get("id", ""),
        "depth": depth,
        "name": _node_name(node),
        "type": _node_type(node),
        "text": _visible_text(node) or ""
    }
    rows.append(current)

    for child in node.get("children", []) or []:
        rows.extend(_walk(child, depth + 1))

    return rows


def build_design_context(payload: dict) -> Dict[str, Any]:
    node_tree = payload.get("node_tree") or payload.get("raw", {}).get("document")

    if not node_tree:
        raise ValueError("Figma node/document verisi bulunamadı.")

    rows = _walk(node_tree)

    texts = []
    buttons = []
    inputs = []
    links = []
    frames = []
    components = []

    for row in rows:
        name = row.get("name", "")
        node_type = row.get("type", "")
        text = row.get("text", "")

        if text:
            texts.append(text)

        if node_type in ["FRAME", "SECTION", "COMPONENT", "INSTANCE", "COMPONENT_SET"]:
            frames.append(name)

        if node_type in ["COMPONENT", "INSTANCE", "COMPONENT_SET"]:
            components.append(name)

        if _looks_like_button(name, text):
            label = text or name
            if label:
                buttons.append(label)

        if _looks_like_input(name, text):
            label = text or name
            if label:
                inputs.append(label)

        if _looks_like_link(name, text):
            label = text or name
            if label:
                links.append(label)

    compact_tree = _compact_node(node_tree)

    unique_texts = _dedupe(texts)
    unique_buttons = _dedupe(buttons)
    unique_inputs = _dedupe(inputs)
    unique_links = _dedupe(links)
    unique_frames = _dedupe([f for f in frames if f])
    unique_components = _dedupe([c for c in components if c])

    context = {
        "file_key": payload.get("file_key"),
        "node_id": payload.get("node_id"),
        "screen_name": node_tree.get("name", "Seçili Figma Ekranı"),
        "summary": {
            "total_nodes": len(rows),
            "text_count": len(unique_texts),
            "button_count": len(unique_buttons),
            "input_count": len(unique_inputs),
            "link_count": len(unique_links),
            "frame_count": len(unique_frames),
            "component_count": len(unique_components)
        },
        "texts": unique_texts[:120],
        "buttons": unique_buttons[:80],
        "inputs": unique_inputs[:80],
        "links": unique_links[:80],
        "frames": unique_frames[:80],
        "components": unique_components[:80],
        "compact_tree": compact_tree
    }

    return context


def extract_candidate_frames(payload: dict) -> List[Dict[str, Any]]:
    """
    Figma dosyasındaki analiz edilebilir ekran/frame adaylarını çıkarır.
    Dosya linki verildiğinde kullanıcıya dropdown sunmak için kullanılır.
    """
    node_tree = payload.get("node_tree") or payload.get("raw", {}).get("document")

    if not node_tree:
        return []

    candidates = []

    def walk(node: dict, page_name: str = "", depth: int = 0) -> None:
        node_type = _node_type(node)
        node_name = _node_name(node)
        node_id = node.get("id", "")

        current_page = page_name

        if node_type == "CANVAS":
            current_page = node_name

        if node_type in ["FRAME", "SECTION", "COMPONENT", "INSTANCE"]:
            children = node.get("children", []) or []
            text_count = _count_text_nodes(node)

            box = node.get("absoluteBoundingBox") or {}
            width = box.get("width") or 0
            height = box.get("height") or 0

            score = 0
            score += 40 if node_type == "FRAME" else 15
            score += 20 if width >= 300 and height >= 300 else 0
            score += min(len(children), 50)
            score += min(text_count * 2, 30)
            score -= depth

            label_page = current_page or "Page"
            label_name = node_name or "(isimsiz frame)"

            candidates.append(
                {
                    "id": node_id,
                    "name": label_name,
                    "type": node_type,
                    "page": label_page,
                    "depth": depth,
                    "children_count": len(children),
                    "text_count": text_count,
                    "width": round(width, 2),
                    "height": round(height, 2),
                    "score": score,
                    "label": f"{label_page} / {label_name} [{node_type}] - {node_id}"
                }
            )

        for child in node.get("children", []) or []:
            walk(child, current_page, depth + 1)

    walk(node_tree)

    candidates.sort(
        key=lambda item: (
            item.get("score", 0),
            item.get("children_count", 0),
            item.get("text_count", 0)
        ),
        reverse=True
    )

    return candidates


def _count_text_nodes(node: dict) -> int:
    count = 1 if node.get("type") == "TEXT" and node.get("characters") else 0

    for child in node.get("children", []) or []:
        count += _count_text_nodes(child)

    return count


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    result = []

    for item in items:
        clean = _safe_str(item)
        if not clean:
            continue

        key = clean.lower()
        if key not in seen:
            seen.add(key)
            result.append(clean)

    return result
