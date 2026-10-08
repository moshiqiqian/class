from __future__ import annotations

import streamlit as st

from app.core.extract_courses import load_or_parse
from app.state import reset_after
from app.ui import footer


def render() -> None:
    st.header("步骤 A · 培养方案上传与解析")
    st.write("上传培养方案后，点击“开始解析”。解析结果会缓存到本地，后续再次使用同一文件无需重复解析。")
    upload = st.file_uploader("培养方案 PDF", type=["pdf"], key="curriculum_pdf")
    col1, col2 = st.columns(2)
    with col1:
        parse = st.button("开始解析", type="primary", use_container_width=True, disabled=upload is None)
    with col2:
        force = st.button("重新解析", use_container_width=True, disabled=upload is None)

    if upload and (parse or force):
        with st.status("正在解析培养方案…", expanded=True) as status:
            st.write("正在读取文本和课程设置表…")
            try:
                catalog, cached = load_or_parse(upload.getvalue(), upload.name, force=force)
                st.write("正在整理学院、专业和课程…")
                st.session_state.parsed_catalog = catalog
                st.session_state.parsed = True
                st.session_state.curriculum_pdf_bytes = upload.getvalue()
                st.session_state.curriculum_pdf_name = upload.name
                reset_after(1)
                colleges = {item["college"] for item in catalog}
                courses = sum(len(item["courses"]) for item in catalog)
                status.update(label="解析完成", state="complete")
                st.success(f"解析成功：{len(colleges)} 个学院、{len(catalog)} 个专业、{courses} 门课程。{'已使用本地缓存。' if cached else '结果已写入本地缓存。'}")
            except (ValueError, OSError) as error:
                status.update(label="解析失败", state="error")
                st.error(str(error))

    if st.session_state.parsed:
        footer(0, "下一步：学生信息 →", 2)
