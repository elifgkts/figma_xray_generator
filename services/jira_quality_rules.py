from typing import Dict, List


CONFLUENCE_HINTS = [
    "atlassian.net/wiki",
    "/wiki/",
    "confluence",
    "viewpage.action",
]

FIGMA_HINTS = [
    "figma.com",
]

WEAK_DESCRIPTION_HINTS = [
    "geliştirilecek",
    "kontrol edilecek",
    "düzeltilecek",
    "bakılacak",
    "incelenecek",
    "fix",
    "bug fix",
    "todo",
    "tbd",
    "test",
]

ACCEPTANCE_HINTS = [
    "acceptance",
    "kabul kriter",
    "acceptance criteria",
    "criteria",
    "beklenen",
    "should",
    "must",
    "olmalı",
    "gerekir",
    "zorunlu",
]


def has_confluence_link(jira_context: Dict) -> bool:
    return any(_contains_any(url, CONFLUENCE_HINTS) for url in jira_context.get("extracted_urls", []))


def has_figma_link(jira_context: Dict) -> bool:
    return any(_contains_any(url, FIGMA_HINTS) for url in jira_context.get("extracted_urls", []))


def has_description(jira_context: Dict) -> bool:
    return bool((jira_context.get("description") or "").strip())


def has_attachments(jira_context: Dict) -> bool:
    return len(jira_context.get("attachments", []) or []) > 0


def has_comments(jira_context: Dict) -> bool:
    return len(jira_context.get("comments", []) or []) > 0


def has_acceptance_like_content(jira_context: Dict) -> bool:
    text_blobs: List[str] = []

    description = jira_context.get("description", "") or ""
    if description:
        text_blobs.append(description)

    for item in jira_context.get("custom_fields", []) or []:
        text = item.get("text", "") or ""
        if text:
            text_blobs.append(text)

    combined = "\n".join(text_blobs).lower()

    return any(hint in combined for hint in ACCEPTANCE_HINTS)


def description_quality(jira_context: Dict) -> Dict:
    description = (jira_context.get("description") or "").strip()

    if not description:
        return {
            "level": "missing",
            "score": 0,
            "reason": "Description boş.",
        }

    lowered = description.lower()
    char_count = len(description)
    line_count = len([x for x in description.splitlines() if x.strip()])

    if char_count < 40:
        return {
            "level": "weak",
            "score": 20,
            "reason": "Description çok kısa.",
        }

    if any(h in lowered for h in WEAK_DESCRIPTION_HINTS) and char_count < 120:
        return {
            "level": "weak",
            "score": 25,
            "reason": "Description çok genel ve aksiyon ifadesi dışında bağlam içermiyor.",
        }

    if char_count < 120 and line_count <= 2:
        return {
            "level": "limited",
            "score": 45,
            "reason": "Description var ama kapsam ve davranış bilgisi sınırlı.",
        }

    if has_acceptance_like_content(jira_context):
        return {
            "level": "good",
            "score": 80,
            "reason": "Description ve/veya custom field içeriğinde acceptance benzeri bağlam var.",
        }

    if char_count >= 120:
        return {
            "level": "moderate",
            "score": 60,
            "reason": "Description yeterli uzunlukta ancak acceptance benzeri açık kriterler zayıf.",
        }

    return {
        "level": "limited",
        "score": 45,
        "reason": "Description sınırlı bağlam içeriyor.",
    }


def compute_readiness_score(jira_context: Dict) -> int:
    score = 0

    if has_confluence_link(jira_context):
        score += 25

    if has_figma_link(jira_context):
        score += 25

    desc_quality = description_quality(jira_context)
    score += min(desc_quality["score"], 25)

    if has_acceptance_like_content(jira_context):
        score += 15

    if has_attachments(jira_context):
        score += 5

    if has_comments(jira_context):
        score += 5

    return min(score, 100)


def build_missing_items(jira_context: Dict) -> List[str]:
    missing = []

    if not has_confluence_link(jira_context):
        missing.append("Confluence link yok")

    if not has_figma_link(jira_context):
        missing.append("Figma link yok")

    if not has_description(jira_context):
        missing.append("Description yok")
    else:
        desc_quality = description_quality(jira_context)
        if desc_quality["level"] in {"weak", "limited", "missing"}:
            missing.append("Description yeterli değil")

    if not has_acceptance_like_content(jira_context):
        missing.append("Acceptance benzeri açıklık yok")

    return missing


def build_recommendation(jira_context: Dict) -> str:
    missing = build_missing_items(jira_context)

    if not missing:
        return "Analiz açısından temel alanlar yeterli görünüyor."

    return " / ".join(missing)


def is_risky_for_analysis(jira_context: Dict) -> bool:
    score = compute_readiness_score(jira_context)
    return score < 60


def _contains_any(text: str, hints: List[str]) -> bool:
    lowered = (text or "").lower()
    return any(h in lowered for h in hints)
