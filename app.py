import json
import os
from typing import Any, Optional

import streamlit as st
from dotenv import load_dotenv

from services.figma_client import FigmaClient, FigmaRateLimitError
from services.figma_parser import build_design_context
from services.ai_generator import generate_analysis_and_tests
from services.exporters import (
    to_markdown,
    to_xray_csv_bytes,
    to_json_bytes,
    test_cases_to_dataframe
)


load_dotenv()


st.set_page_config(
    page_title="Figma → Analiz Dokümanı + Xray Test Case",
    page_icon="🧪",
    layout="wide"
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
        help="Örn: gpt-4o, gpt-4.1-mini vb."
    )

    st.sidebar.divider()

    st.sidebar.write("**Token Durumu**")
    st.sidebar.write("Figma Token:", "✅ Var" if figma_token else "❌ Yok")
    st.sidebar.write("OpenAI API Key:", "✅ Var" if openai_key else "❌ Yok")

    st.sidebar.info(
        "GitHub'a token koyma. Streamlit Cloud'da App Settings > Secrets alanına ekle."
    )

    return figma_token, openai_key, model


def main() -> None:
    init_state()
    show_header()

    figma_token, openai_key, model = show_sidebar()

    st.subheader("1. Figma Linki")

    figma_url = st.text_input(
        "Figma ekran/frame linkini gir",
        placeholder="https://www.figma.com/design/....?node-id=123-456"
    )
    include_figma_image = st.checkbox(
    "Figma ekran görselini de analiz et",
    value=False,
    help="Açık olursa Figma Images API'ye ek istek atılır. Rate limit'e takılıyorsan kapalı bırak."
)

    col1, col2 = st.columns([1, 3])

    with col1:
        generate_button = st.button(
            "Analiz ve Test Case Üret",
            type="primary",
            use_container_width=True
        )

    with col2:
        st.caption(
            "En iyi sonuç için spesifik bir Frame/Screen linki kullan. "
            "Tüm dosya linki verilirse veri çok geniş olabilir."
        )

    if generate_button:
        if not figma_url:
            st.error("Lütfen Figma linki gir.")
            st.stop()

        if not figma_token:
            st.error(
                "FIGMA_TOKEN bulunamadı. Lokal .env veya Streamlit Secrets içine eklemelisin.")
            st.stop()

        if not openai_key:
            st.error(
                "OPENAI_API_KEY bulunamadı. Lokal .env veya Streamlit Secrets içine eklemelisin.")
            st.stop()

        try:
            with st.spinner("Figma verisi okunuyor..."):
                figma_client = FigmaClient(figma_token)
                payload = figma_client.get_design_payload(
    figma_url,
    include_image=include_figma_image
)
                design_context = build_design_context(payload)

                st.session_state.design_context = design_context
                st.session_state.image_url = payload.get("image_url")

            with st.spinner("AI analiz dokümanı ve Xray test case listesi üretiyor..."):
                result = generate_analysis_and_tests(
                    openai_api_key=openai_key,
                    model=model,
                    design_context=st.session_state.design_context,
                    image_url=st.session_state.image_url
                )

                st.session_state.result_json = result
                st.session_state.editable_json_text = json.dumps(
                    result,
                    ensure_ascii=False,
                    indent=2
                )

            st.success("Analiz ve test case üretimi tamamlandı.")

                except FigmaRateLimitError as exc:
            st.error(str(exc))

            if exc.retry_after:
                st.warning(f"Tekrar denemeden önce önerilen bekleme süresi: {exc.retry_after} saniye")

            if exc.upgrade_link:
                st.info(f"Figma plan/seat limitiyle ilişkili olabilir. Upgrade/settings linki: {exc.upgrade_link}")

            st.stop()

        except Exception as exc:
            st.error(f"İşlem sırasında hata oluştu: {exc}")
            st.stop()

    if st.session_state.design_context:
        st.divider()
        st.subheader("2. Figma'dan Çıkarılan Özet")

        context = st.session_state.design_context

        metric_cols = st.columns(6)
        metric_cols[0].metric("Toplam Node", context.get(
            "summary", {}).get("total_nodes", 0))
        metric_cols[1].metric("Text", context.get(
            "summary", {}).get("text_count", 0))
        metric_cols[2].metric("Button", context.get(
            "summary", {}).get("button_count", 0))
        metric_cols[3].metric("Input", context.get(
            "summary", {}).get("input_count", 0))
        metric_cols[4].metric("Link", context.get(
            "summary", {}).get("link_count", 0))
        metric_cols[5].metric("Component", context.get(
            "summary", {}).get("component_count", 0))

        if st.session_state.image_url:
            with st.expander("Figma Render Görseli"):
                st.image(st.session_state.image_url, use_container_width=True)

        with st.expander("Sadeleştirilmiş Figma Context JSON"):
            st.json(context)

    if st.session_state.result_json:
        st.divider()
        st.subheader("3. AI Çıktısı / Düzenleme Alanı")

        st.caption(
            "Buradaki JSON'u manuel düzeltebilirsin. "
            "CSV ve Markdown çıktıları bu düzenlenmiş JSON üzerinden oluşturulur."
        )

        st.session_state.editable_json_text = st.text_area(
            "JSON Çıktısı",
            value=st.session_state.editable_json_text,
            height=450
        )

        try:
            edited_result = safe_json_loads(
                st.session_state.editable_json_text)
        except ValueError as exc:
            st.error(str(exc))
            st.stop()

        st.divider()
        st.subheader("4. Test Case Önizleme")

        df = test_cases_to_dataframe(edited_result)

        if df.empty:
            st.warning("Test case bulunamadı.")
        else:
            st.dataframe(df, use_container_width=True)

        st.divider()
        st.subheader("5. İndirilebilir Çıktılar")

        markdown_text = to_markdown(edited_result)
        csv_bytes = to_xray_csv_bytes(edited_result)
        json_bytes = to_json_bytes(edited_result)

        download_cols = st.columns(3)

        with download_cols[0]:
            st.download_button(
                label="📄 Analiz Dokümanı Markdown İndir",
                data=markdown_text.encode("utf-8-sig"),
                file_name="analiz_dokumani.md",
                mime="text/markdown",
                use_container_width=True
            )

        with download_cols[1]:
            st.download_button(
                label="🧪 Xray CSV İndir",
                data=csv_bytes,
                file_name="xray_import_test_cases.csv",
                mime="text/csv",
                use_container_width=True
            )

        with download_cols[2]:
            st.download_button(
                label="🧾 JSON İndir",
                data=json_bytes,
                file_name="figma_analysis_output.json",
                mime="application/json",
                use_container_width=True
            )

        with st.expander("Analiz Dokümanı Önizleme"):
            st.markdown(markdown_text)


if __name__ == "__main__":
    main()
