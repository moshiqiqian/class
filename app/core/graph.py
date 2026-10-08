from __future__ import annotations

from typing import Any, TypedDict

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


# 规划模式关键词（询问「怎么安排/怎么规划」）
_PLAN_MODE_KEYWORDS = ("怎么规划", "怎么安排", "如何规划", "如何安排", "选课规划", "安排模式", "提前修完", "大四前", "平均分配")
# 学分缺口查询关键词（直接回答，不给规划建议）
_GAP_KEYWORDS = ("还差", "缺口", "差多少", "修多少", "学分够", "学分够不够", "学分还差", "缺多少")
# 选课规划类（广义，命中即走规划）
_PLAN_KEYWORDS = ("选课", "规划", "下学期", "选什么课", "课程安排", "重修", "补考")


def _classify(state: GraphState) -> dict[str, str]:
    question = state.get("question", "")
    # 优先级：规划模式 > 缺口查询 > 选课规划 > 问答
    if any(word in question for word in _PLAN_MODE_KEYWORDS):
        return {"intent": "plan_mode"}
    if any(word in question for word in _GAP_KEYWORDS):
        return {"intent": "gap"}
    if any(word in question for word in _PLAN_KEYWORDS):
        return {"intent": "plan"}
    return {"intent": "qa"}


def _plan_node(state: GraphState) -> dict[str, Any]:
    profile, curriculum = state["profile"], state["curriculum"]
    advice = advisory_notes(profile["major"], profile.get("completed_courses", []))
    # plan 意图默认「平均分配」模式
    return {"plan_result": build_plan(profile, curriculum, advice, mode="balanced")}


def _answer_node(state: GraphState) -> dict[str, Any]:
    profile = state["profile"]
    curriculum = state.get("curriculum") or {}
    extra = _build_extra_context(curriculum)
    text, citations = answer(state["question"], profile["college"], profile["major"], extra_context=extra)
    return {"answer": text, "citations": citations}


def _build_extra_context(curriculum: dict) -> str:
    """把结构化解析出的学分要求等信息拼成文本，注入问答上下文。

    这些数据在 PDF 中是复杂表格，向量检索容易漏检或切碎，直接注入最可靠。
    """
    parts = []
    requirements = curriculum.get("credit_requirements") or {}
    if requirements:
        lines = ["本专业各课程平台毕业学分要求："]
        for platform, credits in requirements.items():
            lines.append(f"- {platform}：{credits} 学分")
        parts.append("\n".join(lines))
    return "\n".join(parts)


def build_graph():
    workflow = StateGraph(GraphState)

    workflow.add_node("classify", _classify)
    workflow.add_node("plan", _plan_node)
    workflow.add_node("answer", _answer_node)

    workflow.set_entry_point("classify")
    # plan_mode / gap / plan 都走规划节点（gap 在 view 层只展示缺口，不展示规划）
    workflow.add_conditional_edges(
        "classify",
        lambda state: state["intent"],
        {"plan": "plan", "plan_mode": "plan", "gap": "plan", "qa": "answer"},
    )
    workflow.add_edge("plan", END)
    workflow.add_edge("answer", END)

    return workflow.compile()


__all__ = ["build_graph", "index_documents"]
