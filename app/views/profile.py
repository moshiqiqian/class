from __future__ import annotations

import streamlit as st

from app.core.calc import enrollment_year_bounds, infer_semester, semester_message
from app.ui import footer
from app.state import reset_after


def render() -> None:
    st.header("步骤 B · 学生信息采集")
    catalog = st.session_state.parsed_catalog
    colleges = list(dict.fromkeys(item["college"] for item in catalog))

    saved = st.session_state.profile  # 之前保存的画像（可能为 None）

    # 若之前已保存过，顶部回显，让用户知道当前值
    if saved:
        st.info(
            f"当前已保存：{saved['college']} · {saved['major']} · "
            f"入学 {saved['enrollment_year']} 年 {saved['enrollment_month']} 月 · "
            f"第 {saved['current_semester']} 学期"
        )

    # selectbox 默认值：优先用之前保存的，否则用 session key 记忆值
    def _default_index(options: list, saved_value, session_key: str) -> int:
        if saved_value in options:
            return options.index(saved_value)
        remembered = st.session_state.get(session_key)
        if remembered in options:
            return options.index(remembered)
        return 0

    college = st.selectbox(
        "学院", colleges,
        index=_default_index(colleges, saved["college"] if saved else None, "profile_college"),
        key="profile_college",
    )
    majors = [item["major"] for item in catalog if item["college"] == college]
    major = st.selectbox(
        "专业", majors,
        index=_default_index(majors, saved["major"] if saved else None, "profile_major"),
        key="profile_major",
    )

    min_year, max_year = enrollment_year_bounds()
    col_year, col_month = st.columns(2)
    with col_year:
        default_year = saved["enrollment_year"] if saved else min(2023, max_year)
        year = st.number_input("入学年份", min_value=min_year, max_value=max_year, value=default_year, step=1, key="profile_year")
    with col_month:
        default_month = saved["enrollment_month"] if saved else 9
        month = st.selectbox("入学月份", list(range(1, 13)), index=default_month - 1, key="profile_month")

    if st.button("确认信息，推算学期", type="primary", use_container_width=True):
        st.session_state.dialog_open = True

    if st.session_state.get("dialog_open"):
        _confirm_dialog(college, major, int(year), int(month))

    if st.session_state.get("freshman_confirm"):
        _freshman_dialog()


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
            _next_stage(max(1, guessed))
    else:
        adjusted = st.number_input("调整后的当前学期", min_value=1, max_value=12, value=max(1, guessed), step=1)
        st.caption("允许按休学、提前修读等真实情况调整。")
        if st.button("保存调整并继续", type="primary", use_container_width=True):
            _save_profile(college, major, year, month, int(adjusted))
            st.session_state.dialog_open = False
            _next_stage(int(adjusted))


def _next_stage(current_semester: int) -> None:
    """确定后续流程：第 1 学期（大一新生，无成绩单）→ 二次确认后直接进问答；否则进过往学分。"""
    if current_semester <= 1:
        st.session_state.freshman_confirm = True
    else:
        st.session_state.stage = 3
    st.rerun()


@st.dialog("大一新生确认")
def _freshman_dialog() -> None:
    st.markdown("你是**第 1 学期**（大一新生），**暂无期末成绩单**。")
    st.markdown("- 过往学分视为 **0**（没有任何已修课程）\n- 可直接进入**智能问答与选课规划**")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✓ 直接进入问答规划", type="primary", use_container_width=True):
            st.session_state.freshman_confirm = False
            st.session_state.credits_confirmed = True
            st.session_state.transcript_records = []
            st.session_state.stage = 4
            st.rerun()
    with col2:
        if st.button("改为录入学分", use_container_width=True):
            st.session_state.freshman_confirm = False
            st.session_state.stage = 3
            st.rerun()


def _save_profile(college: str, major: str, year: int, month: int, current_semester: int) -> None:
    # 保留已填写的学分数据（如果已有），避免返回时丢失
    existing = st.session_state.profile or {}
    profile = {
        "college": college,
        "major": major,
        "enrollment_year": year,
        "enrollment_month": month,
        "current_semester": current_semester,
        "completed_credits": existing.get("completed_credits", {}),
        "completed_courses": existing.get("completed_courses", []),
        "failed_courses": existing.get("failed_courses", []),
        "missing_semesters": existing.get("missing_semesters", []),
    }
    st.session_state.profile = profile
    # 同步保存到当前工作区（工作区与学生绑定）
    from app.core.workspace import save_profile
    if st.session_state.get("current_workspace"):
        save_profile(st.session_state.current_workspace, profile)
    reset_after(2)
