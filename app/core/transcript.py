from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd
import pdfplumber

_ALIASES = {"course": ("课程名称", "课程", "course"), "score": ("成绩", "分数", "score"), "credits": ("学分", "credits"), "semester": ("学期", "semester")}


def semester_from_filename(filename: str) -> int | None:
    match = re.search(r"(?:20\d{2}[-_]20\d{2}|第?\s*\d+)\D*([1-8])(?:学期)?", filename)
    return int(match.group(1)) if match else None


def semester_from_academic_label(text: str, enrollment_year: int | None) -> int | None:
    """从「2023-2024学年第一学期」这类描述换算为绝对学期号（1-8）。

    需要入学年份：第1学期 = 入学年的秋季学期。
    例如入学 2023，则 2023-2024 学年第一学期 = 第1学期，第二学期 = 第2学期，
    2024-2025 学年第一学期 = 第3学期，依此类推。
    """
    year_match = re.search(r"(20\d{2})\s*[-—~至]\s*(20\d{2})\s*学[年年度]", text)
    term_match = re.search(r"第\s*([一二三四五六七八1-8])\s*学期", text)
    if not year_match or not term_match:
        return None
    start_year = int(year_match.group(1))
    term = _to_term(term_match.group(1))
    if term is None:
        return None
    if enrollment_year is None:
        return None
    # 学年起始年与入学年的差，每学年 2 学期
    offset = (start_year - enrollment_year) * 2
    return offset + term


def _column(frame: pd.DataFrame, kind: str) -> str | None:
    normalized = {str(column).strip().lower(): column for column in frame.columns}
    return next((normalized[name.lower()] for name in _ALIASES[kind] if name.lower() in normalized), None)


def _passed(value: object) -> bool:
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value) >= 60
    return str(value).strip().lower() in {"及格", "合格", "通过", "pass"}


def records_from_dataframe(frame: pd.DataFrame, fallback_semester: int | None = None) -> list[dict]:
    course, score, credits, semester = (_column(frame, name) for name in ("course", "score", "credits", "semester"))
    if not all((course, score, credits)):
        raise ValueError("未识别到课程名称、成绩、学分三列，请改用手动录入。")
    result = []
    for _, row in frame.iterrows():
        name = str(row[course]).strip()
        if not name or name.lower() == "nan":
            continue
        value = row[score]
        term = fallback_semester
        if semester and pd.notna(row[semester]):
            term = _to_term(row[semester]) or term
        result.append({"course": name, "score": value, "credits": float(row[credits]), "semester": term, "passed": _passed(value)})
    return result


def _to_term(value: object) -> int | None:
    """解析学期值：兼容阿拉伯数字与中文数字（一~八）。"""
    import re as _re
    text = str(value).strip()
    if match := _re.search(r"[1-8]", text):
        return int(match.group())
    mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}
    for cn, num in mapping.items():
        if cn in text:
            return num
    return None


def parse_upload(uploaded: object, manual_semester: int | None = None, enrollment_year: int | None = None) -> list[dict]:
    filename = getattr(uploaded, "name", "")
    raw, suffix = uploaded.getvalue(), Path(filename).suffix.lower()
    # 学期优先级：手动指定 > 文件名/内容中的「学年+学期」描述 > 文件名数字
    inferred = manual_semester or semester_from_academic_label(filename, enrollment_year) or semester_from_filename(filename)
    if suffix in {".xlsx", ".xls", ".csv"}:
        frame = pd.read_csv(io.BytesIO(raw)) if suffix == ".csv" else pd.read_excel(io.BytesIO(raw))
        return records_from_dataframe(frame, inferred)
    if suffix == ".pdf":
        with pdfplumber.open(io.BytesIO(raw)) as document:
            tables = [table for page in document.pages for table in page.extract_tables() if table]
            text = "\n".join(page.extract_text() or "" for page in document.pages)
        # 优先从正文里找「学年+学期」描述
        if content_term := semester_from_academic_label(text, enrollment_year):
            inferred = inferred or content_term
        if tables:
            for table in tables:
                if len(table) > 1:
                    try:
                        return records_from_dataframe(pd.DataFrame(table[1:], columns=table[0]), inferred)
                    except ValueError:
                        continue
        rows = [re.split(r"\s{2,}|\t|,", line.strip()) for line in text.splitlines()]
        return records_from_dataframe(pd.DataFrame([row[:3] for row in rows if len(row) >= 3], columns=["课程名称", "成绩", "学分"]), inferred)
    if suffix == ".txt":
        text = raw.decode("utf-8-sig")
        if content_term := semester_from_academic_label(text, enrollment_year):
            inferred = inferred or content_term
        rows = [re.split(r"\s{2,}|\t|,", line.strip()) for line in text.splitlines()]
        return records_from_dataframe(pd.DataFrame([row[:3] for row in rows if len(row) >= 3], columns=["课程名称", "成绩", "学分"]), inferred)
    raise ValueError("仅支持 PDF、Excel、CSV 和 TXT 成绩单。")

