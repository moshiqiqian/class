from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core.calc import calculate_gpa, summarize_transcripts
from app.core.workspace import save_profile, save_transcripts


def render() -> None:
    st.header("📈 升学管理")
    st.caption("完成本学期后，在这里同步成绩、计算绩点，并升入下一学期。")

    profile = st.session_state.get("profile")
    if not profile:
        st.warning("学生信息缺失，请先完成信息采集。")
        _back_button()
        return

    current = int(profile.get("current_semester", 1))
    records = st.session_state.get("transcript_records", [])
    this_sem = [r for r in records if r.get("semester") == current]

    st.markdown(f"### 当前：第 {current} 学期")

    # 本学期成绩
    st.markdown("**本学期已录入成绩**")
    if this_sem:
        gpa = calculate_gpa(this_sem)
        col1, col2 = st.columns(2)
        with col1:
            st.metric("本学期绩点", gpa["gpa"])
        with col2:
            st.metric("本学期学分", gpa["total_credits"])
        st.dataframe(pd.DataFrame([{"课程名称": r["course"], "成绩": r["score"], "学分": r["credits"], "绩点": r.get("gpa")} for r in this_sem]), use_container_width=True)
    else:
        st.info(f"暂无第 {current} 学期成绩。请先在「⚙️ 修改信息」页上传包含本学期的成绩单。")

    # 累计信息
    st.markdown("**累计学业情况**")
    gpa_all = calculate_gpa(records)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("总绩点", gpa_all["gpa"])
    with col2:
        st.metric("已获学分", gpa_all["total_credits"])
    with col3:
        st.metric("已修课程", len([r for r in records if r.get("passed")]))

    st.divider()

    # 升学操作
    if current >= 8:
        st.success("你已到第 8 学期（毕业学期），无需再升学。")
    else:
        st.markdown(f"### 确认升学：第 {current} 学期 → 第 {current + 1} 学期")
        st.caption("点击下方按钮将：① 更新当前学期为下一学期；② 重算已获学分/绩点；③ 保存到当前工作区。")
        if st.button(f"✓ 确认完成第 {current} 学期，进入第 {current + 1} 学期", type="primary", use_container_width=True):
            _promote(profile, records, current)

    _back_button()


def _promote(profile: dict, records: list[dict], current: int) -> None:
    """执行升学：更新学期 + 重算学分/绩点 + 保存。"""
    # 重算各平台已获学分与已通过课程
    completed_credits, completed_courses, failed = summarize_transcripts(records, {}, st.session_state.get("transcript_statuses", {}))
    profile["completed_credits"] = completed_credits
    profile["completed_courses"] = completed_courses
    profile["failed_courses"] = failed
    profile["current_semester"] = current + 1
    st.session_state.profile = profile
    if st.session_state.get("current_workspace"):
        save_profile(st.session_state.current_workspace, profile)
        save_transcripts(st.session_state.current_workspace, records)
    st.session_state.flash = f"✅ 已升入第 {current + 1} 学期。"
    st.session_state.promote_page = False
    st.rerun()


def _back_button() -> None:
    st.divider()
    if st.button("← 返回", use_container_width=True):
        st.session_state.promote_page = False
        st.rerun()
