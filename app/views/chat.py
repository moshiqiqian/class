from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core.calc import build_plan, calculate_gpa
from app.core.graph import build_graph
from app.core.rag import index_documents


def _curriculum() -> dict:
    profile = st.session_state.profile
    return next(item for item in st.session_state.parsed_catalog if item["college"] == profile["college"] and item["major"] == profile["major"])


def _ensure_index() -> None:
    """首次进入对话页时自动建立 RAG 索引。"""
    if st.session_state.get("index_ready"):
        return
    try:
        count = index_documents(st.session_state.curriculum_pdf_bytes, st.session_state.curriculum_pdf_name)
        st.session_state.index_ready = True
        if count:
            st.toast(f"已自动建立 RAG 索引（{count} 个文档块）。")
    except (OSError, RuntimeError, ValueError):
        st.session_state.index_ready = True


def render() -> None:
    profile = st.session_state.profile
    curriculum = _curriculum()
    _ensure_index()

    # 顶部信息栏
    st.markdown(f"### 🎓 {profile['college']} · {profile['major']}")
    st.caption(f"当前第 {profile['current_semester']} 学期")

    # 左侧竖排菜单 + 右侧主体
    menu_col, body_col = st.columns([1, 4])
    with menu_col:
        st.markdown("**功能菜单**")
        page = st.radio("", ("对话", "成绩信息", "修改信息"), label_visibility="collapsed", key="chat_page")
    with body_col:
        if page == "对话":
            _render_chat(profile, curriculum)
        elif page == "成绩信息":
            _render_grades()
        else:
            _render_edit()


def _render_chat(profile: dict, curriculum: dict) -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "assistant", "content": f"你好！我是 {profile['major']} 培养方案助手。\n\n你可以问我：\n- **学分缺口**：「我还差多少学分」\n- **选课规划**：「怎么规划才能大四前修完」「帮我平均分配课程」\n- **培养方案问答**：「本专业毕业最低学分是多少」"}
        ]

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant" and message.get("plan"):
                _render_plan(message["plan"], message.get("intent", "plan"))
            else:
                st.markdown(message["content"])

    if question := st.chat_input("请输入你的问题…"):
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        graph = build_graph()
        with st.spinner("思考中…"):
            result = graph.invoke({"question": question, "profile": profile, "curriculum": curriculum})

        if result.get("plan_result"):
            plan = result["plan_result"]
            intent = result.get("intent", "plan")
            content = _plan_summary(plan, intent)
            st.session_state.messages.append({"role": "assistant", "content": content, "plan": plan, "intent": intent})
            with st.chat_message("assistant"):
                _render_plan(plan, intent)
        else:
            answer = result.get("answer", "")
            st.session_state.messages.append({"role": "assistant", "content": answer})
            with st.chat_message("assistant"):
                st.markdown(answer)
                for citation in result.get("citations", []):
                    st.caption(f"来源：第 {citation['page']} 页")


def _plan_summary(plan: dict, intent: str) -> str:
    total_required = plan.get("total_required")
    total_earned = plan.get("total_earned")
    if total_required:
        return f"已获 {total_earned} / 需修 {total_required} 学分。"
    return ""


def _render_plan(plan: dict, intent: str) -> None:
    for warning in plan["warnings"]:
        st.warning(warning)

    # 学分缺口（所有规划类意图都展示）
    st.markdown("#### 学分缺口统计")
    gap_rows = []
    for name, row in plan["gap"].items():
        gap_rows.append({"平台": name, "要求": row["required"], "已获": row["earned"], "缺口": row["missing"]})
    st.dataframe(pd.DataFrame(gap_rows), use_container_width=True)

    # gap 意图：只回答缺口，不给规划建议
    if intent == "gap":
        return

    # 下学期清单
    st.markdown("#### 下学期选课清单")
    if plan["next_courses"]:
        st.dataframe(pd.DataFrame(plan["next_courses"]), use_container_width=True)
    else:
        st.info("下学期暂无必修课安排。")

    # 完整规划
    st.markdown("#### 剩余学期规划")
    if plan["timeline"]:
        for term, courses in plan["timeline"].items():
            total = sum(c["学分"] for c in courses)
            st.markdown(f"**第 {term} 学期**（{len(courses)} 门课，共 {total} 学分）")
            st.dataframe(pd.DataFrame(courses), use_container_width=True)
    else:
        st.info("已无剩余课程。")

    if plan["retakes"]:
        st.markdown("#### 补考 / 重修安排")
        st.dataframe(pd.DataFrame(plan["retakes"]), use_container_width=True)


def _render_grades() -> None:
    st.markdown("#### 成绩信息")
    records = st.session_state.get("transcript_records", [])
    if not records:
        st.info("暂无成绩单数据。请先在「过往学分」步骤上传成绩单。")
        return
    gpa_info = calculate_gpa(records)
    col1, col2 = st.columns(2)
    with col1:
        st.metric("绩点（GPA，4分制）", gpa_info["gpa"])
    with col2:
        st.metric("已获学分", gpa_info["total_credits"])

    st.markdown("#### 已修课程明细")
    detail = pd.DataFrame([{
        "课程名称": r["course"],
        "成绩": r["score"],
        "学分": r["credits"],
        "绩点": r["grade_point"],
    } for r in gpa_info["detail"]])
    st.dataframe(detail, use_container_width=True)

    # 不及格课程
    failed = [r for r in records if not r["passed"]]
    if failed:
        st.markdown("#### 不及格课程")
        st.dataframe(pd.DataFrame([{"课程名称": r["course"], "成绩": r["score"], "学分": r["credits"]} for r in failed]), use_container_width=True)


def _render_edit() -> None:
    st.markdown("#### 修改信息")
    profile = st.session_state.profile
    st.markdown(f"- **学院**：{profile['college']}\n- **专业**：{profile['major']}\n- **当前学期**：第 {profile['current_semester']} 学期\n- **入学年份**：{profile.get('enrollment_year', '—')}")
    st.divider()
    st.markdown("如需修改，请回到对应步骤：")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("修改学生信息（步骤 B）", use_container_width=True):
            st.session_state.stage = 2
            st.rerun()
    with col2:
        if st.button("修改过往学分（步骤 C）", use_container_width=True):
            st.session_state.stage = 3
            st.rerun()
