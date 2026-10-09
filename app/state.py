from __future__ import annotations

import streamlit as st

# 各阶段之间需要持久化的状态，全部集中在这里，避免切页丢失。
DEFAULTS: dict[str, object] = {
    "stage": 1,                          # 当前阶段 1-4
    "parsed_catalog": [],                 # 解析出的课程目录
    "parsed": False,                      # 是否已解析培养方案
    "curriculum_pdf_bytes": None,         # 培养方案 PDF 字节
    "curriculum_pdf_name": None,          # 培养方案文件名
    "profile": None,                      # 学生画像
    "credits_confirmed": False,           # 是否已确认学分
    # —— 阶段 C 的中间状态，切页不丢 ——
    "transcript_records": [],             # 解析出的成绩单记录
    "transcript_statuses": {},            # 不及格课程状态
    "credit_mode": "手动填写",            # 录入方式
    "manual_credits": {},                 # 手动填写的各平台学分
    # —— 阶段 D 的中间状态，切页不丢 ——
    "messages": [],                      # 对话历史
    "index_ready": False,                # RAG 索引是否已建立
    "chat_page": "对话",                 # 对话页当前栏目
    "semester_confirmed": False,         # 学期是否已二次确认
    "show_adjust": False,                # 是否展开学期调整框
    "profile_current": 1,                # 确认后的当前学期
    "dialog_open": False,                # 学期确认弹窗是否打开
    "parsing": False,                    # 是否正在解析 PDF
}


def initialize() -> None:
    for key, value in DEFAULTS.items():
        st.session_state.setdefault(key, value)


def unlock(stage: int) -> bool:
    if stage == 1:
        return True
    if stage == 2:
        return st.session_state.parsed
    if stage == 3:
        return st.session_state.profile is not None
    if stage == 4:
        return st.session_state.credits_confirmed
    return False


def reset_after(stage: int) -> None:
    """当用户回到较早阶段并改动时，作废后续阶段的旧状态。"""
    if stage <= 1:
        st.session_state.profile = None
        st.session_state.credits_confirmed = False
        st.session_state.transcript_records = []
        st.session_state.transcript_statuses = {}
        st.session_state.messages = []
        st.session_state.index_ready = False
    if stage <= 2:
        st.session_state.credits_confirmed = False
        st.session_state.transcript_records = []
        st.session_state.transcript_statuses = {}
        st.session_state.messages = []
    if stage <= 3:
        st.session_state.messages = []
