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
1. Figma ekranından, Figma node/layer bilgisinden veya yüklenen birden fazla ekran görüntüsünden analiz dokümanı üretmek.
2. Gereksinimleri açık, test edilebilir ve iş birimlerinin anlayacağı Türkçe ile yazmak.
3. Xray'e import edilebilecek manuel test case'ler üretmek.
4. Kesin olarak tasarımdan/görselden çıkarılamayan konuları uydurmamak; "open_questions" veya "needs_confirmation" olarak işaretlemek.
5. Test case adımlarında Action, Data ve Expected Result alanlarını net ayırmak.

Çok önemli kurallar:
- Türkçe yaz.
- Gereksiz uzun ve süslü cümleler kurma.
- Test case Summary alanları aksiyon odaklı ve anlaşılır olsun.
- Priority değerleri yalnızca Highest, High, Medium, Low olabilir.
- Test Type her zaman Manual olmalı.
- Tasarımda net görünmeyen business rule'ları kesin kural gibi yazma.
- Eksik analiz noktalarını "open_questions" altında belirt.
- source_confidence alanını doğru kullan:
  - design_based: Tasarım/görsel/layer bilgisinden doğrudan görülen bilgi.
  - assumption: Mantıklı ama doğrulanması gereken varsayım.
  - needs_confirmation: Analist/Product onayı gerektiren konu.

Birden fazla ekran görüntüsü varsa:
- Ekranları bir akışın parçası gibi değerlendir.
- Ekranlar arasında geçiş, popup, empty state, error state, success state ilişkilerini yakala.
- Aynı davranış tekrar ediyorsa gereksiz duplicate test case üretme.
- Her ekran için ayrı ayrı gözlem yap, sonra ortak iş kurallarını çıkar.
- Eğer ekran sırası net değilse bunu open_questions altında belirt.

Test case üretim kuralları:
- Her test case en az 1 step içermeli.
- Action alanı kullanıcının yapacağı eylem olmalı.
- Data alanı gerekiyorsa test datası içermeli; gerekmiyorsa boş string olabilir.
- Expected Result alanı mutlaka gerçek beklenen sonuç olmalı.
- Sadece UI'da görünen mutlu akışları değil, validasyon ve hata durumlarını da düşün.
- Ancak tasarımdan/görselden net çıkarılamayan hata mesajlarını kesinmiş gibi yazma; needs_confirmation olarak işaretle.
- Test case'ler Xray'e import edilebilir manuel test mantığına uygun olmalı.
- Her test case tek bir net davranışı doğrulamalı.
- Çok genel, belirsiz veya "kontrol edilir" gibi zayıf ifadelerden kaçın.
"""


def generate_analysis_and_tests(
    openai_api_key: str,
    model: str,
    design_context: Dict[str, Any],
    image_url: Optional[str] = None,
    image_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
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

    user_text = f"""
Aşağıdaki bağlama göre analiz dokümanı ve Xray manuel test case listesi üret.

Bağlam:
{json.dumps(design_context, ensure_ascii=False, indent=2)}

Not:
- Eğer birden fazla screenshot gönderildiyse, bunları aynı ürün/akışa ait ekranlar olarak değerlendir.
- Görsellerdeki UI elementlerini, metinleri, durumları ve etkileşim ipuçlarını dikkate al.
- Net çıkarılamayan iş kuralı ve akışları açık nokta olarak belirt.
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
        raise RuntimeError(
            "OpenAI API key geçersiz görünüyor. "
            "Lütfen Streamlit Secrets içindeki OPENAI_API_KEY değerini kontrol et. "
            "Yeni bir API key oluşturup app'i yeniden başlatman gerekebilir. "
            "Not: ChatGPT Plus üyeliği OpenAI API key yerine geçmez; API key platform.openai.com üzerinden alınmalıdır."
        ) from exc

    except RateLimitError as exc:
        raise RuntimeError(
            "OpenAI rate limit veya kota sınırına takıldı. "
            "Bir süre bekleyip tekrar dene. Eğer sürekli oluyorsa OpenAI API kullanım limitlerini ve billing durumunu kontrol et."
        ) from exc

    except BadRequestError as exc:
        raise RuntimeError(
            "OpenAI isteği geçersiz görünüyor. "
            "Model adı, JSON schema, görsel formatı veya gönderilen veriyle ilgili bir sorun olabilir. "
            f"Kullanılan model: {model}"
        ) from exc

    except APIConnectionError as exc:
        raise RuntimeError(
            "OpenAI API bağlantısı kurulamadı. "
            "İnternet bağlantısını, Streamlit Cloud erişimini veya geçici OpenAI bağlantı problemlerini kontrol et."
        ) from exc

    except APIError as exc:
        raise RuntimeError(
            "OpenAI tarafında geçici veya servis kaynaklı bir hata oluştu. "
            "Bir süre sonra tekrar dene."
        ) from exc

    output_text = getattr(response, "output_text", None)

    if not output_text:
        output_text = _extract_output_text(response)

    if not output_text:
        raise RuntimeError(
            "OpenAI cevabı boş döndü. "
            "Model çıktısı alınamadı; tekrar deneyebilirsin."
        )

    try:
        parsed_result = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "OpenAI JSON çıktısı parse edilemedi. "
            "Model beklenen JSON formatında cevap üretmemiş olabilir."
        ) from exc

    return parsed_result


