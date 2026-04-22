import json
from typing import Any, Dict, Optional

from openai import OpenAI

from schemas.output_schema import OUTPUT_JSON_SCHEMA


SYSTEM_PROMPT = """
Sen kıdemli bir İş Analisti, Sistem Analisti ve QA Test Mimarı gibi davran.

Görevin:
1. Figma ekranından analiz dokümanı üretmek.
2. Gereksinimleri açık, test edilebilir ve iş birimlerinin anlayacağı Türkçe ile yazmak.
3. Xray'e import edilebilecek manuel test case'ler üretmek.
4. Kesin olarak Figma'dan çıkarılamayan konuları uydurmamak; "open_questions" veya "needs_confirmation" olarak işaretlemek.
5. Test case adımlarında Action, Data ve Expected Result alanlarını net ayırmak.

Çok önemli kurallar:
- Türkçe yaz.
- Gereksiz uzun ve süslü cümleler kurma.
- Test case Summary alanları aksiyon odaklı ve anlaşılır olsun.
- Priority değerleri yalnızca Highest, High, Medium, Low olabilir.
- Test Type her zaman Manual olmalı.
- Figma'da net görünmeyen business rule'ları kesin kural gibi yazma.
- Eksik analiz noktalarını "open_questions" altında belirt.
- source_confidence alanını doğru kullan:
  - design_based: Figma tasarımından doğrudan görülen bilgi.
  - assumption: Mantıklı ama doğrulanması gereken varsayım.
  - needs_confirmation: Analist/Product onayı gerektiren konu.

Test case üretim kuralları:
- Her test case en az 1 step içermeli.
- Action alanı kullanıcının yapacağı eylem olmalı.
- Data alanı gerekiyorsa test datası içermeli; gerekmiyorsa boş string olabilir.
- Expected Result alanı mutlaka gerçek beklenen sonuç olmalı.
- Sadece UI'da görünen mutlu akışları değil, validasyon ve hata durumlarını da düşün.
- Ancak Figma'dan net çıkarılamayan hata mesajlarını kesinmiş gibi yazma; needs_confirmation olarak işaretle.
"""


def generate_analysis_and_tests(
    openai_api_key: str,
    model: str,
    design_context: Dict[str, Any],
    image_url: Optional[str] = None
) -> Dict[str, Any]:
    if not openai_api_key:
        raise ValueError("OPENAI_API_KEY bulunamadı.")

    client = OpenAI(api_key=openai_api_key)

    user_text = f"""
Aşağıdaki Figma tasarım bağlamına göre analiz dokümanı ve Xray manuel test case listesi üret.

Figma Tasarım Bağlamı:
{json.dumps(design_context, ensure_ascii=False, indent=2)}
"""

    user_content = [
        {
            "type": "input_text",
            "text": user_text
        }
    ]

    if image_url:
        user_content.append(
            {
                "type": "input_image",
                "image_url": image_url
            }
        )

    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": user_content
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "figma_analysis_xray_output",
                "schema": OUTPUT_JSON_SCHEMA,
                "strict": True
            }
        },
        max_output_tokens=8000
    )

    output_text = getattr(response, "output_text", None)

    if not output_text:
        output_text = _extract_output_text(response)

    if not output_text:
        raise RuntimeError("OpenAI cevabı boş döndü.")

    try:
        return json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenAI JSON çıktısı parse edilemedi: {exc}") from exc


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
