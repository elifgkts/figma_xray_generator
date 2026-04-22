import json
import os
from typing import Any, Optional

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

st.set_page_config(
    page_title="Figma → Analiz Dokümanı + Xray Test Case",
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
    st.title("🧪 Figma → Analiz Dokümanı + Xray Test Case Generator")
    st.caption(
        "Figma ekranındaki görsel ve layer bilgilerinden analiz dokümanı taslağı "
        "ve Xray'e import edilebilir manuel test case CSV'si üretir."
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
    figma_url: str,
    figma_token: str,
    openai_key: str,
    model: str,
    include_figma_image: bool,
    selected_node_id: Optional[str],
) -> None:
    if not figma_url:
        st.error("Lütfen Figma linki gir.")
        st.stop()

    if not figma_token:
        st.error(
            "FIGMA_TOKEN bulunamadı. Lokal .env veya Streamlit Secrets içine eklemelisin."
        )
        st.stop()

    if not openai_key:
        st.error(
            "OPENAI_API_KEY bulunamadı. Lokal .env veya Streamlit Secrets içine eklemelisin."
        )
        st.stop()

    try:
        with st.spinner("Figma verisi okunuyor..."):
            figma_client = FigmaClient(figma_token)
            payload = figma_client.get_design_payload(
                figma_url,
                include_image=include_figma_image,
                selected_node_id=selected_node_id,
            )

            design_context = build_design_context(payload)

            st.session_state.design_context = design_context
            st.session_state.image_url = payload.get("image_url")

        with st.spinner("AI analiz dokümanı ve Xray test case listesi üretiyor..."):
            result = generate_analysis_and_tests(
                openai_api_key=openai_key,
                model=model,
                design_context=st.session_state.design_context,
                image_url=st.session_state.image_url,
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


def show_figma_summary() -> None:
    if not st.session_state.design_context:
        return

    st.divider()
    st.subheader("3. Figma'dan Çıkarılan Özet")

    context = st.session_state.design_context

    metric_cols = st.columns(6)
    metric_cols[0].metric(
        "Toplam Node",
        context.get("summary", {}).get("total_nodes", 0),
    )
    metric_cols[1].metric(
        "Text",
        context.get("summary", {}).get("text_count", 0),
    )
    metric_cols[2].metric(
        "Button",
        context.get("summary", {}).get("button_count", 0),
    )
    metric_cols[3].metric(
        "Input",
        context.get("summary", {}).get("input_count", 0),
    )
    metric_cols[4].metric(
        "Link",
        context.get("summary", {}).get("link_count", 0),
    )
    metric_cols[5].metric(
        "Component",
        context.get("summary", {}).get("component_count", 0),
    )

    if st.session_state.image_url:
        with st.expander("Figma Render Görseli"):
            st.image(st.session_state.image_url, use_container_width=True)

    with st.expander("Sadeleştirilmiş Figma Context JSON"):
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

    st.subheader("1. Figma Linki")

    figma_url = st.text_input(
        "Figma dosya veya ekran/frame linkini gir",
        placeholder="https://www.figma.com/design/....",
    )

    include_figma_image = st.checkbox(
        "Figma ekran görselini de analiz et",
        value=False,
        help=(
            "Açık olursa Figma Images API'ye ek istek atılır. "
            "Rate limit'e takılıyorsan kapalı bırak."
        ),
    )

    col_scan, col_generate, col_info = st.columns([1, 1, 3])

    with col_scan:
        scan_button = st.button(
            "Figma ekranlarını tara",
            use_container_width=True,
        )

    with col_generate:
        generate_button = st.button(
            "Analiz ve Test Case Üret",
            type="primary",
            use_container_width=True,
        )

    with col_info:
        st.caption(
            "Sadece dosya linki verirsen önce ekranları tara. "
            "Node-id içeren spesifik frame linki verirsen doğrudan üretim de yapabilirsin."
        )

    if scan_button:
        handle_figma_scan(figma_url, figma_token)

    selected_node_id = show_candidate_selector()

    if generate_button:
        handle_generation(
            figma_url=figma_url,
            figma_token=figma_token,
            openai_key=openai_key,
            model=model,
            include_figma_image=include_figma_image,
            selected_node_id=selected_node_id,
        )

    show_figma_summary()
    show_results_and_downloads()


if __name__ == "__main__":
    main()
