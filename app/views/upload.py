from __future__ import annotations

import streamlit as st

from app.core.extract_courses import load_catalog_by_cache, load_or_parse
from app.core.workspace import (
    create_workspace,
    delete_workspace,
    find_cache_file,
    list_workspaces,
    rename_workspace,
)
from app.state import reset_after
from app.ui import footer


def render() -> None:
    st.header("步骤 A · 培养方案工作区")

    workspaces = list_workspaces()

    # 顶部：当前工作区 + 操作按钮
    current_name = st.session_state.get("current_workspace", "")
    if current_name:
        st.success(f"当前工作区：**{current_name}**")

    col_switch, col_new, col_manage = st.columns(3)
    with col_switch:
        if st.button("📂 切换 / 管理工作区", use_container_width=True):
            st.session_state.workspace_panel = True
    with col_new:
        if st.button("🆕 新建工作区", use_container_width=True):
            st.session_state.new_workspace = True
    with col_manage:
        if workspaces and current_name:
            if st.button("🗑️ 删除当前工作区", use_container_width=True):
                for i, w in enumerate(workspaces):
                    if w["name"] == current_name:
                        delete_workspace(i)
                        break
                _clear_current()
                st.rerun()

    # 新建工作区弹窗
    if st.session_state.get("new_workspace"):
        _render_new_workspace()

    # 管理工作区弹窗
    if st.session_state.get("workspace_panel"):
        _render_workspace_panel(workspaces)

    # 进入下一步
    if st.session_state.get("parsed") and st.session_state.get("current_workspace"):
        footer(0, "下一步：学生信息 →", 2)


def _clear_current() -> None:
    st.session_state.parsed = False
    st.session_state.parsed_catalog = []
    st.session_state.curriculum_pdf_bytes = None
    st.session_state.curriculum_pdf_name = None
    st.session_state.current_workspace = ""
    reset_after(1)


def _render_new_workspace() -> None:
    with st.container(border=True):
        st.subheader("新建工作区")
        name = st.text_input("工作区名称", placeholder="例如：2023 级培养方案", key="new_ws_name")
        upload = st.file_uploader("培养方案 PDF", type=["pdf"], key="new_ws_pdf")
        if upload and name:
            existing_cache = find_cache_file(upload.getvalue())
            if existing_cache:
                st.info("检测到该文档之前已解析过，可直接复用已有结果，无需重新解析。")
                col_reuse, col_reparse = st.columns(2)
                with col_reuse:
                    if st.button("复用已有结果", type="primary", use_container_width=True):
                        _finish_new_workspace(name, upload.name, existing_cache, upload.getvalue())
                with col_reparse:
                    if st.button("重新解析", use_container_width=True):
                        _parse_and_finish(name, upload)
            else:
                if st.button("解析并创建", type="primary", use_container_width=True):
                    _parse_and_finish(name, upload)
        if st.button("取消", use_container_width=True):
            st.session_state.new_workspace = False
            st.rerun()


def _parse_and_finish(name: str, upload) -> None:
    with st.status("正在解析培养方案…", expanded=True) as status:
        try:
            catalog, cached = load_or_parse(upload.getvalue(), upload.name, force=True)
            status.update(label="解析完成", state="complete")
            _finish_new_workspace(name, upload.name, _cache_name_from_catalog(catalog, upload), upload.getvalue())
        except (ValueError, OSError) as error:
            status.update(label="解析失败", state="error")
            st.error(str(error))


def _cache_name_from_catalog(catalog, upload) -> str:
    import hashlib
    from app.core.extract_courses import CACHE_DIR
    digest = hashlib.sha256(upload.getvalue()).hexdigest()[:16]
    return f"catalog-{digest}.json"


def _finish_new_workspace(name: str, pdf_name: str, cache_file: str, pdf_bytes) -> None:
    create_workspace(name, pdf_name, cache_file)
    _activate_workspace(name, pdf_name, cache_file, pdf_bytes)
    st.session_state.new_workspace = False
    st.rerun()


def _activate_workspace(name: str, pdf_name: str, cache_file: str, pdf_bytes) -> None:
    try:
        catalog = load_catalog_by_cache(cache_file)
    except FileNotFoundError:
        st.error("缓存文件缺失，请重新解析。")
        return
    st.session_state.parsed_catalog = catalog
    st.session_state.parsed = True
    st.session_state.curriculum_pdf_name = pdf_name
    st.session_state.curriculum_pdf_bytes = pdf_bytes
    st.session_state.current_workspace = name
    reset_after(1)


def _render_workspace_panel(workspaces: list[dict]) -> None:
    with st.container(border=True):
        st.subheader("工作区列表")
        if not workspaces:
            st.info("暂无工作区。请先「新建工作区」上传培养方案。")
        for i, w in enumerate(workspaces):
            col_name, col_use, col_del = st.columns([3, 1, 1])
            with col_name:
                st.write(f"**{w['name']}**  ·  {w.get('pdf_name', '')}  ·  {w.get('created_at', '')}")
            with col_use:
                if st.button("切换", key=f"use_{i}", use_container_width=True):
                    # 切换工作区：从缓存加载 catalog，但不重新解析 PDF
                    try:
                        catalog = load_catalog_by_cache(w["cache_file"])
                        st.session_state.parsed_catalog = catalog
                        st.session_state.parsed = True
                        st.session_state.curriculum_pdf_name = w.get("pdf_name", "")
                        st.session_state.curriculum_pdf_bytes = None  # 无 PDF bytes，问答时提示
                        st.session_state.current_workspace = w["name"]
                        st.session_state.workspace_panel = False
                        reset_after(1)
                        st.rerun()
                    except FileNotFoundError:
                        st.error("缓存文件缺失，请删除该工作区并重新解析。")
            with col_del:
                if st.button("删除", key=f"del_{i}", use_container_width=True):
                    delete_workspace(i)
                    st.rerun()
        if st.button("关闭", use_container_width=True):
            st.session_state.workspace_panel = False
            st.rerun()
