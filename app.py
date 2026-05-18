import base64
import io
import json
import os
from typing import Any, Optional, List, Dict

import streamlit as st
from dotenv import load_dotenv
from PIL import Image

from services.figma_client import FigmaClient, FigmaRateLimitError
from services.figma_parser import build_design_context, extract_candidate_frames
from services.ai_generator import (
    generate_analysis_and_tests,
    generate_analysis_and_tests_for_image_batches,
)
from services.exporters import (
    to_markdown,
    to_pdf_bytes,
    to_xray_csv_bytes,
    to_json_bytes,
    test_cases_to_dataframe,
)
from services.jira_client import JiraClient, JiraAuthConfig
from services.jira_parser import build_jira_context
from services.confluence_client import ConfluenceClient, ConfluenceAuthConfig
from services.link_resolver import resolve_urls
from services.context_merger import merge_source_contexts
from services.analysis_doc_parser import extract_text_from_upload, build_analysis_doc_context
from services.jira_audit import build_audit_jql, audit_issues_from_search_results
from services.project_catalog import PROJECT_GROUPS, PROJECT_LABELS

load_dotenv()

MAX_SCREENSHOTS = 60
IMAGE_BATCH_SIZE = 6
MAX_IMAGE_SIDE = 1600
JPEG_QUALITY = 92
MAX_CONTEXT_STR_LEN = 12000
MAX_ANALYSIS_DOCS = 10

st.set_page_config(
    page_title="Figma / Jira / Analysis Doc → Analiz + Xray",
    page_icon="🧪",
    layout="wide",
)


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
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
    st.session_state.setdefault("result_json", None)
    st.session_state.setdefault("editable_json_text", "")
    st.session_state.setdefault("figma_candidates", [])
    st.session_state.setdefault("figma_file_key", None)
    st.session_state.setdefault("audit_df", None)
    st.session_state.setdefault("audit_csv", None)
    st.session_state.setdefault("jira_boards", [])
    st.session_state.setdefault("jira_sprints", [])


def clear_audit_state() -> None:
    st.session_state.audit_df = None
    st.session_state.audit_csv = None


def reset_board_sprint_state() -> None:
    st.session_state.jira_boards = []
    st.session_state.jira_sprints = []


def show_header() -> None:
    st.title("🧪 Çok Kaynaklı Analiz ve Xray Test Case Generator")
    st.caption(
        "Figma, screenshot, Jira task ve analiz dokümanlarından analiz dokümanı taslağı "
        "ve Xray'e import edilebilir manuel test case CSV'si üretir."
    )


