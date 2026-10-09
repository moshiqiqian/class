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
        "上传多个成绩单文件（可一次传多个学期，乱序也可以）",
        type=["pdf", "xlsx", "xls", "csv", "txt"],
        accept_multiple_files=True,
        key="transcript_uploads",
    )
    st.caption("提示：可前往教务网导出「全部成绩单」，一次上传即可。")
    if not uploads:
        st.info("请上传至少一份成绩单。")
        return [], {}, [], [], []

    enrollment_year = profile.get("enrollment_year")
    term_options = list(range(1, max(2, profile["current_semester"] + 1)))
    records: list[dict] = []
    for index, upload in enumerate(uploads):
        term = semester_from_academic_label(upload.name, enrollment_year) or semester_from_filename(upload.name)
        if term is None:
            term = st.selectbox(f"{upload.name} 对应的学期", term_options, key=f"term_{index}")
        try:
            records.extend(parse_upload(upload, int(term), enrollment_year))
        except ValueError as error:
            st.error(f"{upload.name}：{error}")

    if not records:
        return [], {}, [], [], []

    # 明细表（中文表头 + 课程类型 + 绩点）
    st.subheader("解析明细（请核对）")
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

    # 解析出成绩单后立即保存到工作区（避免未点确认就丢失）
    st.session_state.transcript_records = records
    from app.core.workspace import save_transcripts
    if st.session_state.get("current_workspace"):
        save_transcripts(st.session_state.current_workspace, records)

    return records, completed_credits, completed_courses, failed, missing


def render() -> None:
    st.header("步骤 C · 过往学分录入")
    profile = st.session_state.profile
    if not profile:
        st.warning("学生信息缺失，请先完成步骤 B。")
        return

    # 顶部回显：之前已保存的学分信息
    if st.session_state.get("credits_confirmed") and profile.get("completed_credits"):
        st.success("已保存过往学分信息。返回本页会保留，可继续修改后重新确认。")

    mode = st.radio("录入方式", ("手动填写", "上传成绩单分析"), horizontal=True, key="credit_mode")

    records, completed_credits, completed_courses, failed, missing = [], {}, [], [], []
    if mode == "手动填写":
        completed_credits = _render_manual(profile)
    else:
        records, completed_credits, completed_courses, failed, missing = _render_upload(profile)

    # 回显：已保存的成绩单记录（切换页面/工作区后仍可见）
    saved_records = st.session_state.get("transcript_records", [])
    if mode == "上传成绩单分析" and not records and saved_records:
        st.success(f"已保存 {len(saved_records)} 条成绩记录（无需重新上传）。")
        with st.expander("查看已保存成绩单", expanded=False):
            st.dataframe(pd.DataFrame([{
                "课程名称": r["course"], "课程类型": r.get("platform") or "未匹配",
                "成绩": r["score"], "学分": r["credits"], "学期": r.get("semester"), "绩点": r.get("gpa"),
            } for r in saved_records]), use_container_width=True)

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
            profile.update({
                "completed_credits": completed_credits,
                "completed_courses": completed_courses,
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
            reset_after(3)

        footer(2, "进入智能对话 →", 4, on_next=_confirm)
    else:
        footer(2, "进入智能对话 →", 4, disabled=True)
