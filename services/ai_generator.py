import json
from typing import Any, Dict, Optional

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
1. Figma ekranından veya yüklenen ekran görüntüsünden analiz dokümanı üretmek.
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
    image_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    Figma context ve/veya screenshot image input bilgisini alır,
    OpenAI ile analiz dokümanı + Xray test case JSON çıktısı üretir.

    image_url:
    - Figma render URL olabilir.
    - Base64 data URL olabilir.
    - None olabilir.
    """

    if not openai_api_key:
        raise ValueError(
            "OPENAI_API_KEY bulunamadı. "
            "Lokal çalışıyorsan .env dosyasına, Streamlit Cloud kullanıyorsan Secrets alanına eklemelisin."
        )

    if not model:
        model = "gpt-4o"

    client = OpenAI(api_key=openai_api_key)

    user_text = f"""
Aşağıdaki bağlama göre analiz dokümanı ve Xray manuel test case listesi üret.

Bağlam:
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
                "image_url": image_url,
                "detail": "high"
            }
        )

    try:
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


def _extract_output_text(response: Any) -> str:
    """
    SDK sürüm farklarına karşı güvenli fallback.
    """

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
