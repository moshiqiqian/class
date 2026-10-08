from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core.calc import PLATFORMS, missing_semesters, summarize_transcripts
from app.core.matching import build_lookup, match_course, normalize_name
from app.core.transcript import parse_upload, semester_from_academic_label, semester_from_filename
from app.ui import footer
from app.state import reset_after


def _curriculum() -> dict:
    profile = st.session_state.profile
    return next(item for item in st.session_state.parsed_catalog if item["college"] == profile["college"] and item["major"] == profile["major"])


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


def _render_upload(profile: dict, curriculum: dict) -> tuple[list[dict], dict[str, float], list[str], list[dict], list[int]]:
    uploads = st.file_uploader(
        "上传多个成绩单文件（每学期一个，可乱序上传）",
        type=["pdf", "xlsx", "xls", "csv", "txt"],
        accept_multiple_files=True,
        key="transcript_uploads",
    )
    if not uploads:
        st.info("请上传至少一份成绩单。")
        return [], {}, [], [], []

    enrollment_year = profile.get("enrollment_year")
    term_options = list(range(1, max(2, profile["current_semester"] + 1)))
    records: list[dict] = []
    for index, upload in enumerate(uploads):
        # 学期识别优先级：文件名「学年+学期」> 文件名数字 > 手动选择
        term = semester_from_academic_label(upload.name, enrollment_year) or semester_from_filename(upload.name)
        if term is None:
            term = st.selectbox(f"{upload.name} 对应的学期", term_options, key=f"term_{index}")
        try:
            records.extend(parse_upload(upload, int(term), enrollment_year))
        except ValueError as error:
            st.error(f"{upload.name}：{error}")

    if not records:
        return [], {}, [], [], []

    # 明细表（中文表头 + 课程类型）
    lookup = build_lookup(curriculum["courses"])
    st.subheader("解析明细（请核对）")
    detail = pd.DataFrame([{
        "课程名称": r["course"],
        "课程类型": _match_platform(r["course"], curriculum, lookup),
        "成绩": r["score"],
        "学分": r["credits"],
        "学期": r["semester"],
        "是否及格": "是" if r["passed"] else "否",
    } for r in records])
    st.dataframe(detail, use_container_width=True)

    # 缺失学期监测
    received = {r["semester"] for r in records}
    missing = missing_semesters(profile["current_semester"], received)
    if missing:
        st.warning(f"检测到缺少第 {'、'.join(map(str, missing))} 学期的成绩单，请补充后再确认，以免规划结果偏差。")
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

    course_platforms = {normalize_name(c["name"]): c["platform"] for c in curriculum["courses"]}
    completed_credits, completed_courses, failed = summarize_transcripts(
        records, course_platforms, st.session_state.transcript_statuses,
        match=lambda name: normalize_name(match_course(name, lookup) or name),
    )

    unmatched = [r for r in records if match_course(r["course"], lookup) is None]
    if unmatched:
        st.warning("以下课程未能匹配到培养方案课程表，学分未归类，请核对课程名或改用手动填写：\n\n" + "、".join(sorted({r['course'] for r in unmatched})))

    return records, completed_credits, completed_courses, failed, missing


def _match_platform(name: str, curriculum: dict, lookup: dict) -> str:
    matched = match_course(name, lookup)
    if matched is None:
        return "未匹配"
    for c in curriculum["courses"]:
        if c["name"] == matched:
            return c.get("category", c.get("platform", ""))
    return "未匹配"


def render() -> None:
    st.header("步骤 C · 过往学分录入")
    profile, curriculum = st.session_state.profile, _curriculum()
    mode = st.radio("录入方式", ("手动填写", "上传成绩单分析"), horizontal=True, key="credit_mode")

    records, completed_credits, completed_courses, failed, missing = [], {}, [], [], []
    if mode == "手动填写":
        completed_credits = _render_manual(profile)
    else:
        records, completed_credits, completed_courses, failed, missing = _render_upload(profile, curriculum)

    if mode == "手动填写" or records:
        st.subheader("已获学分汇总")
        st.dataframe(pd.DataFrame([{"平台": k, "已获学分": v} for k, v in completed_credits.items()]), use_container_width=True)

        def _confirm() -> None:
            profile.update({
                "completed_credits": completed_credits,
                "completed_courses": completed_courses,
                "failed_courses": failed,
                "missing_semesters": missing,
            })
            st.session_state.profile = profile
            st.session_state.transcript_records = records
            st.session_state.credits_confirmed = True
            st.session_state.messages = []  # 清空旧对话
            reset_after(3)

        footer(2, "进入智能对话 →", 4, on_next=_confirm)
    else:
        footer(2, "进入智能对话 →", 4, disabled=True)