def generate_analysis_and_tests_for_image_batches(
    openai_api_key: str,
    model: str,
    design_context: Dict[str, Any],
    image_urls: List[str],
    batch_size: int = 6,
) -> Dict[str, Any]:
    """
    Tüm görselleri analiz eder.
    Görselleri batch'lere böler, her batch için ayrı analiz üretir,
    sonra tüm sonuçları tek analiz dokümanı + tek test case listesinde birleştirir.
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
                "Bu batch içindeki görselleri ayrı ayrı analiz et. "
                "Görüntüler genel akışın parçasıdır. "
                "Bu batch'te gördüğün ekranlar için gereksinim, iş kuralı, akış ve test case üret."
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
    Batch sonuçlarını deterministic şekilde birleştirir.
    Burada ekstra OpenAI çağrısı yapılmaz.
    Böylece tüm görseller analiz edilmiş olur ve tek çıktı oluşur.
    """

    combined = {
        "analysis_document": {
            "title": "Çoklu Ekran Analiz ve Gereksinim Dokümanı",
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

    project_summaries = []
    scopes = []

    user_roles_seen = set()
    screen_seen = set()
    open_question_seen = set()
    qa_note_seen = set()
    flow_seen = set()
    test_summary_seen = set()

    fr_counter = 1
    br_counter = 1

    for batch_no, result in enumerate(batch_results, start=1):
        analysis = result.get("analysis_document", {})

        if analysis.get("project_summary"):
            project_summaries.append(f"Batch {batch_no}: {analysis.get('project_summary')}")

        if analysis.get("scope"):
            scopes.append(f"Batch {batch_no}: {analysis.get('scope')}")

        for role in analysis.get("user_roles", []):
            key = normalize_text_key(role)
            if key and key not in user_roles_seen:
                user_roles_seen.add(key)
                combined["analysis_document"]["user_roles"].append(role)

        for screen in analysis.get("screens", []):
            screen_name = screen.get("name", "")
            key = normalize_text_key(screen_name)
            if not key:
                key = normalize_text_key(json.dumps(screen, ensure_ascii=False))

            if key not in screen_seen:
                screen_seen.add(key)
                combined["analysis_document"]["screens"].append(screen)

        for req in analysis.get("functional_requirements", []):
            new_req = dict(req)
            new_req["id"] = f"FR-{fr_counter:03d}"
            fr_counter += 1
            combined["analysis_document"]["functional_requirements"].append(new_req)

        for rule in analysis.get("business_rules", []):
            new_rule = dict(rule)
            new_rule["id"] = f"BR-{br_counter:03d}"
            br_counter += 1
            combined["analysis_document"]["business_rules"].append(new_rule)

        for flow in analysis.get("screen_flows", []):
            key = normalize_text_key(flow.get("flow_name", "")) + "|" + normalize_text_key(
                " ".join(flow.get("steps", []))
            )
            if key and key not in flow_seen:
                flow_seen.add(key)
                combined["analysis_document"]["screen_flows"].append(flow)

        for question in analysis.get("open_questions", []):
            key = normalize_text_key(question)
            if key and key not in open_question_seen:
                open_question_seen.add(key)
                combined["analysis_document"]["open_questions"].append(question)

        for note in analysis.get("qa_notes", []):
            key = normalize_text_key(note)
            if key and key not in qa_note_seen:
                qa_note_seen.add(key)
                combined["analysis_document"]["qa_notes"].append(note)

        for case in result.get("test_cases", []):
            summary = case.get("summary", "")
            key = normalize_text_key(summary)

            if key and key not in test_summary_seen:
                test_summary_seen.add(key)

                new_case = dict(case)
                labels = new_case.get("labels", [])
                if isinstance(labels, list):
                    labels.append(f"batch_{batch_no}")
                    new_case["labels"] = list(dict.fromkeys(labels))

                combined["test_cases"].append(new_case)

        for note in result.get("generation_notes", []):
            combined["generation_notes"].append(f"Batch {batch_no}: {note}")

    combined["analysis_document"]["project_summary"] = (
        f"{total_images} ekran görüntüsü {total_batches} batch halinde analiz edilmiştir. "
        "Aşağıdaki doküman, tüm batch sonuçlarının birleştirilmiş analiz çıktısıdır. "
        + " ".join(project_summaries[:5])
    )

    combined["analysis_document"]["scope"] = (
        "Bu doküman, yüklenen tüm ekran görüntülerinde görülen UI elementleri, ekran akışları, "
        "iş kuralları, açık noktalar ve QA test kapsamını içerir. "
        + " ".join(scopes[:5])
    )

    combined["generation_notes"].insert(
        0,
        f"Tüm görseller analiz edildi. Toplam görsel: {total_images}, batch sayısı: {total_batches}, batch boyutu: yaklaşık 6 görsel.",
    )

    if original_context.get("user_notes"):
        combined["generation_notes"].insert(
            1,
            f"Kullanıcı notu dikkate alındı: {original_context.get('user_notes')}",
        )

    return combined


def normalize_text_key(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip().lower()
    text = " ".join(text.split())

    return text


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