def show_sidebar() -> Dict[str, Any]:
    st.sidebar.header("⚙️ Ayarlar")

    secret_figma_token = get_secret("FIGMA_TOKEN")
    secret_openai_key = get_secret("OPENAI_API_KEY")
    default_model = get_secret("OPENAI_MODEL", "gpt-4o")

    st.sidebar.subheader("🔐 Kullanıcı Tokenları")
    st.sidebar.caption("Bu alana girilen tokenlar sadece mevcut oturumda kullanılır.")

    user_figma_token = st.sidebar.text_input(
        "Figma Personal Access Token",
        type="password",
        placeholder="figd_...",
        help="Boş bırakırsan Streamlit Secrets içindeki FIGMA_TOKEN kullanılır.",
    )

    user_openai_key = st.sidebar.text_input(
        "OpenAI API Key",
        type="password",
        placeholder="sk-proj-...",
        help="Boş bırakırsan Streamlit Secrets içindeki OPENAI_API_KEY kullanılır.",
    )

    figma_token = user_figma_token.strip() if user_figma_token else secret_figma_token
    openai_key = user_openai_key.strip() if user_openai_key else secret_openai_key

    st.sidebar.divider()

    model = st.sidebar.text_input(
        "OpenAI Model",
        value=default_model or "gpt-4o",
        help="Örn: gpt-4o, gpt-4.1-mini vb.",
    )

    if not model or model.strip() in {"gpt-0", "gpt-o", "gpt4o"}:
        model = "gpt-4o"

    st.sidebar.divider()
    st.sidebar.subheader("Jira Ayarları")

    jira_base_url = st.sidebar.text_input(
        "Jira Base URL",
        value=get_secret("JIRA_BASE_URL", ""),
        placeholder="https://your-domain.atlassian.net veya https://jira.company.com",
    )

    jira_deployment = st.sidebar.selectbox(
        "Jira Deployment",
        options=["dc", "cloud"],
        index=0 if get_secret("JIRA_DEPLOYMENT", "dc") == "dc" else 1,
    )

    jira_email = st.sidebar.text_input(
        "Jira Email (Cloud)",
        value=get_secret("JIRA_EMAIL", ""),
    )

    jira_username = st.sidebar.text_input(
        "Jira Username (DC/Server)",
        value=get_secret("JIRA_USERNAME", ""),
    )

    jira_api_token = st.sidebar.text_input(
        "Jira API Token",
        type="password",
        value="",
        placeholder="Cloud API token veya basic token",
    )

    jira_pat = st.sidebar.text_input(
        "Jira PAT",
        type="password",
        value="",
        placeholder="PAT varsa buraya",
    )

    jira_password = st.sidebar.text_input(
        "Jira Password",
        type="password",
        value="",
        placeholder="Gerekliyse basic auth password",
    )

    jira_verify_ssl = st.sidebar.checkbox(
        "Jira SSL doğrulansın",
        value=True,
    )

    st.sidebar.divider()
    st.sidebar.subheader("Confluence Ayarları")

    use_jira_settings_for_confluence = st.sidebar.checkbox(
        "Confluence için Jira ayarlarını kullan",
        value=True,
        help="Cloud ortamında çoğu zaman yeterlidir. Gerekirse kapatıp ayrı ayar gir.",
    )

    if use_jira_settings_for_confluence:
        confluence_base_url = jira_base_url
        confluence_deployment = jira_deployment
        confluence_email = jira_email
        confluence_username = jira_username
        confluence_api_token = jira_api_token
        confluence_pat = jira_pat
        confluence_password = jira_password
        confluence_verify_ssl = jira_verify_ssl
    else:
        confluence_base_url = st.sidebar.text_input(
            "Confluence Base URL",
            value=get_secret("CONFLUENCE_BASE_URL", ""),
            placeholder="https://your-domain.atlassian.net veya https://confluence.company.com",
        )

        confluence_deployment = st.sidebar.selectbox(
            "Confluence Deployment",
            options=["dc", "cloud"],
            index=0 if get_secret("CONFLUENCE_DEPLOYMENT", jira_deployment) == "dc" else 1,
        )

        confluence_email = st.sidebar.text_input(
            "Confluence Email (Cloud)",
            value=get_secret("CONFLUENCE_EMAIL", ""),
        )

        confluence_username = st.sidebar.text_input(
            "Confluence Username (DC/Server)",
            value=get_secret("CONFLUENCE_USERNAME", ""),
        )

        confluence_api_token = st.sidebar.text_input(
            "Confluence API Token",
            type="password",
            value="",
        )

        confluence_pat = st.sidebar.text_input(
            "Confluence PAT",
            type="password",
            value="",
        )

        confluence_password = st.sidebar.text_input(
            "Confluence Password",
            type="password",
            value="",
        )

        confluence_verify_ssl = st.sidebar.checkbox(
            "Confluence SSL doğrulansın",
            value=True,
        )

    st.sidebar.divider()
    st.sidebar.write("**Token Durumu**")
    st.sidebar.write("Figma Token:", "✅ Hazır" if figma_token else "❌ Yok")
    st.sidebar.write("OpenAI API Key:", "✅ Hazır" if openai_key else "❌ Yok")

    return {
        "figma_token": figma_token,
        "openai_key": openai_key,
        "model": model,
        "jira": {
            "base_url": jira_base_url.strip(),
            "deployment_type": jira_deployment,
            "email": jira_email.strip(),
            "username": jira_username.strip(),
            "api_token": jira_api_token.strip(),
            "pat": jira_pat.strip(),
            "password": jira_password.strip(),
            "verify_ssl": jira_verify_ssl,
        },
        "confluence": {
            "base_url": confluence_base_url.strip(),
            "deployment_type": confluence_deployment,
            "email": confluence_email.strip(),
            "username": confluence_username.strip(),
            "api_token": confluence_api_token.strip(),
            "pat": confluence_pat.strip(),
            "password": confluence_password.strip(),
            "verify_ssl": confluence_verify_ssl,
        },
    }


def uploaded_image_to_data_url(uploaded_file) -> str:
    image = Image.open(uploaded_file)

    if image.mode in ("RGBA", "P"):
        background = Image.new("RGB", image.size, (255, 255, 255))
        if image.mode == "RGBA":
            background.paste(image, mask=image.split()[-1])
        else:
            rgba_image = image.convert("RGBA")
            background.paste(rgba_image, mask=rgba_image.split()[-1])
        image = background
    else:
        image = image.convert("RGB")

    width, height = image.size
    max_side = max(width, height)

    if max_side > MAX_IMAGE_SIDE:
        scale = MAX_IMAGE_SIDE / max_side
        new_size = (int(width * scale), int(height * scale))
        image = image.resize(new_size, Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)

    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def uploaded_images_to_data_urls(uploaded_files: List[Any]) -> List[str]:
    if not uploaded_files:
        return []
    limited_files = uploaded_files[:MAX_SCREENSHOTS]
    return [uploaded_image_to_data_url(file) for file in limited_files]


