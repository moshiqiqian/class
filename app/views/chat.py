from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core.calc import build_plan, calculate_gpa
from app.core.graph import build_graph


def _curriculum() -> dict:
    profile = st.session_state.profile
    if not profile:
        return {}
    for item in st.session_state.parsed_catalog:
        if item["college"] == profile["college"] and item["major"] == profile["major"]:
            return item
    return {}


def render() -> None:
    profile = st.session_state.profile
    if not profile:
        st.warning("学生信息缺失，请回到信息采集步骤重新填写。")
        if st.button("返回信息采集"):
            st.session_state.stage = 2
            st.rerun()
        return
    curriculum = _curriculum()
    if not curriculum:
        st.warning("未找到对应专业的课程表，请确认工作区和专业选择。")
        if st.button("返回工作区"):
            st.session_state.stage = 1
            st.rerun()
        return

    # 顶部标题（简洁）
    st.markdown(f"#### 🎓 {profile['college']} · {profile['major']}")

    # 用 tabs 替代丑陋的竖排 radio
    tab_chat, tab_grades, tab_edit = st.tabs(["💬 对话", "📊 成绩信息", "⚙️ 修改信息"])
    with tab_chat:
        _render_chat(profile, curriculum)
    with tab_grades:
        _render_grades()
    with tab_edit:
        _render_edit()


def _render_chat(profile: dict, curriculum: dict) -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "assistant", "content": f"你好！我是 {profile['major']} 培养方案助手。\n\n你可以问我：\n- **学分缺口**：「我还差多少学分」\n- **选课规划**：「怎么规划才能大四前修完」「下学期选什么课」\n- **培养方案问答**：「本专业毕业最低学分是多少」「核心课程有哪些」"}
        ]

    messages = st.session_state.messages

    # 渲染历史消息（向上堆叠，最新的在底部）
    for message in messages:
        _render_message(message)

    # 输入框：st.chat_input 天然固定在底部
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
        elif result.get("review_result"):
            review = result["review_result"]
            st.session_state.messages.append({"role": "assistant", "content": "已修课程与绩点回顾", "review": review})
            with st.chat_message("assistant"):
                _render_review(review)
        else:
            answer = result.get("answer", "")
            st.session_state.messages.append({"role": "assistant", "content": answer})
            with st.chat_message("assistant"):
                st.markdown(answer)
                for citation in result.get("citations", []):
                    st.caption(f"来源：第 {citation['page']} 页")


def _render_message(message: dict) -> None:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant" and message.get("plan"):
            _render_plan(message["plan"], message.get("intent", "plan"))
        elif message["role"] == "assistant" and message.get("review"):
            _render_review(message["review"])
        else:
            st.markdown(message["content"])


def _plan_summary(plan: dict, intent: str) -> str:
    total_required = plan.get("total_required")
    total_earned = plan.get("total_earned")
    if total_required:
        return f"已获 {total_earned} / 需修 {total_required} 学分。"
    return ""


def _render_plan(plan: dict, intent: str) -> None:
    for warning in plan["warnings"]:
        st.warning(warning)

    # 学分缺口（规划类都展示）
    st.markdown("**学分缺口统计**")
    gap_rows = []
    for name, row in plan["gap"].items():
        gap_rows.append({"平台": name, "要求": row["required"], "已获": row["earned"], "缺口": row["missing"]})
    st.dataframe(pd.DataFrame(gap_rows), use_container_width=True)

    # gap 意图：只回答缺口
    if intent == "gap":
        return

    # 下学期清单
    st.markdown("**下学期选课清单**")
    if plan["next_courses"]:
        st.dataframe(pd.DataFrame(plan["next_courses"]), use_container_width=True)
    else:
        st.info("下学期暂无必修课安排。")

    # 剩余学期规划
    st.markdown("**剩余学期规划**")
    if plan["timeline"]:
        for term, courses in plan["timeline"].items():
            total = sum(c["学分"] for c in courses)
            st.markdown(f"*第 {term} 学期*（{len(courses)} 门课，共 {total} 学分）")
            st.dataframe(pd.DataFrame(courses), use_container_width=True)
    else:
        st.info("已无剩余课程。")

    # 已修课程回顾（之前学期）
    if plan.get("completed_courses"):
        st.markdown("**已修课程回顾**")
        st.dataframe(pd.DataFrame(plan["completed_courses"]), use_container_width=True)

    if plan["retakes"]:
        st.markdown("**补考 / 重修安排**")
        st.dataframe(pd.DataFrame(plan["retakes"]), use_container_width=True)


def _render_review(review: dict) -> None:
    """回顾：绩点 + 已修课程 + 学分进度。"""
    if review.get("gpa"):
        col1, col2 = st.columns(2)
        with col1:
            st.metric("绩点（GPA）", review["gpa"]["gpa"])
        with col2:
            st.metric("已获学分", review["gpa"]["total_credits"])

    if review.get("total_required"):
        st.progress(min(1.0, review["total_earned"] / review["total_required"]))
        st.caption(f"学分进度：{review['total_earned']} / {review['total_required']}")

    if review.get("completed_courses"):
        st.markdown("**已修课程**")
        st.dataframe(pd.DataFrame(review["completed_courses"]), use_container_width=True)
    else:
        st.info("暂无已修课程记录。请先在「过往学分」步骤上传成绩单。")


def _render_grades() -> None:
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

    st.markdown("**已修课程明细**")
    detail = pd.DataFrame([{
        "课程名称": r["course"],
        "成绩": r["score"],
        "学分": r["credits"],
        "绩点": r["grade_point"],
    } for r in gpa_info["detail"]])
    st.dataframe(detail, use_container_width=True)

    failed = [r for r in records if not r["passed"]]
    if failed:
        st.markdown("**不及格课程**")
        st.dataframe(pd.DataFrame([{"课程名称": r["course"], "成绩": r["score"], "学分": r["credits"]} for r in failed]), use_container_width=True)


def _render_edit() -> None:
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
