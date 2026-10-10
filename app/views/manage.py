from __future__ import annotations

import streamlit as st

from app.core.extract_courses import load_catalog_by_cache, load_or_parse
from app.core.workspace import (
    bind_pdf,
    delete_workspace_by_name,
    find_cache_file,
    list_workspaces,
    load_into_session,
)


def render() -> None:
    st.header("🗂️ 工作区管理")
    st.caption("在这里可以切换工作区、重新上传/解析培养方案、删除工作区。")

    workspaces = list_workspaces()
    if not workspaces:
        st.info("暂无工作区。请返回向导新建。")
        if st.button("← 返回"):
            st.session_state.manage_page = False
            st.rerun()
        return

    current = st.session_state.get("current_workspace", "")

    for ws in workspaces:
        name = ws["name"]
        is_current = name == current
        with st.container(border=True):
            mark = "🟢 当前" if is_current else "⚪"
            profile = ws.get("profile") or {}
            st.markdown(f"**{mark} {name}**")
            st.caption(f"培养方案：{ws.get('pdf_name') or '未绑定'} · 专业：{profile.get('major', '未填写')} · 创建：{ws.get('created_at', '')}")

            col1, col2, col3 = st.columns(3)
            with col1:
                if is_current:
                    st.button("当前工作区", key=f"cur_{name}", use_container_width=True, disabled=True)
                else:
                    if st.button("切换到此工作区", key=f"sw_{name}", use_container_width=True):
                        if load_into_session(name):
                            st.session_state.manage_page = False
                            st.rerun()
            with col2:
                if st.button("重新上传方案", key=f"re_{name}", use_container_width=True):
                    st.session_state[f"reupload_{name}"] = not st.session_state.get(f"reupload_{name}", False)
            with col3:
                if st.button("删除", key=f"del_{name}", use_container_width=True, disabled=is_current):
                    delete_workspace_by_name(name)
                    st.rerun()

            # 重新上传/解析该工作区的方案
            if st.session_state.get(f"reupload_{name}"):
                upload = st.file_uploader("上传新的培养方案 PDF", type=["pdf"], key=f"file_{name}")
                if upload is not None:
                    cache = find_cache_file(upload.getvalue())
                    label = "复用并更新" if cache else "解析并更新"
                    if st.button(label, key=f"parse_{name}", type="primary"):
                        _reparse(name, upload, cache)

    st.divider()
    if st.button("← 返回", use_container_width=True):
        st.session_state.manage_page = False
        st.rerun()


def _reparse(workspace_name: str, upload, cache: str) -> None:
    """重新解析 PDF 并更新到指定工作区。"""
    pdf_bytes = upload.getvalue()
    if not cache:
        with st.status("正在解析培养方案…可能需要 1~2 分钟", expanded=True) as status:
            try:
                load_or_parse(pdf_bytes, upload.name, force=True)
            except (ValueError, OSError) as error:
                status.update(label="解析失败", state="error")
                st.error(str(error))
                return
            status.update(label="解析完成", state="complete")
        import hashlib
        cache = f"catalog-{hashlib.sha256(pdf_bytes).hexdigest()[:16]}.json"

    bind_pdf(workspace_name, upload.name, cache)
    # 更新当前会话（若是当前工作区）
    if st.session_state.get("current_workspace") == workspace_name:
        try:
            st.session_state.parsed_catalog = load_catalog_by_cache(cache)
            st.session_state.parsed = True
            st.session_state.curriculum_pdf_name = upload.name
        except FileNotFoundError:
            pass
    st.session_state[f"reupload_{workspace_name}"] = False
    st.success(f"工作区「{workspace_name}」的方案已更新。")
    st.rerun()