def build_screenshot_context(uploaded_files: List[Any], user_notes: str, mode: str) -> dict:
    files = uploaded_files[:MAX_SCREENSHOTS] if uploaded_files else []
    screenshots = []

    for index, file in enumerate(files, start=1):
        screenshots.append(
            {
                "index": index,
                "filename": file.name,
                "content_type": file.type,
                "note": "Bu ekran görüntüsü kullanıcı tarafından manuel yüklendi.",
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
            "batch_size": IMAGE_BATCH_SIZE,
        },
        "screenshots": screenshots,
        "instructions": [
            "Görsellerde görünen UI elementlerini analiz et.",
            "Birden fazla ekran varsa ekranları aynı ürün akışının parçaları olarak değerlendir.",
            "Belirsiz noktaları open_questions altında belirt.",
        ],
    }


def build_jira_client(settings: Dict[str, Any]) -> JiraClient:
    cfg = settings["jira"]
    if not cfg["base_url"]:
        raise ValueError("Jira Base URL gerekli.")

    return JiraClient(
        JiraAuthConfig(
            base_url=cfg["base_url"],
            deployment_type=cfg["deployment_type"],
            email=cfg["email"] or None,
            username=cfg["username"] or None,
            api_token=cfg["api_token"] or None,
            pat=cfg["pat"] or None,
            password=cfg["password"] or None,
            verify_ssl=cfg["verify_ssl"],
        )
    )


def build_confluence_client_if_possible(settings: Dict[str, Any]) -> Optional[ConfluenceClient]:
    cfg = settings["confluence"]
    if not cfg["base_url"]:
        return None

    return ConfluenceClient(
        ConfluenceAuthConfig(
            base_url=cfg["base_url"],
            deployment_type=cfg["deployment_type"],
            email=cfg["email"] or None,
            username=cfg["username"] or None,
            api_token=cfg["api_token"] or None,
            pat=cfg["pat"] or None,
            password=cfg["password"] or None,
            verify_ssl=cfg["verify_ssl"],
        )
    )


