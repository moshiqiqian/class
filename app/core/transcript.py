from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd
import pdfplumber

# 列名别名：兼容不同学校/系统的成绩单表头
_COLUMN_ALIASES = {
    "course": ("课程名称", "课程", "course"),
    "score": ("成绩", "分数", "score"),
    "credits": ("学分", "credits"),
    "semester": ("学期", "semester"),
    "platform": ("课程性质", "课程类别", "课程类型", "platform"),
    "academic_year": ("学年", "academic_year", "学年度"),
    "gpa": ("绩点", "gpa", "成绩绩点"),
}

# 成绩单「课程性质」→ 培养方案平台
_PLATFORM_MAP = {
    "通识必修课": "通识必修", "通识必修": "通识必修",
    "通识选修课": "通识选修", "通识选修": "通识选修",
    "学科基础课": "学科基础", "学科基础": "学科基础",
    "专业必修课": "专业必修", "专业必修": "专业必修",
    "专业选修课": "专业选修", "专业选修": "专业选修",
    "实践必修课": "实践必修", "实践必修": "实践必修",
    "实践教学": "实践必修",
}


def _find_col(frame: pd.DataFrame, kind: str) -> str | None:
    """按别名找列名，返回实际列名或 None。"""
    normalized = {str(col).strip(): col for col in frame.columns}
    for name in _COLUMN_ALIASES[kind]:
        for actual, original in normalized.items():
            if actual.lower() == name.lower():
                return original
    return None


