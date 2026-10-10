from __future__ import annotations

import threading

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

    # 显示上一次操作的结果提示（rerun 后一次性展示，避免 status 卡住）
    if msg := st.session_state.pop("flash", None):
        st.success(msg)

    workspaces = list_workspaces()
    current = st.session_state.get("current_workspace", "")

    # ============ 第一步：选择或创建工作区 ============
    st.subheader("① 选择工作区")
    names = [w["name"] for w in workspaces]

    if not names:
        # 无工作区：直接给一个醒目的大按钮，避免列布局错位
        st.info("暂无工作区，请先新建一个工作区。")
        if st.button("🆕 新建工作区", type="primary", use_container_width=True):
            _create_dialog()
        selected = None
    else:
        col_sel, col_new, col_del = st.columns([3, 1, 1], vertical_alignment="bottom")
        with col_sel:
            idx = names.index(current) if current in names else 0
            selected = st.selectbox("工作区", names, index=idx, key="ws_select")
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
        st.info("检测到该文档之前已解析过，可**复用**已有结果，或**重新解析**（当解析逻辑更新后，建议重新解析）。")
        col_reuse, col_reparse = st.columns(2)
        with col_reuse:
            if st.button("复用并绑定", type="primary", use_container_width=True):
                _parse_and_bind(workspace_name, upload, reuse=existing_cache)
        with col_reparse:
            if st.button("重新解析并绑定", use_container_width=True):
                _parse_and_bind(workspace_name, upload, force=True)
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
        st.info("检测到该文档之前已解析过，可**复用**已有结果，或**重新解析**（当解析逻辑更新后，建议重新解析）。")
        col_reuse, col_reparse = st.columns(2)
        with col_reuse:
            if st.button("复用并绑定到当前工作区", type="primary", use_container_width=True):
                _parse_and_bind(workspace_name, upload, reuse=existing_cache)
        with col_reparse:
            if st.button("重新解析并绑定", use_container_width=True):
                _parse_and_bind(workspace_name, upload, force=True)
    else:
        if st.button("解析并绑定到当前工作区", type="primary", use_container_width=True):
            _parse_and_bind(workspace_name, upload)


def _parse_and_bind(workspace_name: str, upload, reuse: str = "", force: bool = False) -> None:
    """解析 PDF（或复用缓存）并绑定到指定工作区。force=True 时忽略旧缓存重新解析。"""
    pdf_bytes = upload.getvalue()

    if reuse and not force:
        cache_file = reuse
    else:
        progress_bar = st.progress(0.0, text="正在解析培养方案…（首次较慢，请勿刷新）")

        def _on_progress(done: int, total: int) -> None:
            progress_bar.progress(min(done / max(total, 1), 1.0), text=f"正在解析培养方案… {done}/{total} 页")

        try:
            load_or_parse(pdf_bytes, upload.name, force=force, progress=_on_progress)
        except (ValueError, OSError) as error:
            progress_bar.empty()
            st.error(f"解析失败：{error}")
            return
        progress_bar.empty()
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
    reset_after(1)

    # 建立 RAG 索引：放到后台线程，避免长时间阻塞导致界面无响应/白屏。
    # 索引就绪前问答会回退到完整原文，功能不受影响。
    index_msg = ""
    if not reuse or force:
        _start_background_index(pdf_bytes, upload.name)
        index_msg = "，检索索引正在后台建立（不影响使用）"

    st.session_state.flash = f"✅ 培养方案解析并绑定成功{index_msg}。"
    st.rerun()


_INDEX_LOCK = threading.Lock()


def _start_background_index(pdf_bytes: bytes, filename: str) -> None:
    """后台线程建立 RAG 索引；失败静默（问答会回退到完整原文）。"""

    def _work() -> None:
        if not _INDEX_LOCK.acquire(blocking=False):
            return  # 已有索引任务在跑，避免并发写向量库
        try:
            from app.core.rag import build_embeddings, index_documents

            index_documents(pdf_bytes, filename, embeddings=build_embeddings())
        except Exception:
            pass
        finally:
            _INDEX_LOCK.release()

    threading.Thread(target=_work, name="rag-index", daemon=True).start()


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