def shrink_context_for_model(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: shrink_context_for_model(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shrink_context_for_model(v) for v in value]
    if isinstance(value, str):
        if len(value) > MAX_CONTEXT_STR_LEN:
            return value[:MAX_CONTEXT_STR_LEN] + "\n...[TRUNCATED]..."
        return value
    return value


def handle_figma_scan(figma_url: str, figma_token: str) -> None:
    if not figma_url:
        st.error("Lütfen Figma linki gir.")
        st.stop()

    if not figma_token:
        st.error("FIGMA_TOKEN bulunamadı.")
        st.stop()

    try:
        with st.spinner("Figma dosyasındaki ekranlar taranıyor..."):
            figma_client = FigmaClient(figma_token)
            outline_payload = figma_client.get_design_outline_payload(figma_url, depth=3)
            candidates = extract_candidate_frames(outline_payload)
            st.session_state["figma_file_key"] = outline_payload.get("file_key")
            st.session_state["figma_candidates"] = candidates

        if candidates:
            st.success(f"{len(candidates)} ekran/frame adayı bulundu.")
        else:
            st.warning("Frame adayı bulunamadı.")

    except FigmaRateLimitError as exc:
        st.error(str(exc))
        if exc.retry_after:
            st.warning(f"Önerilen bekleme süresi: {exc.retry_after} saniye")
        st.stop()

    except Exception as exc:
        st.error(f"Figma ekran tarama sırasında hata oluştu: {exc}")
        st.stop()


def show_candidate_selector() -> Optional[str]:
    selected_node_id = None

    if st.session_state.get("figma_candidates"):
        st.divider()
        st.subheader("Bulunan Figma Ekranları")

        candidate_labels = [item["label"] for item in st.session_state["figma_candidates"]]

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


def handle_load_jira_boards(settings: Dict[str, Any], project_keys: List[str]) -> None:
    if not project_keys:
        st.error("Önce en az bir proje seç.")
        st.stop()

    try:
        jira_client = build_jira_client(settings)
        with st.spinner("Board'lar Jira'dan çekiliyor..."):
            boards = jira_client.list_boards(project_keys)

        cleaned = []
        seen = set()

        for board in boards:
            board_id = board.get("id")
            board_name = board.get("name", "")
            board_type = board.get("type", "")
            if board_id is None:
                continue

            key = str(board_id)
            if key in seen:
                continue
            seen.add(key)

            cleaned.append(
                {
                    "id": board_id,
                    "name": board_name,
                    "type": board_type,
                    "label": f"{board_name} [{board_type}] ({board_id})",
                }
            )

        cleaned = sorted(cleaned, key=lambda x: x["label"].lower())
        st.session_state.jira_boards = cleaned
        st.session_state.jira_sprints = []
        st.success(f"{len(cleaned)} board bulundu.")

    except Exception as exc:
        st.error(f"Board'lar yüklenirken hata oluştu: {exc}")
        st.stop()


def handle_load_jira_sprints(settings: Dict[str, Any], board_id: int) -> None:
    try:
        jira_client = build_jira_client(settings)
        with st.spinner("Sprint'ler Jira'dan çekiliyor..."):
            sprints = jira_client.list_board_sprints(board_id)

        cleaned = []
        for sprint in sprints:
            sprint_id = sprint.get("id")
            name = sprint.get("name", "")
            state = sprint.get("state", "")
            if sprint_id is None:
                continue

            cleaned.append(
                {
                    "id": sprint_id,
                    "name": name,
                    "state": state,
                    "label": f"{name} [{state}] ({sprint_id})",
                }
            )

        cleaned = sorted(cleaned, key=lambda x: x["label"].lower())
        st.session_state.jira_sprints = cleaned
        st.success(f"{len(cleaned)} sprint bulundu.")

    except Exception as exc:
        st.error(f"Sprint'ler yüklenirken hata oluştu: {exc}")
        st.stop()


def handle_figma_or_screenshot_generation(
    mode: str,
    figma_url: str,
    figma_token: str,
    openai_key: str,
    model: str,
    selected_node_id: Optional[str],
    uploaded_screenshots: List[Any],
    user_notes: str,
) -> None:
    clear_audit_state()

    uses_figma = mode in ["Figma API Modu", "Hibrit Mod"]
    uses_screenshot = mode in ["Screenshot Modu", "Hibrit Mod"]

    if not openai_key:
        st.error("OPENAI_API_KEY bulunamadı.")
        st.stop()

    if uses_figma and not figma_url:
        st.error("Bu mod için Figma linki gerekli.")
        st.stop()

    if uses_figma and not figma_token:
        st.error("Bu mod için FIGMA_TOKEN gerekli.")
        st.stop()

    if uses_screenshot and not uploaded_screenshots:
        st.error("Bu mod için en az 1 ekran görüntüsü yüklemelisin.")
        st.stop()

    try:
        image_data_urls: List[str] = []
        design_context = None

        if uses_figma:
            with st.spinner("Figma node/layer verisi okunuyor..."):
                figma_client = FigmaClient(figma_token)
                payload = figma_client.get_design_payload(
                    figma_url,
                    include_image=False,
                    selected_node_id=selected_node_id,
                )
                design_context = build_design_context(payload)
                design_context["generation_mode"] = mode
                design_context["user_notes"] = user_notes or ""
                design_context["figma_image_api_used"] = False
                st.session_state.design_context = design_context

        if uses_screenshot:
            with st.spinner("Ekran görüntüleri hazırlanıyor..."):
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
                    "batch_size": IMAGE_BATCH_SIZE,
                    "filenames": [file.name for file in uploaded_screenshots[:MAX_SCREENSHOTS]],
                    "note": "Figma node/layer bilgisi ile manuel yüklenen ekran görüntüleri birlikte kullanılmıştır.",
                }

        if not design_context:
            st.error("Analiz için context oluşturulamadı.")
            st.stop()

        st.session_state.design_context = design_context

        if image_data_urls:
            st.info(
                f"{len(image_data_urls)} görselin tamamı analiz edilecek. "
                f"Görseller {IMAGE_BATCH_SIZE}'şarlı batch'ler halinde işlenecek."
            )

            with st.spinner("AI tüm ekran görüntülerini batch halinde analiz ediyor..."):
                result = generate_analysis_and_tests_for_image_batches(
                    openai_api_key=openai_key,
                    model=model,
                    design_context=shrink_context_for_model(design_context),
                    image_urls=image_data_urls,
                    batch_size=IMAGE_BATCH_SIZE,
                )
        else:
            with st.spinner("AI analiz ve test case üretiyor..."):
                result = generate_analysis_and_tests(
                    openai_api_key=openai_key,
                    model=model,
                    design_context=shrink_context_for_model(design_context),
                    image_urls=[],
                )

        st.session_state.result_json = result
        st.session_state.editable_json_text = json.dumps(result, ensure_ascii=False, indent=2)
        st.success("Analiz ve test case üretimi tamamlandı.")

    except FigmaRateLimitError as exc:
        st.error(str(exc))
        st.stop()

    except Exception as exc:
        st.error(f"İşlem sırasında hata oluştu: {exc}")
        st.stop()


def build_figma_contexts_from_resolved_links(resolved: Dict[str, Any]) -> List[Dict[str, Any]]:
    figma_contexts = []

    for item in resolved.get("figma_contexts", []):
        try:
            ctx = build_design_context(
                {
                    "file_key": item.get("file_key"),
                    "node_id": item.get("node_id"),
                    "node_tree": item.get("node_tree"),
                }
            )
            ctx["source"] = "figma_link_from_jira"
            ctx["source_url"] = item.get("source_url", "")
            figma_contexts.append(ctx)
        except Exception:
            continue

    return figma_contexts


