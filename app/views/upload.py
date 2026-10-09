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

    col_sel, col_new, col_del = st.columns([3, 1, 1])
    with col_sel:
        if names:
            idx = names.index(current) if current in names else 0
            selected = st.selectbox("工作区", names, index=idx, key="ws_select")
        else:
            selected = None
            st.info("暂无工作区，请先点击「新建工作区」。")
    with col_new:
        st.write("")
        if st.button("🆕 新建工作区", use_container_width=True):
            st.session_state.new_ws_dialog = True
    with col_del:
        st.write("")
        if current and current in names:
            if st.button("🗑️ 删除", use_container_width=True):
                delete_workspace_by_name(current)
                _clear_current()
                st.rerun()

    # 新建工作区弹窗（只输入名字，创建空工作区）
    if st.session_state.get("new_ws_dialog"):
        _render_create_dialog()

    # 切换工作区
    if selected and selected != current:
        _activate_by_name(selected)

    # ============ 第二步：向当前工作区上传方案 ============
    if current:
        st.divider()
        st.subheader("② 上传培养方案")
        ws = get_workspace(current)
        if ws and ws.get("cache_file"):
            st.success(f"「{current}」已绑定方案：{ws.get('pdf_name')}")
            st.caption("如需更换方案，请在下方重新上传。")
        else:
            st.warning(f"「{current}」尚未绑定培养方案，请上传 PDF。")
        _render_upload(ws)

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


def _render_create_dialog() -> None:
    with st.container(border=True):
        st.markdown("**新建工作区**")
        with st.form("create_workspace_form"):
            name = st.text_input("工作区名称", placeholder="例如：计算机2023")
            col_ok, col_cancel = st.columns(2)
            with col_ok:
                submitted = st.form_submit_button("创建", type="primary", use_container_width=True)
            with col_cancel:
                cancelled = st.form_submit_button("取消", use_container_width=True)
        if cancelled:
            st.session_state.new_ws_dialog = False
            st.rerun()
        if submitted:
            if not name.strip():
                st.warning("请输入工作区名称。")
                return
            if name_exists(name):
                st.warning(f"已存在「{name}」，将自动追加序号区分。")
            created = create_workspace(name.strip())
            st.session_state.new_ws_dialog = False
            st.session_state.current_workspace = created["name"]
            _clear_current()
            st.success(f"工作区「{created['name']}」已创建，请上传培养方案。")
            st.rerun()


def _activate_by_name(name: str) -> None:
    """切换到已有工作区。加载 catalog + 绑定的学生画像。"""
    ws = get_workspace(name)
    if not ws:
        return
    st.session_state.current_workspace = name
    st.session_state.profile = ws.get("profile")
    st.session_state.credits_confirmed = bool(ws.get("profile"))
    st.session_state.transcript_records = []
    st.session_state.messages = []
    if ws.get("cache_file"):
        try:
            catalog = load_catalog_by_cache(ws["cache_file"])
            st.session_state.parsed_catalog = catalog
            st.session_state.parsed = True
            st.session_state.curriculum_pdf_name = ws.get("pdf_name", "")
            st.session_state.curriculum_pdf_bytes = None
        except FileNotFoundError:
            st.session_state.parsed = False
            st.session_state.parsed_catalog = []
    else:
        st.session_state.parsed = False
        st.session_state.parsed_catalog = []
        st.session_state.curriculum_pdf_bytes = None
        st.session_state.curriculum_pdf_name = None
    st.session_state.index_ready = False
    reset_after(1)
    st.rerun()


def _render_upload(ws: dict | None) -> None:
    upload = st.file_uploader("培养方案 PDF", type=["pdf"], key="curriculum_pdf")
    if upload is None:
        return

    existing_cache = find_cache_file(upload.getvalue())
    if existing_cache:
        st.info("检测到该文档之前已解析过，可直接复用。")
        if st.button("复用并绑定到当前工作区", type="primary", use_container_width=True):
            _parse_and_bind(upload, reuse=existing_cache)
    else:
        if st.button("解析并绑定到当前工作区", type="primary", use_container_width=True):
            _parse_and_bind(upload)


def _parse_and_bind(upload, reuse: str = "") -> None:
    """解析 PDF（或复用缓存）并绑定到当前工作区。"""
    pdf_bytes = upload.getvalue()
    current = st.session_state.current_workspace

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
    bind_pdf(current, upload.name, cache_file)
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

    # 建立 RAG 索引
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
    else:
        _warmup_models()

    st.success(f"方案已绑定到工作区「{current}」。")
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
