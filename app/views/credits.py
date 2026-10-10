from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core.calc import PLATFORMS, missing_semester_labels, summarize_transcripts
from app.core.transcript import parse_upload, semester_from_academic_label, semester_from_filename
from app.ui import footer
from app.state import reset_after


def _curriculum() -> dict:
    profile = st.session_state.profile
    if not profile:
        return {}
    for item in st.session_state.parsed_catalog:
        if item["college"] == profile["college"] and item["major"] == profile["major"]:
            return item
    return {}


def _render_manual(profile: dict) -> dict[str, float]:
    st.caption("请填写各平台已获得的学分（不含本学期）。")
    credits: dict[str, float] = {}
    columns = st.columns(3)
    for index, platform in enumerate(PLATFORMS):
        with columns[index % 3]:
            key = f"manual_{platform}"
            value = st.session_state.manual_credits.get(platform, float(profile.get("completed_credits", {}).get(platform, 0)))
            credits[platform] = st.number_input(platform, min_value=0.0, value=value, step=0.5, key=key)
    st.session_state.manual_credits = credits
    st.info("手动填写无法自动识别缺失成绩单学期；若成绩单不完整，请在规划结果中留意提示。")
    return credits


def _render_upload(profile: dict) -> tuple[list[dict], dict[str, float], list[str], list[dict], list[int]]:
    uploads = st.file_uploader(
        "上传成绩单文件（可一次传多个学期，乱序也可以）",
        type=["pdf", "xlsx", "xls", "csv", "txt"],
        accept_multiple_files=True,
        key="transcript_uploads",
    )
    st.caption("提示：可前往教务网导出「全部成绩单」，一次上传即可。")

    records: list[dict] = []
    if uploads:
        # 有新上传：解析
        enrollment_year = profile.get("enrollment_year")
        term_options = list(range(1, max(2, profile["current_semester"] + 1)))
        for index, upload in enumerate(uploads):
            term = semester_from_academic_label(upload.name, enrollment_year) or semester_from_filename(upload.name)
            if term is None:
                term = st.selectbox(f"{upload.name} 对应的学期", term_options, key=f"term_{index}")
            try:
                records.extend(parse_upload(upload, int(term), enrollment_year))
            except Exception as error:
                st.error(f"{upload.name} 解析失败：{error}")
        if records:
            st.session_state.transcript_records = records
            from app.core.workspace import save_transcripts
            if st.session_state.get("current_workspace"):
                save_transcripts(st.session_state.current_workspace, records)
    else:
        # 无新上传：直接使用之前保存的成绩单记录（同步）
        records = st.session_state.get("transcript_records", [])
        if records:
            st.info(f"已加载之前保存的 {len(records)} 条成绩记录（无需重新上传）。如需更新请重新上传。")

    if not records:
        st.info("请上传至少一份成绩单。")
        return [], {}, [], [], []

    # 明细表（中文表头 + 课程类型 + 绩点）
    with st.expander(f"成绩明细（{len(records)} 条，点击展开核对）", expanded=False):
        detail = pd.DataFrame([{
            "课程名称": r["course"],
            "课程类型": r.get("platform") or "未匹配",
            "成绩": r["score"],
            "学分": r["credits"],
            "学期": r["semester"],
            "绩点": r.get("gpa"),
            "是否及格": "是" if r["passed"] else "否",
        } for r in records])
        st.dataframe(detail, use_container_width=True)

    # 缺失学期监测
    received = {r["semester"] for r in records}
    missing = missing_semester_labels(profile["current_semester"], received)
    if missing:
        st.warning("检测到缺少以下学期的成绩单，请补充后再确认，以免规划结果偏差：\n\n" + "、".join(missing))
    else:
        st.success("成绩单学期完整。")

    # 不及格课程处理
    unpassed = [r for r in records if not r["passed"]]
    statuses: dict[str, str] = {}
    if unpassed:
        st.subheader("不及格课程处理")
        for index, record in enumerate(unpassed):
            statuses[record["course"]] = st.selectbox(
                f"{record['course']}（{record['credits']} 学分）",
                ("待补考", "需重修", "已通过补考"),
                key=f"status_{index}",
            )
        st.session_state.transcript_statuses = statuses

    completed_credits, completed_courses, failed = summarize_transcripts(records, {}, st.session_state.transcript_statuses)
    return records, completed_credits, completed_courses, failed, missing


