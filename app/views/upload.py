from __future__ import annotations

import streamlit as st

from app.core.extract_courses import load_or_parse
from app.state import reset_after
from app.ui import footer


def render() -> None:
    st.header("步骤 A · 培养方案上传与解析")
    st.write("上传培养方案 PDF，点击「开始解析」。解析结果会按文件缓存；同一文件再次上传可直接复用，无需重复解析。")

    upload = st.file_uploader("培养方案 PDF", type=["pdf"], key="curriculum_pdf")

    # 解析进度状态
    if "parsing" not in st.session_state:
        st.session_state.parsing = False

    col1, col2 = st.columns(2)
    with col1:
        if st.session_state.parsing:
            # 解析中：显示「停止解析」
            if st.button("⏹ 停止解析", use_container_width=True):
                st.session_state.parsing = False
                st.rerun()
        else:
            if st.button("▶ 开始解析", type="primary", use_container_width=True, disabled=upload is None):
                st.session_state.parsing = True
                st.rerun()
    with col2:
        # 「新建工作区」= 清空当前解析结果，重新上传/解析
        if st.button("🆕 新建解析存储", use_container_width=True):
            st.session_state.parsed_catalog = []
            st.session_state.parsed = False
            st.session_state.parsing = False
            reset_after(1)
            st.rerun()

    # 执行解析
    if st.session_state.parsing and upload is not None:
        with st.status("正在解析培养方案…", expanded=True) as status:
            st.write("正在读取文本和课程设置表…")
            try:
                catalog, cached = load_or_parse(upload.getvalue(), upload.name, force=False)
                st.write("正在整理学院、专业和课程…")
                st.session_state.parsed_catalog = catalog
                st.session_state.parsed = True
                st.session_state.curriculum_pdf_bytes = upload.getvalue()
                st.session_state.curriculum_pdf_name = upload.name
                st.session_state.parsing = False
                reset_after(1)
                colleges = {item["college"] for item in catalog}
                courses = sum(len(item["courses"]) for item in catalog)
                status.update(label="解析完成", state="complete")
                st.success(f"解析成功：{len(colleges)} 个学院、{len(catalog)} 个专业、{courses} 门课程。{'已使用本地缓存。' if cached else '结果已写入本地缓存。'}")
                st.rerun()
            except (ValueError, OSError) as error:
                st.session_state.parsing = False
                status.update(label="解析失败", state="error")
                st.error(str(error))

    # 展示当前已解析的工作区信息
    if st.session_state.parsed:
        st.success(f"当前解析存储：{st.session_state.curriculum_pdf_name}（{len(st.session_state.parsed_catalog)} 个专业）")
        footer(0, "下一步：学生信息 →", 2)
