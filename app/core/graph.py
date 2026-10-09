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
    answer: str
    citations: list[dict]
    plan_result: dict
    review_result: dict


# 意图分类：LLM 输出结构化 JSON
_INTENT_PROMPT = (
    "判断学生问题属于哪一类，并判断规划模式。只输出 JSON，不要输出其他内容：\n"
    '{{"intent": "plan|gap|review|missing|qa", "mode": "early|balanced|original|none"}}\n\n'
    "分类规则：\n"
    "- plan：选课规划（问「下学期选什么课」「怎么规划课程」「课程怎么安排」「提前修完」「平均分配」）\n"
    "- gap：学分缺口（问「还差多少学分」「学分够不够毕业」）\n"
    "- review：查询「我自己」的成绩/绩点/已修课程（问「我绩点多少」「我学过哪些课」「我的成绩单」）\n"
    "- missing：查询缺失哪些学期的成绩单（问「缺哪几个学期」）\n"
    "- qa：其他所有培养方案知识问答（培养目标、毕业要求、某课程学分/学期/考核、核心课程、学制、学位、专业介绍等）\n\n"
    "重要区分：\n"
    "- 问「某门课的学分/开课学期/课程内容」→ qa（不是 review）\n"
    "- 问「我的成绩/我的绩点/我学过什么」→ review\n"
    "- 问「培养方案里对成绩/考核的要求」→ qa\n"
    "- 拿不准时选 qa\n\n"
    "mode 仅当 intent=plan 时有意义：early=提前修完, balanced=平均分配, original=原始安排\n\n"
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
    intent, mode = _classify_intent(question)
    return {"intent": intent, "mode": mode}


def _classify_intent(question: str) -> tuple[str, str]:
    """用 LLM 分类意图 + 规划模式；失败回退关键词。返回 (intent, mode)。"""
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if key:
        try:
            raw = _llm().invoke(_INTENT_PROMPT.format(question=question)).content.strip()
            parsed = _parse_intent_json(raw)
            if parsed:
                intent = parsed.get("intent", "qa")
                if intent not in ("plan", "gap", "review", "missing", "qa"):
                    intent = "qa"
                mode = parsed.get("mode", "balanced")
                if mode not in ("early", "balanced", "original"):
                    mode = "balanced"
                return intent, mode
        except Exception:
            pass  # 回退关键词

    # 关键词兜底（注意顺序：missing 优先于 gap，因为「缺少」比「还差」更具体）
    if any(word in question for word in _MISSING_KEYWORDS):
        return "missing", "balanced"
    if any(word in question for word in _GAP_KEYWORDS):
        return "gap", "balanced"
    if any(word in question for word in _REVIEW_KEYWORDS):
        return "review", "balanced"
    if any(word in question for word in _PLAN_MODE_KEYWORDS):
        if "提前" in question or "大四前" in question:
            return "plan", "early"
        if "平均" in question or "均衡" in question:
            return "plan", "balanced"
        return "plan", "original"
    if any(word in question for word in _PLAN_KEYWORDS):
        return "plan", "balanced"
    return "qa", "balanced"


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
    plan = build_plan(profile, curriculum, advice, mode=mode)
    # 基于结构化结果，让 LLM 生成自然语言建议
    plan["suggestion"] = _generate_plan_suggestion(profile, plan)
    return {"plan_result": plan}


def _generate_plan_suggestion(profile: dict, plan: dict) -> str:
    """基于确定性规划结果，生成自然语言的选课建议。"""
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if not key:
        return ""
    try:
        gap_text = "、".join(f"{k}差{v['missing']}学分" for k, v in plan["gap"].items() if v["missing"] > 0)
        next_courses = "、".join(c["课程名称"] for c in plan.get("next_courses", []))
        total_earned = plan.get("total_earned", 0)
        total_required = plan.get("total_required", 0)
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
    workflow.add_node("missing", _missing_node)
    workflow.add_node("answer", _answer_node)

    workflow.set_entry_point("classify")
    # review 和 qa 都交给 answer（含成绩单+方案上下文，能智能回答具体问题）
    workflow.add_conditional_edges(
        "classify",
        lambda state: state["intent"],
        {"plan": "plan", "gap": "plan", "review": "answer", "missing": "missing", "qa": "answer"},
    )
    workflow.add_edge("plan", END)
    workflow.add_edge("missing", END)
    workflow.add_edge("answer", END)

    return workflow.compile()


__all__ = ["build_graph", "index_documents"]
