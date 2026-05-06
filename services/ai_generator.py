import copy
import json
import math
from typing import Any, Dict, Optional, List

from openai import (
    OpenAI,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
    APIConnectionError,
    APIError,
)

from schemas.output_schema import OUTPUT_JSON_SCHEMA


SYSTEM_PROMPT = """
Sen kıdemli bir İş Analisti, Sistem Analisti ve QA Test Mimarı gibi davran.

Görevin:
1. Verilen bağlamdan analiz dokümanı üretmek.
2. Xray'e import edilebilecek manuel test case'ler üretmek.
3. Gereksinimleri açık, test edilebilir ve sade Türkçe ile yazmak.
4. Kaynaklar arasında çelişki varsa güven sırasına göre yorum yapmak.
5. Kesin olarak çıkarılamayan bilgileri uydurmamak; open_questions veya needs_confirmation olarak işaretlemek.

Kaynak önceliği:
1. Confluence
2. Figma
3. Analiz dokümanı
4. Jira description
5. Jira attachment içerikleri
6. Jira comments

Önemli kurallar:
- Türkçe yaz.
- Gereksiz uzun ve süslü cümleler kurma.
- Test case Summary alanları aksiyon odaklı ve anlaşılır olsun.
- Priority değerleri yalnızca Highest, High, Medium, Low olabilir.
- Test Type her zaman Manual olmalı.
- Jira comment içeriğini doğrudan requirement sayma.
- Attachment içeriğini requirement gibi kullanabilirsin ama belirsizse needs_confirmation işaretle.
- Figma veya görselde görülen bir davranış, Confluence ile çelişiyorsa Confluence önceliklidir.
- Belirsiz noktaları açıkça belirt.
- source_confidence alanını doğru kullan:
  - design_based: kaynaktan doğrudan görülen bilgi
  - assumption: mantıklı ama doğrulanması gereken çıkarım
  - needs_confirmation: Product/Analist onayı gerektiren konu

Analiz dokümanı üretim kuralları:
- Proje özeti kısa ama net olsun.
- Kapsam bölümü neyin analiz edildiğini anlatsın.
- Ekranlar, akışlar, gereksinimler ve iş kuralları tekrar etmeyecek şekilde yazılsın.
- Aynı requirement farklı kaynaklarda tekrar ediyorsa bir kez yaz.
- Açık noktaları mutlaka ayrı listele.

Test case üretim kuralları:
- Her test case en az 1 step içermeli.
- Action alanı kullanıcının yaptığı eylem olmalı.
- Data alanı gerekiyorsa veri içermeli; gerekmiyorsa boş string olabilir.
- Expected Result alanı mutlaka gerçek beklenen sonuç olmalı.
- Mutlu akış, validasyon, hata ve alternatif akışlar düşünülmeli.
- Ancak kaynaktan çıkmayan hata mesajlarını kesinmiş gibi yazma.
- Her test case tek bir davranışı doğrulamalı.
- Gereksiz duplicate test case üretme.

Çoklu ekran görüntüsü varsa:
- Ekranları aynı akışın parçaları gibi değerlendir.
- Ekran sırası net değilse bunu open_questions altında belirt.
- Empty state, error state, popup, success state gibi durumları ayır.
"""


