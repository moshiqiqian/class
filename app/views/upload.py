from __future__ import annotations

import streamlit as st

from app.core.extract_courses import load_catalog_by_cache, load_or_parse
from app.core.workspace import (
    bind_pdf,
    create_workspace,
    delete_workspace_by_name,
    find_cache_file,
    get_workspace,
    list_workspaces,
    name_exists,
)
from app.state import reset_after
from app.ui import footer


def render() -> None:
    st.header("培养方案工作区")

    workspaces = list_workspaces()
    current = st.session_state.get("current_workspace", "")

    # ============ 第一步：选择或创建工作区 ============
    st.subheader("① 选择工作区")
    names = [w["name"] for w in workspaces]

    col_sel, col_new, col_del = st.columns([3, 1, 1], vertical_alignment="bottom")
    with col_sel:
        if names:
            idx = names.index(current) if current in names else 0
            selected = st.selectbox("工作区", names, index=idx, key="ws_select")
        else:
            selected = None
            st.info("暂无工作区，请先点击「新建工作区」。")
    with col_new:
        if st.button("🆕 新建工作区", use_container_width=True):
            _create_dialog()
    with col_del:
        if current and current in names:
            if st.button("🗑️ 删除", use_container_width=True):
                delete_workspace_by_name(current)
                _clear_current()
                st.rerun()

    # 切换工作区
    if selected and selected != current:
        _activate_by_name(selected)

    # ============ 第二步：培养方案 ============
    if current:
        st.divider()
        st.subheader("② 培养方案")
        ws = get_workspace(current)
        if ws and ws.get("cache_file"):
            st.success(f"已绑定方案：**{ws.get('pdf_name')}**")
            if st.button("更换培养方案"):
                _change_dialog(current)
        else:
            st.warning(f"「{current}」尚未绑定培养方案，请上传 PDF。")
            _render_upload(current, ws)

    # 进入下一步
    if st.session_state.get("parsed") and current:
        st.divider()
        footer(0, "下一步：学生信息 →", 2)


def _clear_current() -> None:
    st.session_state.parsed = False
    st.session_state.parsed_catalog = []
    st.session_state.curriculum_pdf_bytes = None
    st.session_state.curriculum_pdf_name = None
    st.session_state.current_workspace = ""
    st.session_state.profile = None
    st.session_state.credits_confirmed = False
    reset_after(1)


@st.dialog("新建工作区")
def _create_dialog() -> None:
    """独立的「新建工作区」弹窗：只有名称输入 + 创建/取消，创建后自动关闭。"""
    name = st.text_input("工作区名称", placeholder="例如：计算机2023")
    col_ok, col_cancel = st.columns(2)
    with col_ok:
        if st.button("创建", type="primary", use_container_width=True):
            if not name.strip():
                st.warning("请输入工作区名称。")
                return
            if name_exists(name.strip()):
                st.warning(f"已存在「{name.strip()}」，将自动追加序号区分。")
            created = create_workspace(name.strip())
            st.session_state.current_workspace = created["name"]
            _clear_current()
            st.session_state.current_workspace = created["name"]
            st.rerun()
    with col_cancel:
        if st.button("取消", use_container_width=True):
            st.rerun()


@st.dialog("更换培养方案")
def _change_dialog(workspace_name: str) -> None:
    """独立的「更换方案」弹窗，完成或取消后自动关闭。"""
    st.caption(f"为工作区「{workspace_name}」更换培养方案 PDF")
    upload = st.file_uploader("培养方案 PDF", type=["pdf"], key="change_pdf")
    if upload is None:
        if st.button("取消", use_container_width=True):
            st.rerun()
        return

    existing_cache = find_cache_file(upload.getvalue())
    if existing_cache:
        st.info("检测到该文档之前已解析过，可直接复用。")
        if st.button("复用并绑定", type="primary", use_container_width=True):
            _parse_and_bind(workspace_name, upload, reuse=existing_cache)
    else:
        if st.button("解析并绑定", type="primary", use_container_width=True):
            _parse_and_bind(workspace_name, upload)


def _activate_by_name(name: str) -> None:
    """切换到已有工作区：完整重载（方案+学生+成绩单），并 rerun 重新渲染。"""
    from app.core.workspace import load_into_session
    if load_into_session(name):
        st.rerun()


def _render_upload(workspace_name: str, ws: dict | None) -> None:
    upload = st.file_uploader("培养方案 PDF", type=["pdf"], key="curriculum_pdf")
    if upload is None:
        return

    existing_cache = find_cache_file(upload.getvalue())
    if existing_cache:
        st.info("检测到该文档之前已解析过，可直接复用。")
        if st.button("复用并绑定到当前工作区", type="primary", use_container_width=True):
            _parse_and_bind(workspace_name, upload, reuse=existing_cache)
    else:
        if st.button("解析并绑定到当前工作区", type="primary", use_container_width=True):
            _parse_and_bind(workspace_name, upload)


def _parse_and_bind(workspace_name: str, upload, reuse: str = "") -> None:
    """解析 PDF（或复用缓存）并绑定到指定工作区。完成后关闭弹窗。"""
    pdf_bytes = upload.getvalue()

    if reuse:
        cache_file = reuse
    else:
        with st.status("正在解析培养方案…文件较大，可能需要 1~2 分钟", expanded=True) as status:
            try:
                catalog, cached = load_or_parse(pdf_bytes, upload.name, force=False)
            except (ValueError, OSError) as error:
                status.update(label="解析失败", state="error")
                st.error(str(error))
                return
            status.update(label=f"解析完成：{len(catalog)} 个专业", state="complete")
        cache_file = _cache_name(pdf_bytes)

    # 绑定方案到工作区
    bind_pdf(workspace_name, upload.name, cache_file)
    try:
        catalog = load_catalog_by_cache(cache_file)
    except FileNotFoundError:
        st.error("缓存文件缺失，请重新解析。")
        return
    st.session_state.parsed_catalog = catalog
    st.session_state.parsed = True
    st.session_state.curriculum_pdf_name = upload.name
    st.session_state.curriculum_pdf_bytes = pdf_bytes
    st.session_state.index_ready = False
    reset_after(1)

    # 建立 RAG 索引（仅首次解析时；用于原文缺失时的兜底检索）
    if not reuse:
        with st.status("正在建立检索索引…首次需加载模型，可能需要 1~2 分钟", expanded=True) as status:
            try:
                from app.core.rag import index_documents
                count = index_documents(pdf_bytes, upload.name)
                st.session_state.index_ready = count > 0
                status.update(label=f"索引建立完成（{count} 个片段）" if count else "索引已存在", state="complete")
            except Exception as e:
                status.update(label="索引建立失败（不影响结构化问答）", state="error")
                st.warning(f"索引建立失败：{e}，结构化问答和选课规划仍可用。")

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
