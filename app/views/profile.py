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

    if st.button("确认信息，推算学期", type="primary", use_container_width=True):
        st.session_state.dialog_open = True

    if st.session_state.get("dialog_open"):
        _confirm_dialog(college, major, int(year), int(month))


@st.dialog("确认当前学期")
def _confirm_dialog(college: str, major: str, year: int, month: int) -> None:
    guessed = infer_semester(year, month)
    st.markdown(f"根据入学时间，推算当前为 **第 {guessed} 学期**。")
    if warning := semester_message(guessed):
        st.warning(warning)

    choice = st.radio("是否确认？", ("确认无误", "需要调整"), horizontal=True)
    if choice == "确认无误":
        if st.button("保存并继续", type="primary", use_container_width=True):
            _save_profile(college, major, year, month, max(1, guessed))
            st.session_state.dialog_open = False
            st.session_state.stage = 3
            st.rerun()
    else:
        adjusted = st.number_input("调整后的当前学期", min_value=1, max_value=12, value=max(1, guessed), step=1)
        st.caption("允许按休学、提前修读等真实情况调整。")
        if st.button("保存调整并继续", type="primary", use_container_width=True):
            _save_profile(college, major, year, month, int(adjusted))
            st.session_state.dialog_open = False
            st.session_state.stage = 3
            st.rerun()


def _save_profile(college: str, major: str, year: int, month: int, current_semester: int) -> None:
    st.session_state.profile = {
        "college": college,
        "major": major,
        "enrollment_year": year,
        "enrollment_month": month,
        "current_semester": current_semester,
        "completed_credits": {},
        "completed_courses": [],
        "failed_courses": [],
        "missing_semesters": [],
    }
    reset_after(2)
