from __future__ import annotations

import json
import os
import re
from typing import Any, TypedDict

import streamlit as st
from langgraph.graph import END, StateGraph

from app.core.calc import build_plan, calculate_gpa
from app.core.prerequisite import advisory_notes
from app.core.rag import _llm, index_documents
from app.core.tools import ALL_TOOLS


class GraphState(TypedDict, total=False):
    question: str
    profile: dict
    curriculum: dict
    intent: str
    mode: str
    target_semester: int
    answer: str
    citations: list[dict]
    plan_result: dict
    review_result: dict


# 意图分类：LLM 输出结构化 JSON
_INTENT_PROMPT = (
    "判断学生问题属于哪一类，并判断规划模式。只输出 JSON，不要输出其他内容：\n"
    '{{"intent": "plan|gap|review|missing|semester_plan|qa", "mode": "early|balanced|career|original", "target_semester": 0}}\n\n'
    "分类规则：\n"
    "- plan：选课规划（问「下学期选什么课」「怎么规划课程」「提前修完」「平均分配」）\n"
    "- semester_plan：为「某一个具体学期」规划课程（问「帮我规划第3学期」「重新规划第1学期」「第5学期该修什么」），target_semester 填该学期号(1-8)\n"
    "- gap：学分缺口（问「还差多少学分」「学分够不够毕业」）\n"
    "- review：查询「我自己」的成绩/绩点/已修课程（问「我绩点多少」「我学过哪些课」）\n"
    "- missing：查询缺失哪些学期的成绩单（问「缺哪几个学期」）\n"
    "- qa：其他所有培养方案知识问答（培养目标、毕业要求、某课程学分/学期/考核、核心课程、学制、学位等）\n\n"
    "mode（当 intent=plan 时）：\n"
    "- career：要求「整个大学生涯/大一到大四/大学四年/全程/完整」的整体规划（从头到尾排满 8 个学期）\n"
    "- early：提到提前修完/大四前修完\n"
    "- balanced：提到平均分配/均衡安排\n"
    "- original：按培养方案原始安排\n\n"
    "重要：\n"
    "- 只要在问「整个大学/大一到大四/大学四年/全程」怎么规划 → intent=plan, mode=career\n"
    "- 提到具体学期号（如第1学期、大二上）并要求规划 → semester_plan\n"
    "- 拿不准时 intent=qa\n\n"
    "问题：{question}\n"
    "JSON："
)

# 关键词兜底（仅在 LLM 不可用/失败时使用，用较具体短语避免误判）
_MISSING_KEYWORDS = ("缺少哪", "缺失哪", "还缺哪", "缺哪几个", "缺了哪些学期", "缺少哪几个学期")
_PLAN_MODE_KEYWORDS = ("怎么规划", "怎么安排", "如何规划", "如何安排", "选课规划", "提前修完", "大四前修完", "平均分配")
_GAP_KEYWORDS = ("还差多少学分", "学分够不够", "学分还差", "够不够毕业", "学分缺口")
_REVIEW_KEYWORDS = ("我绩点", "我的绩点", "我的成绩", "我学过", "我修过", "已修哪些", "我的成绩单", "我之前的成绩")
_PLAN_KEYWORDS = ("下学期选什么课", "选什么课", "选课", "课程安排", "重修", "补考")


def _classify(state: GraphState) -> dict[str, str]:
    question = state.get("question", "")
    intent, mode, target = _classify_intent(question)
    return {"intent": intent, "mode": mode, "target_semester": target}


def _extract_semester(question: str) -> int:
    """从问题里提取学期号（「第3学期」「大三上」等），失败返回 0。"""
    m = re.search(r"第\s*([1-8])\s*学期", question)
    if m:
        return int(m.group(1))
    # 大一上=1 大一下=2 大二上=3 ... 大四上=7 大四下=8
    grade_map = {"一": 1, "二": 2, "三": 3, "四": 4}
    g = re.search(r"大([一二三四])([上下])", question)
    if g:
        base = (grade_map[g.group(1)] - 1) * 2
        return base + (1 if g.group(2) == "上" else 2)
    return 0


def _classify_intent(question: str) -> tuple[str, str, int]:
    """完全由 LLM 分类意图 + 规划模式 + 目标学期（不使用关键词）。"""
    raw = _llm().invoke(_INTENT_PROMPT.format(question=question)).content.strip()
    parsed = _parse_intent_json(raw) or {}
    intent = parsed.get("intent", "qa")
    if intent not in ("plan", "gap", "review", "missing", "semester_plan", "qa"):
        intent = "qa"
    mode = parsed.get("mode", "balanced")
    if mode not in ("early", "balanced", "career", "original"):
        mode = "balanced"
    target = int(parsed.get("target_semester", 0) or 0)
    # semester_plan 必须带合法学期号，否则降级为普通规划
    if intent == "semester_plan" and target not in range(1, 9):
        target = _extract_semester(question)
        if target == 0:
            intent = "plan"
    return intent, mode, target