def generate_analysis_and_tests(
    openai_api_key: str,
    model: str,
    design_context: Dict[str, Any],
    image_url: Optional[str] = None,
    image_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Verilen context ve opsiyonel görsellerden
    analiz dokümanı + Xray test case JSON çıktısı üretir.
    """

    if not openai_api_key:
        raise ValueError(
            "OPENAI_API_KEY bulunamadı. "
            "Lokal çalışıyorsan .env dosyasına, Streamlit Cloud kullanıyorsan Secrets alanına eklemelisin."
        )

    if not model:
        model = "gpt-4o"

    client = OpenAI(api_key=openai_api_key)

    all_image_urls: List[str] = []

    if image_urls:
        all_image_urls.extend([url for url in image_urls if url])

    if image_url:
        all_image_urls.append(image_url)

    prepared_context = prepare_context_for_prompt(design_context)

    user_text = f"""
Aşağıdaki bağlama göre analiz dokümanı ve Xray manuel test case listesi üret.

Bağlam Özeti:
{json.dumps(prepared_context, ensure_ascii=False, indent=2)}
"""

    user_content = [
        {
            "type": "input_text",
            "text": user_text,
        }
    ]

    for img in all_image_urls:
        user_content.append(
            {
                "type": "input_image",
                "image_url": img,
                "detail": "high",
            }
        )

    try:
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "figma_analysis_xray_output",
                    "schema": OUTPUT_JSON_SCHEMA,
                    "strict": True,
                }
            },
            max_output_tokens=8000,
        )

    except AuthenticationError as exc:
        raise RuntimeError(f"OpenAI AuthenticationError: {exc}") from exc

    except RateLimitError as exc:
        raise RuntimeError(f"OpenAI RateLimitError: {exc}") from exc

    except BadRequestError as exc:
        raise RuntimeError(f"OpenAI BadRequestError: {exc}") from exc

    except APIConnectionError as exc:
        raise RuntimeError(f"OpenAI APIConnectionError: {exc}") from exc

    except APIError as exc:
        raise RuntimeError(f"OpenAI APIError: {exc}") from exc

    output_text = getattr(response, "output_text", None)

    if not output_text:
        output_text = _extract_output_text(response)

    if not output_text:
        raise RuntimeError("OpenAI cevabı boş döndü.")

    try:
        parsed_result = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenAI JSON çıktısı parse edilemedi.") from exc

    return parsed_result


def generate_analysis_and_tests_for_image_batches(
    openai_api_key: str,
    model: str,
    design_context: Dict[str, Any],
    image_urls: List[str],
    batch_size: int = 6,
) -> Dict[str, Any]:
    """
    Çok sayıda görsel varsa hepsini batch batch analiz eder,
    sonra sonuçları tek analiz çıktısında birleştirir.
    """

    if not image_urls:
        return generate_analysis_and_tests(
            openai_api_key=openai_api_key,
            model=model,
            design_context=design_context,
            image_urls=[],
        )

    total_images = len(image_urls)
    total_batches = math.ceil(total_images / batch_size)
    batch_results: List[Dict[str, Any]] = []

    for batch_index in range(total_batches):
        start = batch_index * batch_size
        end = min(start + batch_size, total_images)

        batch_images = image_urls[start:end]

        batch_context = copy.deepcopy(design_context)
        batch_context["batch_processing"] = {
            "enabled": True,
            "batch_index": batch_index + 1,
            "total_batches": total_batches,
            "total_images": total_images,
            "image_indexes_in_this_batch": list(range(start + 1, end + 1)),
            "instruction": (
                "Bu batch içindeki ekranları ayrı ayrı analiz et. "
                "Genel akışın bir parçası olarak değerlendir."
            ),
        }

        result = generate_analysis_and_tests(
            openai_api_key=openai_api_key,
            model=model,
            design_context=batch_context,
            image_urls=batch_images,
        )

        batch_results.append(result)

    return merge_batch_results_locally(
        batch_results=batch_results,
        original_context=design_context,
        total_images=total_images,
        total_batches=total_batches,
    )


def merge_batch_results_locally(
    batch_results: List[Dict[str, Any]],
    original_context: Dict[str, Any],
    total_images: int,
    total_batches: int,
) -> Dict[str, Any]:
    """
    Batch sonuçlarını lokal olarak birleştirir.
    """

    combined = {
        "analysis_document": {
            "title": "Çok Kaynaklı Analiz ve Gereksinim Dokümanı",
            "project_summary": "",
            "scope": "",
            "user_roles": [],
            "screens": [],
            "functional_requirements": [],
            "business_rules": [],
            "screen_flows": [],
            "open_questions": [],
            "qa_notes": [],
        },
        "test_cases": [],
        "generation_notes": [],
    }

    user_roles_seen = set()
    screens_seen = set()
    flows_seen = set()
    questions_seen = set()
    qa_notes_seen = set()
    test_case_seen = set()

    fr_counter = 1
    br_counter = 1

    project_summary_parts = []
    scope_parts = []

    for batch_no, result in enumerate(batch_results, start=1):
        analysis = result.get("analysis_document", {})

        if analysis.get("project_summary"):
            project_summary_parts.append(analysis["project_summary"])

        if analysis.get("scope"):
            scope_parts.append(analysis["scope"])

        for role in analysis.get("user_roles", []):
            key = normalize_text_key(role)
            if key and key not in user_roles_seen:
                user_roles_seen.add(key)
                combined["analysis_document"]["user_roles"].append(role)

        for screen in analysis.get("screens", []):
            screen_key = normalize_text_key(screen.get("name", ""))
            if not screen_key:
                screen_key = normalize_text_key(
                    json.dumps(screen, ensure_ascii=False))

            if screen_key not in screens_seen:
                screens_seen.add(screen_key)
                combined["analysis_document"]["screens"].append(screen)

        for req in analysis.get("functional_requirements", []):
            new_req = dict(req)
            new_req["id"] = f"FR-{fr_counter:03d}"
            fr_counter += 1
            combined["analysis_document"]["functional_requirements"].append(
                new_req)

        for rule in analysis.get("business_rules", []):
            new_rule = dict(rule)
            new_rule["id"] = f"BR-{br_counter:03d}"
            br_counter += 1
            combined["analysis_document"]["business_rules"].append(new_rule)

        for flow in analysis.get("screen_flows", []):
            key = normalize_text_key(flow.get("flow_name", "")) + "|" + normalize_text_key(
                " ".join(flow.get("steps", []))
            )
            if key and key not in flows_seen:
                flows_seen.add(key)
                combined["analysis_document"]["screen_flows"].append(flow)

        for question in analysis.get("open_questions", []):
            key = normalize_text_key(question)
            if key and key not in questions_seen:
                questions_seen.add(key)
                combined["analysis_document"]["open_questions"].append(
                    question)

        for note in analysis.get("qa_notes", []):
            key = normalize_text_key(note)
            if key and key not in qa_notes_seen:
                qa_notes_seen.add(key)
                combined["analysis_document"]["qa_notes"].append(note)

        for case in result.get("test_cases", []):
            key = normalize_test_case_key(case)
            if key and key not in test_case_seen:
                test_case_seen.add(key)

                new_case = dict(case)
                labels = new_case.get("labels", [])
                if isinstance(labels, list):
                    labels.append(f"batch_{batch_no}")
                    new_case["labels"] = list(dict.fromkeys(labels))

                combined["test_cases"].append(new_case)

        for note in result.get("generation_notes", []):
            combined["generation_notes"].append(f"Batch {batch_no}: {note}")

    combined["analysis_document"]["project_summary"] = build_merged_project_summary(
        project_summary_parts=project_summary_parts,
        total_images=total_images,
        total_batches=total_batches,
        original_context=original_context,
    )

    combined["analysis_document"]["scope"] = build_merged_scope(
        scope_parts=scope_parts,
        total_images=total_images,
        original_context=original_context,
    )

    combined["generation_notes"].insert(
        0,
        f"Tüm görseller analiz edildi. Toplam görsel: {total_images}, batch sayısı: {total_batches}.",
    )

    if original_context.get("user_notes"):
        combined["generation_notes"].insert(
            1,
            f"Kullanıcı notu dikkate alındı: {original_context.get('user_notes')}",
        )

    return combined


def prepare_context_for_prompt(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Modele daha okunabilir ve kompakt context verir.
    """
    prepared = copy.deepcopy(context)

    if prepared.get("source") == "merged_multi_source_context":
        jira_context = prepared.get("jira_context", {})
        figma_contexts = prepared.get("figma_contexts", [])
        confluence_contexts = prepared.get("confluence_contexts", [])
        analysis_doc_contexts = prepared.get("analysis_document_contexts", [])

        prepared["compact_summary"] = {
            "jira_issue_key": jira_context.get("issue_key", ""),
            "jira_summary": jira_context.get("summary", ""),
            "jira_priority": jira_context.get("priority", ""),
            "jira_status": jira_context.get("status", ""),
            "jira_comment_count": len(jira_context.get("comments", [])),
            "jira_attachment_text_count": len(jira_context.get("attachment_texts", [])),
            "jira_url_count": len(jira_context.get("extracted_urls", [])),
            "figma_context_count": len(figma_contexts),
            "confluence_context_count": len(confluence_contexts),
            "analysis_doc_count": len(analysis_doc_contexts),
        }

        prepared["jira_context"] = shrink_jira_context(jira_context)
        prepared["figma_contexts"] = [
            shrink_figma_context(x) for x in figma_contexts[:5]]
        prepared["confluence_contexts"] = [
            shrink_confluence_context(x) for x in confluence_contexts[:5]]
        prepared["analysis_document_contexts"] = [
            shrink_analysis_doc_context(x) for x in analysis_doc_contexts[:5]
        ]

    return prepared


def shrink_jira_context(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": ctx.get("source"),
        "issue_key": ctx.get("issue_key", ""),
        "summary": ctx.get("summary", ""),
        "issue_type": ctx.get("issue_type", ""),
        "priority": ctx.get("priority", ""),
        "status": ctx.get("status", ""),
        "labels": ctx.get("labels", [])[:20],
        "components": ctx.get("components", [])[:20],
        "description": truncate_text(ctx.get("description", ""), 12000),
        "comments": [truncate_text(x, 2000) for x in ctx.get("comments", [])[:10]],
        "custom_fields": [
            {
                "field": item.get("field", ""),
                "text": truncate_text(item.get("text", ""), 2000),
            }
            for item in ctx.get("custom_fields", [])[:20]
        ],
        "attachments": ctx.get("attachments", [])[:20],
        "attachment_texts": [
            {
                "filename": item.get("filename", ""),
                "mime_type": item.get("mime_type", ""),
                "text": truncate_text(item.get("text", ""), 6000),
            }
            for item in ctx.get("attachment_texts", [])[:10]
        ],
        "attachment_processing_summary": ctx.get("attachment_processing_summary", [])[:20],
        "linked_issues": ctx.get("linked_issues", [])[:20],
        "remote_links": ctx.get("remote_links", [])[:20],
        "extracted_urls": ctx.get("extracted_urls", [])[:30],
        "summary_block": ctx.get("summary_block", {}),
    }


def shrink_figma_context(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": ctx.get("source"),
        "source_url": ctx.get("source_url", ""),
        "screen_name": ctx.get("screen_name", ""),
        "summary": ctx.get("summary", {}),
        "texts": ctx.get("texts", [])[:80],
        "buttons": ctx.get("buttons", [])[:40],
        "inputs": ctx.get("inputs", [])[:40],
        "links": ctx.get("links", [])[:40],
        "frames": ctx.get("frames", [])[:40],
        "components": ctx.get("components", [])[:40],
    }


def shrink_confluence_context(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": ctx.get("source"),
        "source_url": ctx.get("source_url", ""),
        "page_id": ctx.get("page_id", ""),
        "title": ctx.get("title", ""),
        "text": truncate_text(ctx.get("text", ""), 14000),
        "links": ctx.get("links", [])[:50],
        "summary": ctx.get("summary", {}),
    }


def shrink_analysis_doc_context(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": ctx.get("source"),
        "filename": ctx.get("filename", ""),
        "screen_name": ctx.get("screen_name", ""),
        "text": truncate_text(ctx.get("text", ""), 14000),
        "detected_sections": ctx.get("detected_sections", [])[:80],
        "detected_requirements": ctx.get("detected_requirements", [])[:120],
        "detected_business_rules": ctx.get("detected_business_rules", [])[:120],
        "detected_flows": ctx.get("detected_flows", [])[:80],
        "detected_user_roles": ctx.get("detected_user_roles", [])[:40],
        "summary": ctx.get("summary", {}),
    }


def build_merged_project_summary(
    project_summary_parts: List[str],
    total_images: int,
    total_batches: int,
    original_context: Dict[str, Any],
) -> str:
    source = original_context.get("source", "")

    if source == "merged_multi_source_context":
        return (
            "Bu doküman, birden fazla kaynaktan toplanan bilgiler kullanılarak üretilmiştir. "
            "Jira task, varsa Confluence ve Figma bağlantıları ile attachment içerikleri birlikte değerlendirilmiştir."
        )

    if total_images > 0:
        return (
            f"{total_images} ekran görüntüsü {total_batches} batch halinde analiz edilmiştir. "
            "Aşağıdaki çıktı tüm ekranların birleştirilmiş analiz sonucudur."
        )

    if project_summary_parts:
        return " ".join(project_summary_parts[:5])

    return "Verilen kaynaklardan analiz dokümanı oluşturulmuştur."


def build_merged_scope(
    scope_parts: List[str],
    total_images: int,
    original_context: Dict[str, Any],
) -> str:
    source = original_context.get("source", "")

    if source == "merged_multi_source_context":
        return (
            "Kapsam; issue içeriği, attachment metinleri, ilişkili bağlantılar, dokümanlar ve "
            "tasarım verilerinden elde edilen ekranlar, iş kuralları, akışlar ve test senaryolarını içerir."
        )

    if total_images > 0:
        return (
            "Bu doküman, yüklenen tüm ekran görüntülerinde görülen ekranlar, akışlar, "
            "iş kuralları ve QA test kapsamını kapsar."
        )

    if scope_parts:
        return " ".join(scope_parts[:5])

    return "Kapsam, verilen bağlamdan çıkarılabilen fonksiyonel gereksinimler ve test kapsamını içerir."


def truncate_text(text: str, max_len: int) -> str:
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n...[TRUNCATED]..."


def normalize_text_key(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    return " ".join(text.split())


def normalize_test_case_key(case: Dict[str, Any]) -> str:
    summary = normalize_text_key(case.get("summary", ""))
    steps = case.get("steps", [])

    if not summary and not steps:
        return ""

    action_blob = " ".join(normalize_text_key(
        step.get("action", "")) for step in steps[:3])
    expected_blob = " ".join(normalize_text_key(
        step.get("expected_result", "")) for step in steps[:2])

    return f"{summary}|{action_blob}|{expected_blob}"


def _extract_output_text(response: Any) -> str:
    parts = []

    output = getattr(response, "output", None)
    if not output:
        return ""

    for item in output:
        content = getattr(item, "content", None)
        if not content:
            continue

        for block in content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)

    return "\n".join(parts).strip()
