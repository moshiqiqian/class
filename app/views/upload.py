from __future__ import annotations

import streamlit as st

from app.core.extract_courses import load_catalog_by_cache, load_or_parse
from app.core.workspace import (
    create_workspace,
    delete_workspace_by_name,
    find_cache_file,
    list_workspaces,
    name_exists,
)
from app.state import reset_after
from app.ui import footer

_NEW_LABEL = "＋ 新建培养方案"


def render() -> None:
    st.header("培养方案")

    workspaces = list_workspaces()
    current = st.session_state.get("current_workspace", "")

    # 下拉选择：已有工作区 + 新建选项
    names = [w["name"] for w in workspaces]
    options = names + [_NEW_LABEL]
    if current in names:
        default_idx = names.index(current)
    else:
        default_idx = 0

    col_select, col_delete = st.columns([4, 1])
    with col_select:
        selected = st.selectbox("选择培养方案工作区", options, index=default_idx, key="ws_select")
    with col_delete:
        st.write("")
        if current and current in names:
            if st.button("🗑️ 删除当前方案", use_container_width=True):
                delete_workspace_by_name(current)
                _clear_current()
                st.rerun()

    # 根据选择处理
    if selected == _NEW_LABEL:
        _render_new()
    elif selected != current:
        # 用户切换了工作区
        _activate_by_name(selected, workspaces)
    else:
        # 当前工作区已激活
        if st.session_state.get("parsed"):
            st.success(f"当前工作区：**{selected}**（已就绪）")

    # 进入下一步
    if st.session_state.get("parsed") and st.session_state.get("current_workspace"):
        st.divider()
        footer(0, "下一步：学生信息 →", 2)


def _clear_current() -> None:
    st.session_state.parsed = False
    st.session_state.parsed_catalog = []
    st.session_state.curriculum_pdf_bytes = None
    st.session_state.curriculum_pdf_name = None
    st.session_state.current_workspace = ""
    reset_after(1)


def _activate_by_name(name: str, workspaces: list[dict]) -> None:
    """切换到已有工作区。从缓存加载 catalog + 绑定的学生画像。"""
    ws = next((w for w in workspaces if w["name"] == name), None)
    if not ws:
        return
    try:
        catalog = load_catalog_by_cache(ws["cache_file"])
    except FileNotFoundError:
        st.error("该工作区的缓存文件缺失，请删除后重新上传。")
        return
    st.session_state.parsed_catalog = catalog
    st.session_state.parsed = True
    st.session_state.curriculum_pdf_name = ws.get("pdf_name", "")
    st.session_state.curriculum_pdf_bytes = None
    st.session_state.current_workspace = name
    st.session_state.index_ready = False
    # 加载工作区绑定的学生画像（工作区与学生绑定）
    st.session_state.profile = ws.get("profile")
    st.session_state.credits_confirmed = bool(ws.get("profile"))
    st.session_state.transcript_records = []
    st.session_state.messages = []
    _warmup_models()
    st.rerun()


def _render_new() -> None:
    st.subheader("上传培养方案")
    st.caption("支持 PDF 格式。首次解析需建立检索索引，大文件可能耗时 1~2 分钟，请耐心等待。")

    name = st.text_input("方案名称（可选）", placeholder="例如：2023 级培养方案", key="new_ws_name")
    upload = st.file_uploader("培养方案 PDF", type=["pdf"], key="new_ws_pdf")

    if upload is None:
        return

    if not name:
        name = upload.name.replace(".pdf", "")

    # 重名提示
    if name_exists(name):
        st.warning(f"已存在同名方案「{name}」，创建时会自动追加序号区分。")

    # 检测是否已解析过该文档
    existing_cache = find_cache_file(upload.getvalue())

    if existing_cache:
        st.info("检测到该文档之前已解析过，可直接复用，无需重新解析。")
        if st.button("复用已有结果", type="primary", use_container_width=True):
            _activate_new(name, upload, existing_cache, build_index=False)
    else:
        if st.button("解析并创建", type="primary", use_container_width=True):
            _parse_and_create(name, upload)


def _parse_and_create(name: str, upload) -> None:
    """解析 PDF + 建立索引（耗时操作集中在这里，对话阶段零等待）。"""
    pdf_bytes = upload.getvalue()

    with st.status("正在解析培养方案…文件较大，可能需要 1~2 分钟", expanded=True) as status:
        try:
            catalog, cached = load_or_parse(pdf_bytes, upload.name, force=False)
        except (ValueError, OSError) as error:
            status.update(label="解析失败", state="error")
            st.error(str(error))
            return
        status.update(label=f"解析完成：{len(catalog)} 个专业", state="complete")

    cache_file = _cache_name(pdf_bytes)
    _activate_new(name, upload, cache_file, build_index=True)


def _activate_new(name: str, upload, cache_file: str, build_index: bool) -> None:
    """创建/复用工作区，可选建立 RAG 索引。"""
    pdf_bytes = upload.getvalue()
    created = create_workspace(name, upload.name, cache_file)
    actual_name = created["name"]
    if actual_name != name:
        st.info(f"方案名称已存在，已自动命名为「{actual_name}」。")
    try:
        catalog = load_catalog_by_cache(cache_file)
    except FileNotFoundError:
        st.error("缓存文件缺失，请重新解析。")
        return
    st.session_state.parsed_catalog = catalog
    st.session_state.parsed = True
    st.session_state.curriculum_pdf_name = upload.name
    st.session_state.curriculum_pdf_bytes = pdf_bytes
    st.session_state.current_workspace = actual_name
    st.session_state.index_ready = False
    reset_after(1)

    if build_index:
        with st.status("正在建立检索索引…首次需加载模型，可能需要 1~2 分钟", expanded=True) as status:
            try:
                from app.core.rag import index_documents
                count = index_documents(pdf_bytes, upload.name)
                st.session_state.index_ready = count > 0
                status.update(label=f"索引建立完成（{count} 个片段）" if count else "索引已存在", state="complete")
            except Exception as e:
                status.update(label="索引建立失败（不影响结构化问答）", state="error")
                st.warning(f"索引建立失败：{e}，结构化问答和选课规划仍可用。")
    else:
        _warmup_models()

    st.success(f"工作区「{actual_name}」已就绪。")
    st.rerun()


def _cache_name(pdf_bytes: bytes) -> str:
    import hashlib
    digest = hashlib.sha256(pdf_bytes).hexdigest()[:16]
    return f"catalog-{digest}.json"


def _warmup_models() -> None:
    """预热 embedding 模型和 LLM，避免首次问答时卡顿。"""
    try:
        from app.core.rag import _embeddings, _llm
        _embeddings()
        _llm()
    except Exception:
        pass
