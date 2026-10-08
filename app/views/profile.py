from __future__ import annotations

import streamlit as st

from app.core.calc import enrollment_year_bounds, infer_semester, semester_message
from app.ui import footer
from app.state import reset_after


def render() -> None:
    st.header("步骤 B · 学生信息采集")
    catalog = st.session_state.parsed_catalog
    colleges = list(dict.fromkeys(item["college"] for item in catalog))

    college = st.selectbox("学院", colleges, key="profile_college")
    majors = [item["major"] for item in catalog if item["college"] == college]
    major = st.selectbox("专业", majors, key="profile_major")

    min_year, max_year = enrollment_year_bounds()
    col_year, col_month = st.columns(2)
    with col_year:
        year = st.number_input("入学年份", min_value=min_year, max_value=max_year, value=min(2023, max_year), step=1, key="profile_year")
    with col_month:
        month = st.selectbox("入学月份", list(range(1, 13)), index=8, key="profile_month")

    guessed = infer_semester(int(year), int(month))

    # 二次确认：先展示推算结果 + 确认，点「需要调整」才展开修改框
    st.info(f"根据入学时间，推算当前为 **第 {guessed} 学期**。")
    if warning := semester_message(guessed):
        st.warning(warning)

    if "semester_confirmed" not in st.session_state:
        st.session_state.semester_confirmed = False

    if not st.session_state.semester_confirmed:
        col_ok, col_adjust = st.columns(2)
        with col_ok:
            if st.button("✓ 确认无误", type="primary", use_container_width=True):
                st.session_state.semester_confirmed = True
                st.session_state.profile_current = max(1, guessed)
                st.rerun()
        with col_adjust:
            if st.button("调整学期", use_container_width=True):
                st.session_state.semester_confirmed = True
                st.session_state.profile_current = max(1, guessed)
                st.session_state.show_adjust = True
                st.rerun()
        return  # 未确认前不显示保存

    if st.session_state.get("show_adjust"):
        current = st.number_input("调整后的当前学期", min_value=1, max_value=12, value=st.session_state.get("profile_current", max(1, guessed)), step=1, key="profile_current")
        st.caption("允许按休学、提前修读等真实情况调整。")
    else:
        current = st.session_state.get("profile_current", max(1, guessed))
        st.success(f"已确认为第 {current} 学期。如需调整请返回「学生信息」步骤重新确认。")

    def _save() -> None:
        st.session_state.profile = {
            "college": college,
            "major": major,
            "enrollment_year": int(year),
            "enrollment_month": int(month),
            "current_semester": int(current),
            "completed_credits": {},
            "completed_courses": [],
            "failed_courses": [],
            "missing_semesters": [],
        }
        # 清空确认状态，下次进入重新确认
        st.session_state.semester_confirmed = False
        st.session_state.show_adjust = False
        reset_after(2)

    footer(1, "下一步：过往学分 →", 3, on_next=_save)