def _parse_intent_json(raw: str) -> dict | None:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return None
    return None


def _plan_node(state: GraphState) -> dict[str, Any]:
    """选课规划：确定性计算（保证数字准确）+ LLM 生成实质建议。"""
    profile, curriculum = state["profile"], state["curriculum"]
    advice = advisory_notes(profile["major"], profile.get("completed_courses", []))
    mode = state.get("mode", "balanced")
    if mode == "career":
        from app.core.calc import build_career_plan
        plan = build_career_plan(profile, curriculum)
    else:
        plan = build_plan(profile, curriculum, advice, mode=mode)
    # gap 意图只答缺口，不生成规划建议
    if state.get("intent") != "gap":
        plan["suggestion"] = _generate_plan_suggestion(profile, plan)
    return {"plan_result": plan}


def _semester_plan_node(state: GraphState) -> dict[str, Any]:
    """为指定学期（含历史学期）重新规划课程。"""
    from app.core.calc import build_semester_plan
    profile, curriculum = state["profile"], state["curriculum"]
    target = int(state.get("target_semester", 0) or 0)
    if target not in range(1, 9):
        # 兜底：转成普通规划
        return _plan_node(state)
    plan = build_semester_plan(profile, curriculum, target)
    plan["suggestion"] = _generate_semester_suggestion(profile, plan)
    return {"plan_result": plan}


def _generate_semester_suggestion(profile: dict, plan: dict) -> str:
    """为指定学期规划生成建议。"""
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if not key:
        return ""
    try:
        target = plan["target_semester"]
        courses = "、".join(f"{c['课程名称']}({c['状态']})" for c in plan["courses"])
        prompt = (
            f"你是培养方案选课顾问。学生希望重新规划「第 {target} 学期」。\n"
            f"该学期培养方案课程：{courses or '无'}。\n"
            f"共 {plan['course_count']} 门课、{plan['total_credits']} 学分。\n"
            "请用 2~3 句话给出该学期的选课建议，指出哪些需优先补修、如何安排节奏。"
        )
        return _llm().invoke(prompt).content.strip()
    except Exception:
        return ""


def _generate_plan_suggestion(profile: dict, plan: dict) -> str:
    """基于确定性规划结果，生成自然语言的选课建议。"""
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if not key:
        return ""
    try:
        mode = plan.get("mode", "balanced")
        total_earned = plan.get("total_earned", 0)
        total_required = plan.get("total_required", 0)
        gap_text = "、".join(f"{k}差{v['missing']}学分" for k, v in plan.get("gap", {}).items() if v.get("missing", 0) > 0)

        if mode == "career":
            # 全生涯规划：说明约束，提示按实际调整
            from app.core.calc import MAX_CREDITS_PER_SEMESTER, MAX_ELECTIVE_CREDITS_PER_SEMESTER
            timeline_desc = "；".join(
                f"第{t}学期 {len(rows)}门/共{sum(c['学分'] for c in rows):.0f}学分"
                for t, rows in plan.get("timeline", {}).items()
            )
            prompt = (
                "你是培养方案选课顾问。以下是一个「全大学生涯（大一到大四）规划」的确定性排课结果。\n"
                f"已获 {total_earned} / 需修 {total_required} 学分；缺口：{gap_text or '已满足'}。\n"
                f"排课结果：{timeline_desc}。\n"
                f"约束：每学期总学分上限 {MAX_CREDITS_PER_SEMESTER:.0f}、选修学分上限 {MAX_ELECTIVE_CREDITS_PER_SEMESTER:.0f}。\n"
                "请用 3~4 句话：(1) 说明这是按上限约束生成的适应性方案；(2) 提醒学生按自己实际情况（能力、兴趣、开课时间）调整；"
                "(3) 如果学生能提供具体的每学期学分目标或偏好课程，可以据此优化。语气务实、简洁。"
            )
        else:
            next_courses = "、".join(c["课程名称"] for c in plan.get("next_courses", []))
            prompt = (
                f"你是培养方案选课顾问。根据以下确定性计算的结果，用 2~4 句话给出实质性的选课建议。\n"
                f"当前第 {profile['current_semester']} 学期，已获 {total_earned} / 需修 {total_required} 学分。\n"
                f"学分缺口：{gap_text or '已满足要求'}。\n"
                f"下学期课程：{next_courses or '无必修课'}。\n"
                "建议要具体、可执行，指出优先补哪些、节奏如何安排。"
            )
        return _llm().invoke(prompt).content.strip()
    except Exception:
        return ""


def _review_node(state: GraphState) -> dict[str, Any]:
    profile, curriculum = state["profile"], state["curriculum"]
    records = st.session_state.get("transcript_records", [])
    plan = build_plan(profile, curriculum, mode="original")
    review = {
        "completed_courses": plan.get("completed_courses", []),
        "gap": plan.get("gap", {}),
        "gpa": calculate_gpa(records) if records else None,
        "total_earned": plan.get("total_earned"),
        "total_required": plan.get("total_required"),
        "records": records,
    }
    return {"review_result": review}


