from __future__ import annotations

import streamlit as st

from app.state import initialize
from app.views import chat, credits, manage, profile, promote, upload


def main() -> None:
    st.set_page_config(page_title="培养方案智能问答与选课规划", page_icon="🎓", layout="wide")
    initialize()

    # 工作区管理页（独立二级页面）
    if st.session_state.get("manage_page"):
        manage.render()
        return

    # 升学管理页（独立二级页面）
    if st.session_state.get("promote_page"):
        promote.render()
        return

    # 阶段 4 是独立的对话页面，不显示向导导航
    if st.session_state.stage == 4:
        chat.render()
        return

    st.title("培养方案智能问答与选课规划")
    st.caption("先完成信息采集（三步），进入独立的智能对话页面。")
    _setup_wizard()


def _setup_wizard() -> None:
    from app.state import unlock

    steps = ((1, "上传并扫描方案"), (2, "选择专业·学生信息"), (3, "过往学分"))
    columns = st.columns(len(steps))
    current = st.session_state.stage
    done_flags = {
        1: st.session_state.parsed,
        2: st.session_state.profile is not None,
        3: st.session_state.credits_confirmed,
    }
    for column, (number, title) in zip(columns, steps):
        done = done_flags[number]
        mark = "✅" if done else ("🔵" if number == current else "⚪")
        with column:
            if st.button(f"{mark} {number}. {title}", key=f"step_{number}", use_container_width=True, disabled=not unlock(number)):
                st.session_state.stage = number
                st.rerun()
    st.divider()
    stage = st.session_state.stage
    if stage == 1:
        upload.render()
    elif stage == 2:
        profile.render()
    elif stage == 3:
        credits.render()


if __name__ == "__main__":
    main()
