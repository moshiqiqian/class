from __future__ import annotations

import os
from typing import Any, TypedDict

import streamlit as st
from langgraph.graph import END, StateGraph

from app.core.calc import build_plan
from app.core.prerequisite import advisory_notes
from app.core.rag import answer, index_documents


class GraphState(TypedDict, total=False):
    question: str
    profile: dict
    curriculum: dict
    intent: str
    answer: str
    citations: list[dict]
    plan_result: dict


# 意图分类：优先用 LLM 判断（兼容更多问法），失败时回退关键词
_INTENT_PROMPT = (
    "请判断学生问题属于以下哪一类，只回复类别名：\n"
    "- plan：选课规划/课程安排（问「下学期选什么课」「怎么规划」「课程怎么安排」）\n"
    "- gap：学分缺口查询（问「还差多少学分」「学分够不够」「缺口」）\n"
    "- qa：培养方案知识问答（问「培养目标」「毕业要求」「某课程学分」「学制」「学位」等）\n"
    "问题：{question}\n"
    "类别："
)

# 关键词兜底（LLM 不可用时）
_PLAN_MODE_KEYWORDS = ("怎么规划", "怎么安排", "如何规划", "如何安排", "选课规划", "安排模式", "提前修完", "大四前", "平均分配")
_GAP_KEYWORDS = ("还差", "缺口", "差多少", "修多少", "学分够", "学分还差", "缺多少")
_PLAN_KEYWORDS = ("选课", "规划", "下学期", "选什么课", "课程安排", "重修", "补考")


def _classify(state: GraphState) -> dict[str, str]:
    question = state.get("question", "")
    return {"intent": _classify_intent(question)}


def _classify_intent(question: str) -> str:
    """用 LLM 分类意图；LLM 不可用时回退关键词匹配。"""
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if key:
        try:
            from app.core.rag import _llm
            result = _llm().invoke(_INTENT_PROMPT.format(question=question)).content.strip()
            for intent in ("plan", "gap", "qa"):
                if intent in result:
                    return intent
        except Exception:
            pass  # 回退关键词
    # 关键词兜底
    if any(word in question for word in _PLAN_MODE_KEYWORDS):
        return "plan"
    if any(word in question for word in _GAP_KEYWORDS):
        return "gap"
    if any(word in question for word in _PLAN_KEYWORDS):
        return "plan"
    return "qa"


def _plan_node(state: GraphState) -> dict[str, Any]:
    profile, curriculum = state["profile"], state["curriculum"]
    advice = advisory_notes(profile["major"], profile.get("completed_courses", []))
    return {"plan_result": build_plan(profile, curriculum, advice, mode="balanced")}


def _answer_node(state: GraphState) -> dict[str, Any]:
    profile = state["profile"]
    curriculum = state.get("curriculum") or {}
    extra = _build_extra_context(curriculum)
    # 传入完整专业原文，让回答质量接近「直接读全文」
    full_text = curriculum.get("raw_text", "")
    text, citations = answer(state["question"], profile["college"], profile["major"], extra_context=extra, full_text=full_text)
    return {"answer": text, "citations": citations}


def _build_extra_context(curriculum: dict) -> str:
    """把结构化解析出的学分要求、课程列表拼成文本，注入问答上下文。"""
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
    workflow.add_node("answer", _answer_node)

    workflow.set_entry_point("classify")
    workflow.add_conditional_edges(
        "classify",
        lambda state: state["intent"],
        {"plan": "plan", "gap": "plan", "qa": "answer"},
    )
    workflow.add_edge("plan", END)
    workflow.add_edge("answer", END)

    return workflow.compile()


__all__ = ["build_graph", "index_documents"]