def _missing_node(state: GraphState) -> dict[str, Any]:
    """查询缺失哪些学期的成绩单。确定性计算。"""
    from app.core.calc import missing_semester_labels
    profile = state["profile"]
    records = st.session_state.get("transcript_records", [])
    received = {r.get("semester") for r in records}
    missing = missing_semester_labels(profile["current_semester"], received)
    return {"review_result": {"missing_semesters": missing, "current_semester": profile["current_semester"], "received": received}}


def _answer_node(state: GraphState) -> dict[str, Any]:
    """问答节点：上下文含「培养方案原文 + 成绩单 + 学分要求」，由 LLM 针对问题作答。

    这样「成绩最高的一门」「能修的所有课程」等具体问题能得到直接回答，
    而不是机械返回整张成绩单。
    """
    profile = state["profile"]
    curriculum = state.get("curriculum") or {}
    question = state["question"]
    records = st.session_state.get("transcript_records", [])

    context_parts = []

    # 1. 学分要求（结构化）
    reqs = curriculum.get("credit_requirements") or {}
    if reqs:
        context_parts.append("【学分要求】\n" + "\n".join(f"- {k}：{v} 学分" for k, v in reqs.items()))

    # 2. 成绩单（学生已修课程）
    if records:
        lines = ["【学生已修课程与成绩】"]
        for r in records:
            lines.append(f"- {r['course']}：{r.get('score')}分，{r.get('credits')}学分，第{r.get('semester')}学期，绩点{r.get('gpa')}")
        context_parts.append("\n".join(lines))

    # 3. 培养方案原文（课程表、培养目标等）
    full_text = curriculum.get("raw_text", "")
    if full_text:
        context_parts.append("【培养方案原文】\n" + full_text)

    # 若原文缺失，向量检索兜底
    if not full_text:
        from app.core.rag import retrieve_sections
        sections = retrieve_sections(question)
        if sections:
            context_parts.append("【检索资料】\n" + "\n\n".join(f"【{s['title']}】\n{s['content']}" for s in sections))

    context = "\n\n".join(context_parts)
    if not context.strip():
        return {"answer": "当前工作区尚未绑定培养方案或缺少资料，请先在上一步上传培养方案。"}

    model = _llm().bind_tools(ALL_TOOLS)
    prompt = (
        f"你是「{profile['college']} · {profile['major']}」的培养方案问答助手。\n\n"
        "你可以使用工具查询课程的学分、开课学期、学分要求等结构化信息。\n"
        "回答要求：\n"
        "1. 针对学生的具体问题直接作答，不要机械罗列全部数据。\n"
        "2. 例如问「成绩最高的一门」就答出具体哪门课；问「能修的所有课程」就完整列出课程。\n"
        "3. 数字必须准确，条理清晰。若资料不足，明确说明。\n\n"
        f"【资料】\n{context}\n\n"
        f"【问题】\n{question}"
    )
    result = model.invoke(prompt)
    answer_text = result.content if result.content else ""
    if getattr(result, "tool_calls", None):
        answer_text = _run_tool_loop(model, prompt, result.tool_calls)
    return {"answer": answer_text, "citations": []}


def _run_tool_loop(model, prompt: str, tool_calls: list) -> str:
    """执行工具调用，并把结果反馈给 LLM 生成最终回答。"""
    from langchain_core.messages import AIMessage, ToolMessage
    from app.core.tools import ALL_TOOLS

    tools_by_name = {t.name: t for t in ALL_TOOLS}
    tool_messages = []
    for call in tool_calls:
        name = call.get("name")
        args = call.get("args", {})
        tool = tools_by_name.get(name)
        if tool:
            try:
                result = tool.invoke(args)
                tool_messages.append(ToolMessage(content=str(result), tool_call_id=call.get("id", name)))
            except Exception as e:
                tool_messages.append(ToolMessage(content=f"工具调用失败：{e}", tool_call_id=call.get("id", name)))
    if not tool_messages:
        return ""
    followup = model.invoke([prompt, AIMessage(content="", tool_calls=tool_calls), *tool_messages])
    return followup.content or ""


def build_graph():
    workflow = StateGraph(GraphState)

    workflow.add_node("classify", _classify)
    workflow.add_node("plan", _plan_node)
    workflow.add_node("semester_plan", _semester_plan_node)
    workflow.add_node("missing", _missing_node)
    workflow.add_node("answer", _answer_node)

    workflow.set_entry_point("classify")
    # review 和 qa 都交给 answer（含成绩单+方案上下文，能智能回答具体问题）
    workflow.add_conditional_edges(
        "classify",
        lambda state: state["intent"],
        {"plan": "plan", "gap": "plan", "semester_plan": "semester_plan", "review": "answer", "missing": "missing", "qa": "answer"},
    )
    workflow.add_edge("plan", END)
    workflow.add_edge("semester_plan", END)
    workflow.add_edge("missing", END)
    workflow.add_edge("answer", END)

    return workflow.compile()


__all__ = ["build_graph", "index_documents"]