def handle_jira_task_generation(issue_key: str, settings: Dict[str, Any], user_notes: str) -> None:
    clear_audit_state()

    if not issue_key:
        st.error("Lütfen Jira issue key gir.")
        st.stop()

    if not settings["openai_key"]:
        st.error("OPENAI_API_KEY bulunamadı.")
        st.stop()

    try:
        with st.spinner("Jira issue okunuyor..."):
            jira_client = build_jira_client(settings)
            issue_bundle = jira_client.get_issue_bundle(issue_key)
            jira_context = build_jira_context(issue_bundle)

        figma_client = None
        if settings["figma_token"]:
            try:
                figma_client = FigmaClient(settings["figma_token"])
            except Exception:
                figma_client = None

        confluence_client = None
        try:
            confluence_client = build_confluence_client_if_possible(settings)
        except Exception:
            confluence_client = None

        with st.spinner("Jira içindeki linkler çözümleniyor..."):
            resolved = resolve_urls(
                urls=jira_context.get("extracted_urls", []),
                figma_client=figma_client,
                confluence_client=confluence_client,
            )

        figma_contexts = build_figma_contexts_from_resolved_links(resolved)
        confluence_contexts = resolved.get("confluence_contexts", [])

        merged_context = merge_source_contexts(
            jira_context=jira_context,
            figma_contexts=figma_contexts,
            confluence_contexts=confluence_contexts,
            analysis_doc_contexts=[],
            user_notes=user_notes,
        )

        merged_context["resolved_links"] = {
            "figma_context_count": len(figma_contexts),
            "confluence_context_count": len(confluence_contexts),
            "jira_url_count": len(resolved.get("jira_urls", [])),
            "unresolved_sources": resolved.get("unresolved_sources", []),
        }

        st.session_state.design_context = merged_context

        with st.spinner("AI Jira task bağlamından analiz ve test case üretiyor..."):
            result = generate_analysis_and_tests(
                openai_api_key=settings["openai_key"],
                model=settings["model"],
                design_context=shrink_context_for_model(merged_context),
                image_urls=[],
            )

        st.session_state.result_json = result
        st.session_state.editable_json_text = json.dumps(result, ensure_ascii=False, indent=2)
        st.success("Jira task modunda üretim tamamlandı.")

    except Exception as exc:
        st.error(f"Jira Task Modu sırasında hata oluştu: {exc}")
        st.stop()


def handle_analysis_doc_generation(uploaded_docs: List[Any], pasted_text: str, settings: Dict[str, Any], user_notes: str) -> None:
    clear_audit_state()

    if not settings["openai_key"]:
        st.error("OPENAI_API_KEY bulunamadı.")
        st.stop()

    if not uploaded_docs and not pasted_text.strip():
        st.error("En az bir analiz dokümanı yüklemeli veya metin yapıştırmalısın.")
        st.stop()

    analysis_doc_contexts = []

    try:
        limited_docs = uploaded_docs[:MAX_ANALYSIS_DOCS] if uploaded_docs else []

        for uploaded in limited_docs:
            text = extract_text_from_upload(uploaded.name, uploaded.getvalue())
            ctx = build_analysis_doc_context(text, filename=uploaded.name)
            analysis_doc_contexts.append(ctx)

        if pasted_text.strip():
            ctx = build_analysis_doc_context(pasted_text, filename="Pasted Analysis Text")
            analysis_doc_contexts.append(ctx)

        merged_context = merge_source_contexts(
            jira_context=None,
            figma_contexts=[],
            confluence_contexts=[],
            analysis_doc_contexts=analysis_doc_contexts,
            user_notes=user_notes,
        )

        st.session_state.design_context = merged_context

        with st.spinner("AI analiz dokümanından test case üretiyor..."):
            result = generate_analysis_and_tests(
                openai_api_key=settings["openai_key"],
                model=settings["model"],
                design_context=shrink_context_for_model(merged_context),
                image_urls=[],
            )

        st.session_state.result_json = result
        st.session_state.editable_json_text = json.dumps(result, ensure_ascii=False, indent=2)
        st.success("Analiz Dokümanı Modu tamamlandı.")

    except Exception as exc:
        st.error(f"Analiz Dokümanı Modu sırasında hata oluştu: {exc}")
        st.stop()


