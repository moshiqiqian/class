from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core.parse_schedule import parse_schedule
from app.ui import footer


def _curriculum() -> dict:
    profile = st.session_state.profile
    if not profile:
        return {}
    for item in st.session_state.parsed_catalog:
        if item["college"] == profile["college"] and item["major"] == profile["major"]:
            return item
    return {}


def render() -> None:
    st.header("步骤 C · 本学期课表")
    profile = st.session_state.profile
    if not profile:
        st.warning("学生信息缺失，请先完成步骤 B。")
        return
    semester = profile.get("current_semester", 1)
    st.caption(
        f"当前第 {semester} 学期。上传**本学期课表**，系统据此判断你正在修读的课程，"
        "避免在后续选课规划中重复推荐；课表 + 之前成绩单 = 你目前的实际修读状态。"
    )

    upload = st.file_uploader("本学期课表 PDF", type=["pdf"], key="schedule_pdf")
    if upload is not None:
        try:
            items = parse_schedule(upload.getvalue(), upload.name)
        except Exception as error:  # noqa: BLE001 展示给用户
            st.error(f"课表解析失败：{error}")
            items = []
        if items:
            st.session_state.schedule_courses = [
                {"name": it["name"], "credits": float(it.get("credits", 0) or 0), "weeks": it.get("weeks", ""), "slots": it.get("slots", "")}
                for it in items
            ]
            st.success(f"已解析出 {len(items)} 门课程。")
        else:
            st.warning("未识别到课程，请确认是教务系统导出的课表 PDF。")

    schedule = st.session_state.get("schedule_courses", [])
    if schedule:
        st.subheader("课表课程")
        st.dataframe(
            pd.DataFrame([{"课程名称": c["name"], "学分": c["credits"], "周次": c.get("weeks", ""), "节次": c.get("slots", "")} for c in schedule]),
            use_container_width=True, hide_index=True,
        )

    # —— 网课补充：本学期培养方案开设、但不在课表中的通识课 ——
    curriculum = _curriculum()
    scheduled_names = {c["name"] for c in schedule}
    seen: set[str] = set()
    candidates: list[dict] = []
    for course in curriculum.get("courses", []):
        if (
            course.get("platform") in ("通识必修", "通识选修")
            and int(course.get("semester", 0)) == int(semester)
            and course["name"] not in scheduled_names
            and course["name"] not in seen
        ):
            seen.add(course["name"])
            candidates.append(course)

    if candidates:
        st.subheader("网课补充")
        st.caption("以下为本学期培养方案开设、但**不在课表中**的通识课程（可能是网课）。若你确实在修读，请勾选。")
        labels = [f"{c['name']}（{c.get('platform', '')}，{c['credits']} 学分）" for c in candidates]
        previous = set(st.session_state.get("schedule_online", []))
        default_labels = [label for label, c in zip(labels, candidates) if c["name"] in previous]
        chosen = st.multiselect("本学期在修读的通识网课", labels, default=default_labels, key="online_pick")
        st.session_state.schedule_online = [candidates[labels.index(label)]["name"] for label in chosen]

    total = sum(c["credits"] for c in schedule)
    note = f"课表课程共 **{len(schedule)}** 门、**{total:.1f}** 学分"
    if st.session_state.get("schedule_online"):
        note += f"；另补录网课 {len(st.session_state.schedule_online)} 门"
    st.caption(note + "。")

    def _confirm() -> None:
        st.session_state.schedule_confirmed = True

    footer(2, "保存并进入成绩单 →", 4, on_next=_confirm)

    if st.button("跳过（本学期无课表 / 暂不提供）", use_container_width=True):
        st.session_state.schedule_courses = []
        st.session_state.schedule_online = []
        st.session_state.schedule_confirmed = True
        st.session_state.stage = 4
        st.rerun()
