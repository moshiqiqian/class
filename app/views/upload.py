from __future__ import annotations

import streamlit as st

from app.core.extract_courses import scan_toc
from app.core.workspace import (
    create_workspace,
    delete_workspace_by_name,
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

    st.caption("将先扫描目录（秒级）；正式解析在你选好学院/专业后进行。")
    if st.button("扫描目录并绑定", type="primary", use_container_width=True):
        _scan_and_bind(workspace_name, upload)
    if st.button("取消", use_container_width=True):
        st.rerun()


def _activate_by_name(name: str) -> None:
    """切换到已有工作区：完整重载（方案+学生+成绩单），并 rerun 重新渲染。"""
    from app.core.workspace import load_into_session
    if load_into_session(name):
        st.rerun()


def _render_upload(workspace_name: str, ws: dict | None) -> None:
    upload = st.file_uploader("培养方案 PDF", type=["pdf"], key="curriculum_pdf")
    if upload is None:
        return

    st.caption("将先扫描目录（秒级）；正式解析在你选好学院/专业后进行。")
    if st.button("扫描目录并绑定到当前工作区", type="primary", use_container_width=True):
        _scan_and_bind(workspace_name, upload)


def _scan_and_bind(workspace_name: str, upload) -> None:
    """第一阶段：只扫描目录，建立工作区绑定；正文解析放到第 2 步选好专业/学院后进行。"""
    pdf_bytes = upload.getvalue()
    with st.spinner("正在扫描培养方案目录…"):
        try:
            toc = scan_toc(pdf_bytes, upload.name)
        except Exception as error:  # noqa: BLE001 展示给用户
            st.error(f"目录扫描失败：{error}")
            return

    st.session_state.curriculum_pdf_bytes = pdf_bytes
    st.session_state.curriculum_pdf_name = upload.name
    st.session_state.toc = toc
    st.session_state.toc_scanned = True
    st.session_state.parsed = False
    st.session_state.parsed_catalog = []
    st.session_state.selected_unit = None
    st.session_state.pending_workspace = workspace_name
    reset_after(1)

    if toc.get("has_toc") and toc.get("units"):
        st.session_state.flash = f"✅ 已扫描目录：共 {len(toc['units'])} 个专业/大类。请进入「2. 学生信息」选择你的学院与专业，再解析正文。"
    else:
        st.session_state.flash = "✅ 未发现目录（可能是单一专业方案）。请进入「2. 学生信息」解析。"
    st.session_state.stage = 2
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