def handle_jira_audit_generation(
    project_keys: List[str],
    filter_mode: str,
    start_date,
    end_date,
    issue_types: List[str],
    sprint_ids: List[int],
    settings: Dict[str, Any],
    max_issues: int,
    include_attachment_contents: bool,
    deep_link_analysis: bool,
) -> None:
    st.session_state.result_json = None
    st.session_state.editable_json_text = ""
    st.session_state.design_context = None

    if not project_keys:
        st.error("Lütfen en az bir proje seç.")
        st.stop()

    if filter_mode in {"Tarih Aralığı", "Tarih + Sprint"} and (start_date is None or end_date is None):
        st.error("Tarih filtreli kullanımda başlangıç ve bitiş tarihi gerekli.")
        st.stop()

    if filter_mode in {"Sprint", "Tarih + Sprint"} and not sprint_ids:
        st.error("Sprint filtreli kullanımda en az bir sprint seç.")
        st.stop()

    try:
        jira_client = build_jira_client(settings)
        all_issues = []
        per_project_limit = max(1, int(max_issues / max(1, len(project_keys))))

        for project_key in project_keys:
            jql = build_audit_jql(
                project_keys=[project_key.strip()],
                filter_mode=filter_mode,
                start_date=str(start_date) if start_date else None,
                end_date=str(end_date) if end_date else None,
                issue_types=issue_types,
                sprint_ids=sprint_ids,
            )

            with st.spinner(f"{project_key} için issue listesi Jira'dan çekiliyor..."):
                issues = jira_client.search_issues_paginated(
                    jql=jql,
                    fields=[
                        "summary",
                        "description",
                        "issuetype",
                        "priority",
                        "status",
                        "created",
                        "updated",
                    ],
                    limit=per_project_limit,
                )
                all_issues.extend(issues)

        if not all_issues:
            st.warning("Belirtilen filtrelerle issue bulunamadı.")
            st.session_state.audit_df = None
            st.session_state.audit_csv = None
            return

        unique_map = {}
        for issue in all_issues:
            key = issue.get("key")
            if key:
                unique_map[key] = issue

        issues_to_analyze = list(unique_map.values())[:max_issues]

        figma_client = None
        if deep_link_analysis and settings.get("figma_token"):
            try:
                figma_client = FigmaClient(settings["figma_token"])
            except Exception:
                figma_client = None

        confluence_client = None
        if deep_link_analysis:
            try:
                confluence_client = build_confluence_client_if_possible(settings)
            except Exception:
                confluence_client = None

        with st.spinner("Issue'lar analiz ediliyor..."):
            df = audit_issues_from_search_results(
                jira_client=jira_client,
                issues=issues_to_analyze,
                include_attachment_contents=include_attachment_contents,
                filter_mode=filter_mode,
                start_date=str(start_date) if start_date else None,
                end_date=str(end_date) if end_date else None,
                deep_link_analysis=deep_link_analysis,
                figma_client=figma_client,
                confluence_client=confluence_client,
            )

        st.session_state.audit_df = df
        st.session_state.audit_csv = df.to_csv(index=False, sep=";").encode("utf-8-sig")
        st.success(f"{len(df)} issue analiz edildi.")

    except Exception as exc:
        st.error(f"Jira Analysis Audit Modu sırasında hata oluştu: {exc}")
        st.stop()

def show_analysis_context() -> None:
    if not st.session_state.design_context:
        return

    st.divider()
    st.subheader("Analiz İçin Kullanılan Bağlam")

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
        small_summary = {k: v for k, v in summary.items() if isinstance(v, (str, int, float, bool))}
        if small_summary:
            cols = st.columns(min(len(small_summary), 4))
            for idx, (k, v) in enumerate(list(small_summary.items())[:4]):
                cols[idx].metric(k, v)

    with st.expander("Kullanılan Context JSON"):
        st.json(context)


