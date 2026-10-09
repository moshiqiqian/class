from __future__ import annotations

from langchain_core.tools import tool

# 这些工具让 LLM 在回答时可以主动查询结构化数据，
# 而不必依赖向量检索（结构化数据更精确）。


@tool
def query_credit_requirements() -> str:
    """查询本专业的各课程平台毕业学分要求（如通识必修、专业必修各需多少学分）。"""
    curriculum = _get_curriculum()
    if not curriculum:
        return "无课程表数据。"
    reqs = curriculum.get("credit_requirements") or {}
    if not reqs:
        return "未解析到学分要求。"
    lines = ["本专业各课程平台毕业学分要求："]
    for platform, credits in reqs.items():
        lines.append(f"- {platform}：{credits} 学分")
    return "\n".join(lines)


@tool
def query_course_info(course_name: str) -> str:
    """查询某门课程的详细信息（学分、课程类别、开课学期）。

    Args:
        course_name: 课程名称，如「数据结构」「高等数学」
    """
    curriculum = _get_curriculum()
    if not curriculum:
        return "无课程表数据。"
    matches = []
    for course in curriculum.get("courses", []):
        if course_name in course["name"]:
            matches.append(course)
    if not matches:
        return f"未找到课程「{course_name}」。"
    lines = [f"找到 {len(matches)} 门匹配课程："]
    for c in matches[:10]:
        lines.append(f"- {c['name']}：{c.get('category', c.get('platform', ''))}，{c['credits']} 学分，第 {c.get('semester', '?')} 学期")
    return "\n".join(lines)


@tool
def query_semester_courses(semester: int) -> str:
    """查询某学期开设的课程列表。

    Args:
        semester: 学期号（1-8）
    """
    curriculum = _get_curriculum()
    if not curriculum:
        return "无课程表数据。"
    matches = [c for c in curriculum.get("courses", []) if int(c.get("semester", 0)) == semester]
    if not matches:
        return f"第 {semester} 学期暂无课程。"
    lines = [f"第 {semester} 学期开设课程："]
    for c in matches:
        lines.append(f"- {c['name']}（{c.get('category', c.get('platform', ''))}，{c['credits']} 学分）")
    return "\n".join(lines)


def _get_curriculum() -> dict:
    """从当前会话读取选定的专业课程表。工具在 LangGraph 上下文中执行。"""
    import streamlit as st
    profile = st.session_state.get("profile")
    if not profile:
        return {}
    for item in st.session_state.get("parsed_catalog", []):
        if item["college"] == profile["college"] and item["major"] == profile["major"]:
            return item
    return {}


ALL_TOOLS = [query_credit_requirements, query_course_info, query_semester_courses]
