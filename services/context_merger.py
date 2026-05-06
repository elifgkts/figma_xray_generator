from typing import Any, Dict, List


def merge_source_contexts(
    jira_context: Dict[str, Any] | None = None,
    figma_contexts: List[Dict[str, Any]] | None = None,
    confluence_contexts: List[Dict[str, Any]] | None = None,
    analysis_doc_contexts: List[Dict[str, Any]] | None = None,
    user_notes: str = "",
) -> Dict[str, Any]:
    figma_contexts = figma_contexts or []
    confluence_contexts = confluence_contexts or []
    analysis_doc_contexts = analysis_doc_contexts or []

    return {
        "source": "merged_multi_source_context",
        "evidence_priority": [
            "confluence",
            "figma",
            "analysis_document",
            "jira_description",
            "jira_comments",
        ],
        "jira_context": jira_context or {},
        "figma_contexts": figma_contexts,
        "confluence_contexts": confluence_contexts,
        "analysis_document_contexts": analysis_doc_contexts,
        "user_notes": user_notes or "",
        "summary": {
            "has_jira": bool(jira_context),
            "figma_context_count": len(figma_contexts),
            "confluence_context_count": len(confluence_contexts),
            "analysis_doc_count": len(analysis_doc_contexts),
        },
        "instructions": [
            "Kaynak çelişirse evidence_priority sırasını dikkate al.",
            "Comments içeriğini doğrudan kesin requirement sayma.",
            "Net olmayan noktaları open_questions altında belirt.",
            "Tüm kaynakları tek analiz dokümanı ve tek test case kümesinde birleştir.",
        ],
    }