def _schedule_names() -> list[str]:
    """本学期课表 + 补录网课的课程名，用于避免重复推荐。"""
    names = [c["name"] for c in st.session_state.get("schedule_courses", [])]
    names += st.session_state.get("schedule_online", [])
    return names


def render() -> None:
    st.header("步骤 D · 过往成绩单")
    profile = st.session_state.profile
    if not profile:
        st.warning("学生信息缺失，请先完成步骤 B。")
        return

    # 兜底同步：若 session 无成绩单但工作区有，则加载（避免看不到已存成绩）
    if not st.session_state.get("transcript_records") and st.session_state.get("current_workspace"):
        from app.core.workspace import get_workspace
        ws = get_workspace(st.session_state.current_workspace)
        if ws and ws.get("transcripts"):
            st.session_state.transcript_records = ws["transcripts"]

    # 顶部回显：之前已保存的学分信息
    if st.session_state.get("credits_confirmed") and profile.get("completed_credits"):
        st.success("已保存过往学分信息。返回本页会保留，可继续修改后重新确认。")

    mode = st.radio("录入方式", ("手动填写", "上传成绩单分析"), horizontal=True, key="credit_mode")

    records, completed_credits, completed_courses, failed, missing = [], {}, [], [], []
    if mode == "手动填写":
        completed_credits = _render_manual(profile)
    else:
        records, completed_credits, completed_courses, failed, missing = _render_upload(profile)

    # 回显：若本次没有计算（如切换模式或返回），用已保存的 profile 数据
    if not completed_credits and profile.get("completed_credits"):
        completed_credits = profile["completed_credits"]
    if not completed_courses and profile.get("completed_courses"):
        completed_courses = profile["completed_courses"]
    if not failed and profile.get("failed_courses"):
        failed = profile["failed_courses"]

    if mode == "手动填写" or records or completed_credits:
        st.subheader("已获学分汇总")
        summary = pd.DataFrame([{"平台": k, "已获学分": v} for k, v in completed_credits.items()])
        st.dataframe(summary, use_container_width=True)
        total = sum(v for v in completed_credits.values())
        st.caption(f"已获学分总计：**{total}** 学分")

        def _confirm() -> None:
            names = list(completed_courses)
            for name in _schedule_names():
                if name not in names:
                    names.append(name)
            profile.update({
                "completed_credits": completed_credits,
                "completed_courses": names,
                "failed_courses": failed,
                "missing_semesters": missing,
            })
            st.session_state.profile = profile
            st.session_state.transcript_records = records or st.session_state.transcript_records
            st.session_state.credits_confirmed = True
            st.session_state.messages = []
            # 同步保存到当前工作区（工作区与学生绑定，含成绩单）
            from app.core.workspace import save_profile, save_transcripts
            if st.session_state.get("current_workspace"):
                save_profile(st.session_state.current_workspace, profile)
                save_transcripts(st.session_state.current_workspace, st.session_state.transcript_records)
            reset_after(4)

        footer(3, "进入智能对话 →", 5, on_next=_confirm)
    else:
        footer(3, "进入智能对话 →", 5, disabled=True)

    # 无成绩单（如大一新生）：直接用课表作为修读状态进入问答
    st.divider()
    if st.button("没有成绩单，直接进入问答（如大一新生）", use_container_width=True):
        profile.update({
            "completed_credits": {},
            "completed_courses": _schedule_names(),
            "failed_courses": [],
            "missing_semesters": [],
        })
        st.session_state.profile = profile
        st.session_state.transcript_records = []
        st.session_state.credits_confirmed = True
        st.session_state.messages = []
        from app.core.workspace import save_profile
        if st.session_state.get("current_workspace"):
            save_profile(st.session_state.current_workspace, profile)
        reset_after(4)
        st.session_state.stage = 5
        st.rerun()
