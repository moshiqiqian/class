from __future__ import annotations

import re
from io import BytesIO

import pdfplumber

# 课表单元格：课程名 + ★/☆/■ 标记 + (节次)周次/... + 学分
_MARKERS = "★☆■◇◆"
_NAME_RE = re.compile(r"^(?P<name>[\u4e00-\u9fffA-Za-z0-9（）()·]+?)\s*[" + re.escape(_MARKERS) + r"]")
_CREDIT_RE = re.compile(r"学分[:：]\s*([\d.]+)")
_SLOT_RE = re.compile(r"\((\d+)\s*-\s*(\d+)节\)")
_WEEKS_RE = re.compile(r"节\)\s*([0-9,\-单双周]+)周")
_HEADERS = {"课程名", "时间段", "节次", "上午", "下午", "晚上"}


def _parse_cell(cell) -> dict | None:
    if not cell:
        return None
    text = re.sub(r"\s+", "", str(cell))
    match = _NAME_RE.match(text)
    if not match:
        return None
    name = match.group("name").strip()
    if len(name) < 2 or name in _HEADERS:
        return None
    credit_match = _CREDIT_RE.search(text)
    credits = float(credit_match.group(1)) if credit_match else 0.0
    weeks = ""
    if wk := _WEEKS_RE.search(text):
        weeks = wk.group(1)
    slots = ""
    if sl := _SLOT_RE.search(text):
        slots = f"{sl.group(1)}-{sl.group(2)}节"
    return {"name": name, "credits": credits, "weeks": weeks, "slots": slots, "raw": text}


def parse_schedule(pdf_bytes: bytes, filename: str = "") -> list[dict]:
    """解析教务课表 PDF，返回去重后的课程列表 [{name, credits, weeks, slots}]。

    同一门课可能以「理论★」和「实验☆」两个格出现，按课程名合并（学分取较大值）。
    """
    courses: dict[str, dict] = {}
    full_parts: list[str] = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            full_parts.append(page.extract_text() or "")
            for table in page.extract_tables() or []:
                for row in table:
                    for cell in row:
                        item = _parse_cell(cell)
                        if not item:
                            continue
                        name = item["name"]
                        prev = courses.get(name)
                        if prev is None:
                            courses[name] = item
                        else:
                            prev["credits"] = max(prev["credits"], item["credits"])
                            for key in ("weeks", "slots"):
                                if item[key] and item[key] not in prev[key]:
                                    prev[key] = (prev[key] + "," + item[key]).strip(",")
    # 兜底：跨页续表会把「学分」挤到下一页单元格，导致 0；用全文就近补齐
    full = re.sub(r"\s+", "", "\n".join(full_parts))
    for name, item in courses.items():
        if item["credits"] <= 0:
            match = re.search(re.escape(name) + r".{0,400}?学分[:：]([\d.]+)", full)
            if match:
                item["credits"] = float(match.group(1))
    return list(courses.values())


def schedule_to_courses(items: list[dict], college: str, major: str, semester: int) -> list[dict]:
    """把课表条目转成课程记录，便于与成绩单合并计算实际修读状态。"""
    result = []
    for it in items:
        result.append({
            "course": it["name"],
            "credits": float(it.get("credits", 0) or 0),
            "semester": semester,
            "score": None,
            "gpa": None,
            "source": "课表",
            "name": it["name"],
            "required": True,
        })
    return result
