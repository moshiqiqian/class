from __future__ import annotations

import json
import os
import re
from typing import Any, TypedDict

import streamlit as st
from langgraph.graph import END, StateGraph

from app.core.calc import build_plan, calculate_gpa
from app.core.prerequisite import advisory_notes
from app.core.rag import answer, index_documents


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


# 意图分类：LLM 输出结构化 JSON，覆盖 4 类意图 + 规划模式
_INTENT_PROMPT = (
    "判断学生问题属于哪一类，并判断规划模式。只输出 JSON，不要输出其他内容：\n"
    '{{"intent": "plan|gap|review|qa", "mode": "early|balanced|original|none"}}\n\n'
    "intent 含义：\n"
    "- plan：选课规划（问「下学期选什么课」「怎么规划」「课程怎么安排」「提前修完」「平均分配」）\n"
    "- gap：学分缺口查询（问「还差多少学分」「学分够不够毕业」「缺口」）\n"
    "- review：回顾已修课程/成绩/绩点（问「我之前学了什么」「绩点多少」「已修了哪些课」「成绩单」）\n"
    "- qa：培养方案知识问答（问「培养目标」「毕业要求」「学制」「学位」「某课程学分」「核心课程」等）\n\n"
    "mode 含义（仅当 intent=plan 时有效）：\n"
    "- early：提到提前修完/大四前修完\n"
    "- balanced：提到平均分配/均衡安排\n"
    "- original：按培养方案原始安排（默认）\n\n"
    "问题：{question}\n"
    "JSON："
)

# 关键词兜底（LLM 不可用或解析失败时）
_PLAN_MODE_KEYWORDS = ("怎么规划", "怎么安排", "如何规划", "如何安排", "选课规划", "安排模式", "提前修完", "大四前", "平均分配")
_GAP_KEYWORDS = ("还差", "缺口", "差多少", "修多少", "学分够", "学分还差", "缺多少")
_REVIEW_KEYWORDS = ("已修", "学过", "绩点", "gpa", "GPA", "成绩单", "回顾", "之前学了", "修了什么")
_PLAN_KEYWORDS = ("选课", "规划", "下学期", "选什么课", "课程安排", "重修", "补考")


def _classify(state: GraphState) -> dict[str, str]:
    question = state.get("question", "")
    intent, mode = _classify_intent(question)
    return {"intent": intent, "mode": mode}


def _classify_intent(question: str) -> tuple[str, str]:
    """用 LLM 分类意图 + 规划模式；失败回退关键词。返回 (intent, mode)。"""
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if key:
        try:
            from app.core.rag import _llm
            raw = _llm().invoke(_INTENT_PROMPT.format(question=question)).content.strip()
            parsed = _parse_intent_json(raw)
            if parsed:
                intent = parsed.get("intent", "qa")
                if intent not in ("plan", "gap", "review", "qa"):
                    intent = "qa"
                mode = parsed.get("mode", "balanced")
                if mode not in ("early", "balanced", "original"):
                    mode = "balanced"
                return intent, mode
        except Exception:
            pass  # 回退关键词

    # 关键词兜底
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
    """解析 LLM 返回的 JSON，容忍多余文本。"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 提取 { ... } 子串再试
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return None
    return None


def _plan_node(state: GraphState) -> dict[str, Any]:
    profile, curriculum = state["profile"], state["curriculum"]
    advice = advisory_notes(profile["major"], profile.get("completed_courses", []))
    mode = state.get("mode", "balanced")
    return {"plan_result": build_plan(profile, curriculum, advice, mode=mode)}


def _review_node(state: GraphState) -> dict[str, Any]:
    """回顾已修课程 + 绩点。确定性计算，不依赖 LLM。"""
    profile, curriculum = state["profile"], state["curriculum"]
    records = st.session_state.get("transcript_records", [])
    plan = build_plan(profile, curriculum, mode="original")
    review = {
        "completed_courses": plan.get("completed_courses", []),
        "gap": plan.get("gap", {}),
        "gpa": calculate_gpa(records) if records else None,
        "total_earned": plan.get("total_earned"),
        "total_required": plan.get("total_required"),
    }
    return {"review_result": review}


def _answer_node(state: GraphState) -> dict[str, Any]:
    profile = state["profile"]
    curriculum = state.get("curriculum") or {}
    extra = _build_extra_context(curriculum)
    full_text = curriculum.get("raw_text", "")
    text, citations = answer(state["question"], profile["college"], profile["major"], extra_context=extra, full_text=full_text)
    return {"answer": text, "citations": citations}


def _build_extra_context(curriculum: dict) -> str:
    """把结构化解析出的学分要求拼成文本，注入问答上下文。"""
    parts = []
    requirements = curriculum.get("credit_requirements") or {}
    if requirements:
        lines = ["本专业各课程平台毕业学分要求："]
        for platform, credits in requirements.items():
            lines.append(f"- {platform}：{credits} 学分")
        parts.append("\n".join(lines))
    return "\n".join(parts)


@st.cache_resource(show_spinner=False)
def build_graph():
    workflow = StateGraph(GraphState)

    workflow.add_node("classify", _classify)
    workflow.add_node("plan", _plan_node)
    workflow.add_node("review", _review_node)
    workflow.add_node("answer", _answer_node)

    workflow.set_entry_point("classify")
    # 四种意图 → 三个处理节点（gap 和 plan 都走 plan，但 gap 只展示缺口）
    workflow.add_conditional_edges(
        "classify",
        lambda state: state["intent"],
        {"plan": "plan", "gap": "plan", "review": "review", "qa": "answer"},
    )
    workflow.add_edge("plan", END)
    workflow.add_edge("review", END)
    workflow.add_edge("answer", END)

    return workflow.compile()


__all__ = ["build_graph", "index_documents"]
