from __future__ import annotations

from collections.abc import Callable

import streamlit as st


def footer(go_back_to: int, next_label: str, next_stage: int, on_next: Callable[[], None] | None = None, disabled: bool = False) -> None:
    """统一底部操作栏：上一步 / 下一步。

    go_back_to：上一步目标阶段编号（0 表示无上一步）。
    next_stage：点击「下一步」后跳转到的阶段。
    on_next：跳转前需要执行的保存回调（先保存，再跳转）。
    """
    st.divider()
    left, right = st.columns(2)
    with left:
        if st.button("← 上一步", use_container_width=True, disabled=go_back_to < 1):
            st.session_state.stage = go_back_to
            st.rerun()
    with right:
        if st.button(next_label, type="primary", use_container_width=True, disabled=disabled):
            if on_next:
                on_next()
            st.session_state.stage = next_stage
            st.rerun()