def _to_int(value: object) -> int | None:
    """把单元格值解析为整数（兼容字符串/浮点）。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    match = re.search(r"-?\d+", text)
    return int(match.group()) if match else None


def _to_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def _passed(value: object) -> bool:
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value) >= 60
    return str(value).strip().lower() in {"及格", "合格", "通过", "pass"}


def _map_platform(value: object) -> str | None:
    text = str(value or "").strip()
    return _PLATFORM_MAP.get(text)


def _to_term(value: object) -> int | None:
    """解析学期值：兼容阿拉伯数字与中文数字（一~八）。"""
    text = str(value).strip()
    if match := re.search(r"[1-8]", text):
        return int(match.group())
    mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}
    for cn, num in mapping.items():
        if cn in text:
            return num
    return None


def _academic_term(academic_year: object, semester_in_year: object, enrollment_year: int | None) -> int | None:
    """从「学年（如 2025-2026）+ 学期（1/2）」推算绝对学期号（1-8）。

    公式：绝对学期 = (学年起始年 - 入学年) * 2 + 学年内学期号。
    例如入学 2023，学年 2025-2026、学期 1 → (2025-2023)*2 + 1 = 5（大三上）。
    """
    year_match = re.search(r"(20\d{2})", str(academic_year or ""))
    term = _to_term(semester_in_year)
    if not year_match or term is None or enrollment_year is None:
        return None
    return (int(year_match.group(1)) - enrollment_year) * 2 + term


def semester_from_filename(filename: str) -> int | None:
    match = re.search(r"(?:20\d{2}[-_]20\d{2}|第?\s*\d+)\D*([1-8])(?:学期)?", filename)
    return int(match.group(1)) if match else None


def semester_from_academic_label(text: str, enrollment_year: int | None) -> int | None:
    """从「2023-2024学年第一学期」这类描述换算为绝对学期号（1-8）。"""
    year_match = re.search(r"(20\d{2})\s*[-—~至]\s*(20\d{2})\s*学[年年度]", text)
    term_match = re.search(r"第\s*([一二三四五六七八1-8])\s*学期", text)
    if not year_match or not term_match:
        return None
    term = _to_term(term_match.group(1))
    if term is None or enrollment_year is None:
        return None
    return (int(year_match.group(1)) - enrollment_year) * 2 + term


def records_from_dataframe(frame: pd.DataFrame, fallback_semester: int | None = None, enrollment_year: int | None = None) -> list[dict]:
    """从成绩单 DataFrame 解析课程记录。

    优先识别成绩单自带的「课程性质」（直接归类平台，无需对接培养方案）、
    「学年+学期」（推算绝对学期）、「绩点」列。
    """
    course_col = _find_col(frame, "course")
    score_col = _find_col(frame, "score")
    credits_col = _find_col(frame, "credits")
    semester_col = _find_col(frame, "semester")
    platform_col = _find_col(frame, "platform")
    academic_year_col = _find_col(frame, "academic_year")
    gpa_col = _find_col(frame, "gpa")

    if not all((course_col, score_col, credits_col)):
        raise ValueError("未识别到「课程名称、成绩、学分」三列，请改用手动录入。")

    result = []
    for _, row in frame.iterrows():
        name = str(row[course_col]).strip()
        if not name or name.lower() == "nan":
            continue
        score = row[score_col]
        credits = _to_float(row[credits_col]) or 0.0
        # 清洗：成绩为空/NaN 时跳过该行，避免后续计算异常
        if score is None or (isinstance(score, float) and pd.isna(score)) or str(score).strip() in ("", "nan"):
            continue

        # 学期：优先「学年+学期」推算，其次「学期」列，最后 fallback
        term = fallback_semester
        if academic_year_col and _find_col(frame, "academic_year"):
            if computed := _academic_term(row[academic_year_col], row[semester_col] if semester_col else None, enrollment_year):
                term = computed
        if term is None and semester_col:
            term = _to_term(row[semester_col])

        # 平台：优先成绩单自带的「课程性质」
        platform = _map_platform(row[platform_col]) if platform_col else None

        # 绩点：优先成绩单自带，否则 None（由调用方用成绩换算）
        gpa = _to_float(row[gpa_col]) if gpa_col else None

        result.append({
            "course": name,
            "score": score,
            "credits": credits,
            "semester": term,
            "platform": platform,
            "gpa": gpa,
            "passed": _passed(score),
        })
    return result


def parse_upload(uploaded: object, manual_semester: int | None = None, enrollment_year: int | None = None) -> list[dict]:
    filename = getattr(uploaded, "name", "")
    raw, suffix = uploaded.getvalue(), Path(filename).suffix.lower()
    inferred = manual_semester or semester_from_academic_label(filename, enrollment_year) or semester_from_filename(filename)

    if suffix in {".xlsx", ".xls", ".csv"}:
        frame = pd.read_csv(io.BytesIO(raw)) if suffix == ".csv" else pd.read_excel(io.BytesIO(raw))
        return records_from_dataframe(frame, inferred, enrollment_year)

    if suffix == ".pdf":
        with pdfplumber.open(io.BytesIO(raw)) as document:
            tables = [table for page in document.pages for table in page.extract_tables() if table]
            text = "\n".join(page.extract_text() or "" for page in document.pages)
        if content_term := semester_from_academic_label(text, enrollment_year):
            inferred = inferred or content_term
        if tables:
            for table in tables:
                if len(table) > 1:
                    try:
                        return records_from_dataframe(pd.DataFrame(table[1:], columns=table[0]), inferred, enrollment_year)
                    except ValueError:
                        continue
        rows = [re.split(r"\s{2,}|\t|,", line.strip()) for line in text.splitlines()]
        return records_from_dataframe(pd.DataFrame([row[:3] for row in rows if len(row) >= 3], columns=["课程名称", "成绩", "学分"]), inferred, enrollment_year)

    if suffix == ".txt":
        text = raw.decode("utf-8-sig")
        if content_term := semester_from_academic_label(text, enrollment_year):
            inferred = inferred or content_term
        rows = [re.split(r"\s{2,}|\t|,", line.strip()) for line in text.splitlines()]
        return records_from_dataframe(pd.DataFrame([row[:3] for row in rows if len(row) >= 3], columns=["课程名称", "成绩", "学分"]), inferred, enrollment_year)

    raise ValueError("仅支持 PDF、Excel、CSV 和 TXT 成绩单。")