def show_results_and_downloads() -> None:
    if not st.session_state.result_json:
        return

    st.divider()
    st.subheader("AI Çıktısı / Düzenleme Alanı")

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
    st.subheader("Test Case Önizleme")

    df = test_cases_to_dataframe(edited_result)

    if df.empty:
        st.warning("Test case bulunamadı.")
    else:
        st.dataframe(df, use_container_width=True)

    st.divider()
    st.subheader("İndirilebilir Çıktılar")

    try:
        markdown_text = to_markdown(edited_result)
        pdf_bytes = to_pdf_bytes(edited_result)
        csv_bytes = to_xray_csv_bytes(edited_result)
        json_bytes = to_json_bytes(edited_result)
    except Exception as exc:
        st.error(f"Çıktı dosyaları oluşturulurken hata oluştu: {exc}")
        st.stop()

    cols = st.columns(4)

    with cols[0]:
        st.download_button(
            label="📄 Analiz Markdown İndir",
            data=markdown_text.encode("utf-8-sig"),
            file_name="analiz_dokumani.md",
            mime="text/markdown",
            use_container_width=True,
        )

    with cols[1]:
        st.download_button(
            label="📕 Analiz PDF İndir",
            data=pdf_bytes,
            file_name="analiz_dokumani.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    with cols[2]:
        st.download_button(
            label="🧪 Xray CSV İndir",
            data=csv_bytes,
            file_name="xray_import_test_cases.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with cols[3]:
        st.download_button(
            label="🧾 JSON İndir",
            data=json_bytes,
            file_name="figma_analysis_output.json",
            mime="application/json",
            use_container_width=True,
        )

    with st.expander("Analiz Dokümanı Önizleme"):
        st.markdown(markdown_text)


def show_audit_results() -> None:
    if st.session_state.get("audit_df") is None:
        return

    df = st.session_state.audit_df

    st.divider()
    st.subheader("Jira Audit Sonuçları")

    column_config = {}
    if "Issue Link" in df.columns:
        column_config["Issue Link"] = st.column_config.LinkColumn(
            "Issue Link",
            help="Issue'u Jira'da aç",
            display_text="Aç",
        )

    st.data_editor(
        df,
        use_container_width=True,
        hide_index=True,
        disabled=True,
        column_config=column_config,
    )

    risky_df = df[df["Analiz Açısından Riskli"] == "Evet"] if "Analiz Açısından Riskli" in df.columns else df.iloc[0:0]

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Toplam Issue", len(df))
    col_b.metric("Riskli Issue", len(risky_df))
    col_c.metric(
        "Ortalama Readiness",
        round(float(df["Readiness Score"].mean()), 1) if len(df) > 0 else 0,
    )

    st.download_button(
        label="📥 Jira Audit CSV İndir",
        data=st.session_state.audit_csv,
        file_name="jira_analysis_audit.csv",
        mime="text/csv",
        use_container_width=True,
    )


def main() -> None:
    init_state()
    show_header()

    settings = show_sidebar()

    st.subheader("Çalışma Modu")

    mode = st.radio(
        "Nasıl analiz üretmek istiyorsun?",
        options=[
            "Figma API Modu",
            "Screenshot Modu",
            "Hibrit Mod",
            "Jira Task Modu",
            "Analiz Dokümanı Modu",
            "Jira Analysis Audit Modu",
        ],
        horizontal=False,
    )

    st.divider()

    selected_node_id = None
    figma_url = ""
    uploaded_screenshots: List[Any] = []
    issue_key = ""
    uploaded_docs: List[Any] = []
    pasted_analysis_text = ""

    selected_group = "bip"
    selected_projects: List[str] = []
    filter_mode = "Tarih Aralığı"
    audit_issue_types = ["Story", "Task", "Sub-task"]
    audit_start_date = None
    audit_end_date = None
    selected_board_id = None
    selected_sprint_ids: List[int] = []
    audit_max_issues = 100
    audit_include_attachment_contents = False
    audit_deep_link_analysis = True

    if mode in ["Figma API Modu", "Hibrit Mod"]:
        st.subheader("Figma Linki")

        figma_url = st.text_input(
            "Figma dosya veya ekran/frame linkini gir",
            placeholder="https://www.figma.com/design/....",
        )

        col_scan, col_info = st.columns([1, 4])

        with col_scan:
            scan_button = st.button("Figma ekranlarını tara", use_container_width=True)

        with col_info:
            st.caption(
                "Sadece dosya linki verirsen önce ekranları tara. "
                "Node-id içeren frame linki verirsen doğrudan üretim de yapabilirsin."
            )

        if scan_button:
            handle_figma_scan(figma_url, settings["figma_token"])

        selected_node_id = show_candidate_selector()

    if mode in ["Screenshot Modu", "Hibrit Mod"]:
        st.subheader("Ekran Görüntüleri")

        uploaded_screenshots = st.file_uploader(
            "Figma ekran görüntülerini yükle",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            help=(
                f"En fazla {MAX_SCREENSHOTS} görsel analiz edilecek. "
                f"Görseller {IMAGE_BATCH_SIZE}'şarlı batch'lerle işlenecek."
            ),
        )

        if uploaded_screenshots:
            preview_cols = st.columns(4)
            preview_limit = min(len(uploaded_screenshots), 12)

            for index, uploaded_file in enumerate(uploaded_screenshots[:preview_limit]):
                with preview_cols[index % 4]:
                    st.image(
                        uploaded_file,
                        caption=f"{index + 1}. {uploaded_file.name}",
                        use_container_width=True,
                    )

    if mode == "Jira Task Modu":
        st.subheader("Jira Task")

        issue_key = st.text_input(
            "Jira Issue Key",
            placeholder="Örn: APP-1234",
        )

        st.caption(
            "Task içindeki description, comments, custom field'lar, remote link'ler ve "
            "varsa Figma / Confluence linkleri birlikte değerlendirilecektir."
        )

    if mode == "Analiz Dokümanı Modu":
        st.subheader("Analiz Dokümanı")

        uploaded_docs = st.file_uploader(
            "Analiz dokümanlarını yükle",
            type=["txt", "md", "docx", "pdf", "html", "htm"],
            accept_multiple_files=True,
            help=f"En fazla {MAX_ANALYSIS_DOCS} doküman işlenecek.",
        )

        pasted_analysis_text = st.text_area(
            "Veya analiz metnini buraya yapıştır",
            height=220,
            placeholder="Analiz dokümanı metnini buraya yapıştırabilirsin...",
        )

    if mode == "Jira Analysis Audit Modu":
        st.subheader("Jira Analysis Audit")

        selected_group = st.selectbox(
            "Ürün / Grup",
            options=list(PROJECT_GROUPS.keys()),
            format_func=lambda x: PROJECT_LABELS.get(x, x),
        )

        selected_projects = st.multiselect(
            "Projeler",
            options=PROJECT_GROUPS[selected_group],
            default=PROJECT_GROUPS[selected_group],
            on_change=reset_board_sprint_state,
        )

        filter_mode = st.radio(
            "Filtre Tipi",
            options=["Tarih Aralığı", "Sprint", "Tarih + Sprint"],
            horizontal=True,
        )

        if filter_mode in {"Tarih Aralığı", "Tarih + Sprint"}:
            col1, col2 = st.columns(2)
            with col1:
                audit_start_date = st.date_input("Başlangıç Tarihi")
            with col2:
                audit_end_date = st.date_input("Bitiş Tarihi")

        audit_issue_types = st.multiselect(
            "Issue Types",
            options=["Story", "Task", "Sub-task", "Bug", "Epic"],
            default=["Story", "Task", "Sub-task"],
        )

        if filter_mode in {"Sprint", "Tarih + Sprint"}:
            st.markdown("**Sprint Filtresi**")

            col_b1, col_b2 = st.columns([1, 2])
            with col_b1:
                if st.button("Board'ları Yükle", use_container_width=True):
                    handle_load_jira_boards(settings, selected_projects)

            with col_b2:
                if st.session_state.get("jira_boards"):
                    selected_board_label = st.selectbox(
                        "Board Seç",
                        options=[b["label"] for b in st.session_state.jira_boards],
                    )
                    selected_board = next(
                        b for b in st.session_state.jira_boards if b["label"] == selected_board_label
                    )
                    selected_board_id = int(selected_board["id"])

            if selected_board_id is not None:
                col_s1, col_s2 = st.columns([1, 2])
                with col_s1:
                    if st.button("Sprint'leri Yükle", use_container_width=True):
                        handle_load_jira_sprints(settings, selected_board_id)

                with col_s2:
                    if st.session_state.get("jira_sprints"):
                        selected_sprint_labels = st.multiselect(
                            "Sprint Seç",
                            options=[s["label"] for s in st.session_state.jira_sprints],
                        )
                        selected_sprints = [
                            s for s in st.session_state.jira_sprints if s["label"] in selected_sprint_labels
                        ]
                        selected_sprint_ids = [int(s["id"]) for s in selected_sprints]

        col3, col4 = st.columns(2)
        with col3:
            audit_max_issues = st.number_input(
                "Maksimum issue sayısı",
                min_value=10,
                max_value=1000,
                value=100,
                step=10,
            )
        with col4:
            audit_include_attachment_contents = st.checkbox(
                "Attachment içeriklerini de oku",
                value=False,
                help="Daha yavaş olabilir ama attachment içindeki bağlamı da kalite değerlendirmesine katabilir.",
            )

        audit_deep_link_analysis = st.checkbox(
            "Task içindeki Confluence / Figma linklerini derin analiz et",
            value=True,
            help="Açıksa issue içindeki linklerin içeriğine de erişmeye çalışır. Erişim yoksa veya yetki yetersizse bunu sonuç tablosunda belirtir.",
        )

        st.caption(
            "Bu mod, seçilen projelerde Story/Task/Sub-task issue'larını tarar. "
            "Tarih filtresinde issue'nun create tarihi değil, Ready To Test/Test'e geçiş tarihi esas alınır. "
            "Sprint filtresi de opsiyonel olarak kullanılabilir. Link derin analizi açıksa Confluence/Figma içeriklerine de erişmeyi dener."
        )

    user_notes = st.text_area(
        "Ek bilgi / notlar",
        placeholder=(
            "Örn: Test case'ler Xray Manual Test formatına uygun olmalı. "
            "Negatif senaryolar da üretilsin. "
            "Comments kesin requirement sayılmasın."
        ),
        height=120,
    )

    st.divider()

    generate_button = st.button(
        "Analiz ve Test Case Üret",
        type="primary",
        use_container_width=True,
    )

    if generate_button:
        if mode in ["Figma API Modu", "Screenshot Modu", "Hibrit Mod"]:
            handle_figma_or_screenshot_generation(
                mode=mode,
                figma_url=figma_url,
                figma_token=settings["figma_token"],
                openai_key=settings["openai_key"],
                model=settings["model"],
                selected_node_id=selected_node_id,
                uploaded_screenshots=uploaded_screenshots,
                user_notes=user_notes,
            )

        elif mode == "Jira Task Modu":
            handle_jira_task_generation(
                issue_key=issue_key.strip(),
                settings=settings,
                user_notes=user_notes,
            )

        elif mode == "Analiz Dokümanı Modu":
            handle_analysis_doc_generation(
                uploaded_docs=uploaded_docs,
                pasted_text=pasted_analysis_text,
                settings=settings,
                user_notes=user_notes,
            )

        elif mode == "Jira Analysis Audit Modu":
            handle_jira_audit_generation(
                project_keys=selected_projects,
                filter_mode=filter_mode,
                start_date=audit_start_date,
                end_date=audit_end_date,
                issue_types=audit_issue_types,
                sprint_ids=selected_sprint_ids,
                settings=settings,
                max_issues=int(audit_max_issues),
                include_attachment_contents=audit_include_attachment_contents,
                deep_link_analysis=audit_deep_link_analysis,
            )

    show_analysis_context()
    show_results_and_downloads()
    show_audit_results()


if __name__ == "__main__":
    main()
