import base64
import json
import os
from typing import Any, Optional, List

import streamlit as st
from dotenv import load_dotenv

from services.figma_client import FigmaClient, FigmaRateLimitError
from services.figma_parser import build_design_context, extract_candidate_frames
from services.ai_generator import generate_analysis_and_tests
from services.exporters import (
    to_markdown,
    to_pdf_bytes,
    to_xray_csv_bytes,
    to_json_bytes,
    test_cases_to_dataframe,
)

load_dotenv()

MAX_SCREENSHOTS = 6

st.set_page_config(
    page_title="Figma / Screenshot → Analiz + Xray",
    page_icon="🧪",
    layout="wide",
)


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Lokal: .env
    Streamlit Cloud: Secrets
    """
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass

    return os.getenv(key, default)


def safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON formatı hatalı: {exc}") from exc


def init_state() -> None:
    st.session_state.setdefault("design_context", None)
    st.session_state.setdefault("image_url", None)
    st.session_state.setdefault("result_json", None)
    st.session_state.setdefault("editable_json_text", "")
    st.session_state.setdefault("figma_candidates", [])
    st.session_state.setdefault("figma_file_key", None)


def show_header() -> None:
    st.title("🧪 Figma / Screenshot → Analiz Dokümanı + Xray Test Case Generator")
    st.caption(
        "Figma node/layer bilgisi veya yüklenen birden fazla ekran görüntüsünden "
        "analiz dokümanı taslağı ve Xray'e import edilebilir manuel test case CSV'si üretir."
    )


def show_sidebar() -> tuple[str, str, str]:
    st.sidebar.header("⚙️ Ayarlar")

    figma_token = get_secret("FIGMA_TOKEN")
    openai_key = get_secret("OPENAI_API_KEY")
    default_model = get_secret("OPENAI_MODEL", "gpt-4o")

    model = st.sidebar.text_input(
        "OpenAI Model",
        value=default_model or "gpt-4o",
        help="Örn: gpt-4o, gpt-4.1-mini vb.",
    )

    st.sidebar.divider()

    st.sidebar.write("**Token Durumu**")
    st.sidebar.write("Figma Token:", "✅ Var" if figma_token else "❌ Yok")
    st.sidebar.write("OpenAI API Key:", "✅ Var" if openai_key else "❌ Yok")

    st.sidebar.info(
        "GitHub'a token koyma. Streamlit Cloud'da App Settings > Secrets alanına ekle."
    )

    return figma_token, openai_key, model


def uploaded_image_to_data_url(uploaded_file) -> str:
    """
    Streamlit file_uploader ile gelen image dosyasını base64 data URL'e çevirir.
    """
    if uploaded_file is None:
        return ""

    mime_type = uploaded_file.type or "image/png"
    raw_bytes = uploaded_file.getvalue()
    encoded = base64.b64encode(raw_bytes).decode("utf-8")

    return f"data:{mime_type};base64,{encoded}"


def uploaded_images_to_data_urls(uploaded_files: List[Any]) -> List[str]:
    if not uploaded_files:
        return []

    limited_files = uploaded_files[:MAX_SCREENSHOTS]
    return [uploaded_image_to_data_url(file) for file in limited_files]


def build_screenshot_context(
    uploaded_files: List[Any],
    user_notes: str,
    mode: str,
) -> dict:
    files = uploaded_files[:MAX_SCREENSHOTS] if uploaded_files else []

    screenshots = []

    for index, file in enumerate(files, start=1):
        screenshots.append(
            {
                "index": index,
                "filename": file.name,
                "content_type": file.type,
                "note": (
                    "Bu ekran görüntüsü kullanıcı tarafından manuel yüklendi. "
                    "Figma API kullanılmadan analiz edilebilir."
                ),
            }
        )

    return {
        "source": "screenshot_upload",
        "mode": mode,
        "screen_name": "Çoklu ekran görüntüsü analizi",
        "user_notes": user_notes or "",
        "summary": {
            "input_type": "multiple_images",
            "figma_api_used": False,
            "screenshot_count": len(files),
            "max_screenshot_count": MAX_SCREENSHOTS,
            "note": (
                "Bu analiz Figma API node/layer verisi olmadan, "
                "yüklenen ekran görüntüleri üzerinden üretilmiştir."
            ),
        },
        "screenshots": screenshots,
        "instructions": [
            "Görsellerde görünen UI elementlerini analiz et.",
            "Birden fazla ekran varsa ekranları aynı ürün akışının parçaları olarak değerlendir.",
            "Ekranlar arasında olası geçişleri, popup/empty/error/success state ilişkilerini çıkar.",
            "Görselden net çıkarılamayan business rule'ları kesin bilgi gibi yazma.",
            "Belirsiz noktaları open_questions altında belirt.",
            "Test case'leri Xray manuel test case formatına uygun üret.",
        ],
    }


def handle_figma_scan(figma_url: str, figma_token: str) -> None:
    if not figma_url:
        st.error("Lütfen Figma linki gir.")
        st.stop()

    if not figma_token:
        st.error("FIGMA_TOKEN bulunamadı. Lokal .env veya Streamlit Secrets içine eklemelisin.")
        st.stop()

    try:
        with st.spinner("Figma dosyasındaki ekranlar taranıyor..."):
            figma_client = FigmaClient(figma_token)
            outline_payload = figma_client.get_design_outline_payload(
                figma_url,
                depth=3,
            )

            candidates = extract_candidate_frames(outline_payload)

            st.session_state["figma_file_key"] = outline_payload.get("file_key")
            st.session_state["figma_candidates"] = candidates

        if candidates:
            st.success(f"{len(candidates)} ekran/frame adayı bulundu.")
        else:
            st.warning("Frame adayı bulunamadı. Linkin erişilebilir olduğundan emin ol.")

    except FigmaRateLimitError as exc:
        st.error(str(exc))

        if exc.retry_after:
            st.warning(
                f"Tekrar denemeden önce önerilen bekleme süresi: {exc.retry_after} saniye"
            )

        if exc.upgrade_link:
            st.info(
                f"Figma plan/seat limitiyle ilişkili olabilir. "
                f"Upgrade/settings linki: {exc.upgrade_link}"
            )

        st.stop()

    except Exception as exc:
        st.error(f"Figma ekran tarama sırasında hata oluştu: {exc}")
        st.stop()


def handle_generation(
    mode: str,
    figma_url: str,
    figma_token: str,
    openai_key: str,
    model: str,
    selected_node_id: Optional[str],
    uploaded_screenshots: List[Any],
    user_notes: str,
) -> None:
    if not openai_key:
        st.error(
            "OPENAI_API_KEY bulunamadı. Lokal .env veya Streamlit Secrets içine eklemelisin."
        )
        st.stop()

    uses_figma = mode in ["Figma API Modu", "Hibrit Mod"]
    uses_screenshot = mode in ["Screenshot Modu", "Hibrit Mod"]

    if uses_figma and not figma_url:
        st.error("Bu mod için Figma linki gerekli.")
        st.stop()

    if uses_figma and not figma_token:
        st.error(
            "Bu mod için FIGMA_TOKEN gerekli. Lokal .env veya Streamlit Secrets içine eklemelisin."
        )
        st.stop()

    if uses_screenshot and not uploaded_screenshots:
        st.error("Bu mod için en az 1 ekran görüntüsü yüklemelisin.")
        st.stop()

    if uploaded_screenshots and len(uploaded_screenshots) > MAX_SCREENSHOTS:
        st.warning(
            f"{len(uploaded_screenshots)} görsel yüklendi. "
            f"Maliyet ve performans için ilk {MAX_SCREENSHOTS} görsel kullanılacak."
        )

    try:
        image_data_urls: List[str] = []
        design_context = None

        if uses_figma:
            with st.spinner("Figma node/layer verisi okunuyor..."):
                figma_client = FigmaClient(figma_token)

                # Önemli:
                # include_image=False. Böylece Figma Images API kullanılmaz.
                # Görsel analiz gerekiyorsa kullanıcı screenshot yükler.
                payload = figma_client.get_design_payload(
                    figma_url,
                    include_image=False,
                    selected_node_id=selected_node_id,
                )

                design_context = build_design_context(payload)

                if user_notes:
                    design_context["user_notes"] = user_notes

                design_context["generation_mode"] = mode
                design_context["figma_image_api_used"] = False

                st.session_state.design_context = design_context
                st.session_state.image_url = None

        if uses_screenshot:
            image_data_urls = uploaded_images_to_data_urls(uploaded_screenshots)

            if not design_context:
                design_context = build_screenshot_context(
                    uploaded_files=uploaded_screenshots,
                    user_notes=user_notes,
                    mode=mode,
                )
            else:
                design_context["screenshot"] = {
                    "uploaded": True,
                    "count": min(len(uploaded_screenshots), MAX_SCREENSHOTS),
                    "filenames": [
                        file.name for file in uploaded_screenshots[:MAX_SCREENSHOTS]
                    ],
                    "note": (
                        "Bu modda Figma node/layer bilgisi ile manuel yüklenen ekran görüntüleri birlikte kullanılmıştır. "
                        "Figma Images API kullanılmamıştır."
                    ),
                }

        if not design_context:
            st.error("Analiz için kullanılacak bağlam oluşturulamadı.")
            st.stop()

        st.session_state.design_context = design_context

        with st.spinner("AI analiz dokümanı ve Xray test case listesi üretiyor..."):
            result = generate_analysis_and_tests(
                openai_api_key=openai_key,
                model=model,
                design_context=design_context,
                image_urls=image_data_urls,
            )

            st.session_state.result_json = result
            st.session_state.editable_json_text = json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )

        st.success("Analiz ve test case üretimi tamamlandı.")

    except FigmaRateLimitError as exc:
        st.error(str(exc))

        if exc.retry_after:
            st.warning(
                f"Tekrar denemeden önce önerilen bekleme süresi: {exc.retry_after} saniye"
            )

        if exc.upgrade_link:
            st.info(
                f"Figma plan/seat limitiyle ilişkili olabilir. "
                f"Upgrade/settings linki: {exc.upgrade_link}"
            )

        st.info(
            "Figma limitine takıldığın için Screenshot Modu ile devam edebilirsin. "
            "Bu modda Figma API hiç kullanılmaz."
        )

        st.stop()

    except Exception as exc:
        st.error(f"İşlem sırasında hata oluştu: {exc}")
        st.stop()


def show_candidate_selector() -> Optional[str]:
    selected_node_id = None

    if st.session_state.get("figma_candidates"):
        st.divider()
        st.subheader("2. Bulunan Figma Ekranları")

        candidate_labels = [
            item["label"] for item in st.session_state["figma_candidates"]
        ]

        selected_label = st.selectbox(
            "Analiz edilecek ekran/frame seç",
            options=candidate_labels,
        )

        selected_candidate = next(
            item for item in st.session_state["figma_candidates"]
            if item["label"] == selected_label
        )

        selected_node_id = selected_candidate["id"]

        with st.expander("Seçilen frame bilgisi"):
            st.json(selected_candidate)

    return selected_node_id


def show_analysis_context() -> None:
    if not st.session_state.design_context:
        return

    st.divider()
    st.subheader("3. Analiz İçin Kullanılan Bağlam")

    context = st.session_state.design_context
    summary = context.get("summary", {})

    if "total_nodes" in summary:
        metric_cols = st.columns(6)
        metric_cols[0].metric("Toplam Node", summary.get("total_nodes", 0))
        metric_cols[1].metric("Text", summary.get("text_count", 0))
        metric_cols[2].metric("Button", summary.get("button_count", 0))
        metric_cols[3].metric("Input", summary.get("input_count", 0))
        metric_cols[4].metric("Link", summary.get("link_count", 0))
        metric_cols[5].metric("Component", summary.get("component_count", 0))
    else:
        st.info("Bu analiz screenshot üzerinden üretildiği için Figma node metrikleri bulunmuyor.")
        if summary.get("screenshot_count") is not None:
            st.metric("Kullanılan Screenshot", summary.get("screenshot_count", 0))

    with st.expander("Kullanılan Context JSON"):
        st.json(context)


def show_results_and_downloads() -> None:
    if not st.session_state.result_json:
        return

    st.divider()
    st.subheader("4. AI Çıktısı / Düzenleme Alanı")

    st.caption(
        "Buradaki JSON'u manuel düzeltebilirsin. "
        "CSV, PDF ve Markdown çıktıları bu düzenlenmiş JSON üzerinden oluşturulur."
    )

    st.session_state.editable_json_text = st.text_area(
        "JSON Çıktısı",
        value=st.session_state.editable_json_text,
        height=450,
    )

    try:
        edited_result = safe_json_loads(st.session_state.editable_json_text)
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    st.divider()
    st.subheader("5. Test Case Önizleme")

    df = test_cases_to_dataframe(edited_result)

    if df.empty:
        st.warning("Test case bulunamadı.")
    else:
        st.dataframe(df, use_container_width=True)

    st.divider()
    st.subheader("6. İndirilebilir Çıktılar")

    try:
        markdown_text = to_markdown(edited_result)
        pdf_bytes = to_pdf_bytes(edited_result)
        csv_bytes = to_xray_csv_bytes(edited_result)
        json_bytes = to_json_bytes(edited_result)
    except Exception as exc:
        st.error(f"Çıktı dosyaları oluşturulurken hata oluştu: {exc}")
        st.stop()

    download_cols = st.columns(4)

    with download_cols[0]:
        st.download_button(
            label="📄 Analiz Markdown İndir",
            data=markdown_text.encode("utf-8-sig"),
            file_name="analiz_dokumani.md",
            mime="text/markdown",
            use_container_width=True,
        )

    with download_cols[1]:
        st.download_button(
            label="📕 Analiz PDF İndir",
            data=pdf_bytes,
            file_name="analiz_dokumani.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    with download_cols[2]:
        st.download_button(
            label="🧪 Xray CSV İndir",
            data=csv_bytes,
            file_name="xray_import_test_cases.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with download_cols[3]:
        st.download_button(
            label="🧾 JSON İndir",
            data=json_bytes,
            file_name="figma_analysis_output.json",
            mime="application/json",
            use_container_width=True,
        )

    with st.expander("Analiz Dokümanı Önizleme"):
        st.markdown(markdown_text)


def main() -> None:
    init_state()
    show_header()

    figma_token, openai_key, model = show_sidebar()

    st.subheader("1. Çalışma Modu")

    mode = st.radio(
        "Nasıl analiz üretmek istiyorsun?",
        options=[
            "Figma API Modu",
            "Screenshot Modu",
            "Hibrit Mod",
        ],
        horizontal=True,
        help=(
            "Figma API Modu node/layer bilgisi kullanır. "
            "Screenshot Modu Figma API kullanmaz. "
            "Hibrit Mod ikisini birlikte kullanır ama Figma Images API'ye gitmez."
        ),
    )

    st.divider()

    figma_url = ""
    uploaded_screenshots: List[Any] = []

    if mode in ["Figma API Modu", "Hibrit Mod"]:
        st.subheader("Figma Linki")

        figma_url = st.text_input(
            "Figma dosya veya ekran/frame linkini gir",
            placeholder="https://www.figma.com/design/....",
        )

        col_scan, col_info = st.columns([1, 4])

        with col_scan:
            scan_button = st.button(
                "Figma ekranlarını tara",
                use_container_width=True,
            )

        with col_info:
            st.caption(
                "Sadece dosya linki verirsen önce ekranları tara. "
                "Node-id içeren spesifik frame linki verirsen doğrudan üretim de yapabilirsin. "
                "Bu versiyon Figma Images API kullanmaz."
            )

        if scan_button:
            handle_figma_scan(figma_url, figma_token)

    if mode in ["Screenshot Modu", "Hibrit Mod"]:
        st.subheader("Ekran Görüntüleri")

        uploaded_screenshots = st.file_uploader(
            "Figma ekran görüntülerini yükle",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            help=(
                "Birden fazla ekran yükleyebilirsin: ana ekran, popup, empty state, error state, success state vb. "
                f"Maliyet/performans için ilk {MAX_SCREENSHOTS} görsel kullanılacak."
            ),
        )

        if uploaded_screenshots:
            if len(uploaded_screenshots) > MAX_SCREENSHOTS:
                st.warning(
                    f"{len(uploaded_screenshots)} görsel yüklendi. "
                    f"İlk {MAX_SCREENSHOTS} görsel analizde kullanılacak."
                )

            st.caption("Yüklenen ekran görüntüleri:")

            preview_cols = st.columns(3)

            for index, uploaded_file in enumerate(uploaded_screenshots[:MAX_SCREENSHOTS]):
                with preview_cols[index % 3]:
                    st.image(
                        uploaded_file,
                        caption=f"{index + 1}. {uploaded_file.name}",
                        use_container_width=True,
                    )

    user_notes = st.text_area(
        "Ek bilgi / notlar",
        placeholder=(
            "Örn: Bu ekranlar fizy son dinlenenler akışına aittir. "
            "Liste itemlarına tıklanınca ilgili detay ekranına gidilir. "
            "Chevron olan itemlar liste/albüm içeriğini açar. "
            "Test case'ler Xray Manual Test formatına uygun olmalı."
        ),
        height=120,
    )

    selected_node_id = show_candidate_selector()

    st.divider()

    generate_button = st.button(
        "Analiz ve Test Case Üret",
        type="primary",
        use_container_width=True,
    )

    if generate_button:
        handle_generation(
            mode=mode,
            figma_url=figma_url,
            figma_token=figma_token,
            openai_key=openai_key,
            model=model,
            selected_node_id=selected_node_id,
            uploaded_screenshots=uploaded_screenshots,
            user_notes=user_notes,
        )

    show_analysis_context()
    show_results_and_downloads()


if __name__ == "__main__":
    main()
