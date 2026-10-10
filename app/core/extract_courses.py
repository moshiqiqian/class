from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from io import BytesIO
from pathlib import Path

import pdfplumber

CACHE_DIR = Path("data/cache")


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _tight(value: object) -> str:
    """去掉所有空白，用于课程名/类别等不允许内部空格（PDF 换行所致）的字段。"""
    return re.sub(r"\s+", "", str(value or ""))


def _context(text: str, previous_college: str, previous_major: str) -> tuple[str, str]:
    college = previous_college
    major = previous_major
    # 学院：仅在明确的「XX学院/学部」标题行更新
    college_match = re.search(r"([\u4e00-\u9fff]{2,30}(?:学院|学部))\s*$", text, re.MULTILINE)
    # 专业：仅匹配「XX专业(类)(本科)人才培养方案/培养方案」这类正式标题，避免误抓正文里的「专业」字样
    major_match = re.search(r"([\u4e00-\u9fff]{2,30}专业(?:类)?)(?:本科)?(?:人才培养方案|培养方案)", text)
    if college_match:
        college = college_match.group(1)
    if major_match:
        major = major_match.group(1).replace("人才培养方案", "").replace("培养方案", "")
    return college, major


def _index(headers: list[str], candidates: tuple[str, ...]) -> int | None:
    for index, header in enumerate(headers):
        compact = re.sub(r"\s+", "", header)
        if any(re.sub(r"\s+", "", item) in compact for item in candidates):
            return index
    return None


_TERM_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}


def _to_term(value: object) -> int | None:
    """解析开课学期：兼容阿拉伯数字（1-8）与中文数字（一~八）及范围（如「一-七」取首个）。"""
    text = re.sub(r"\s+", "", str(value or ""))
    if not text:
        return None
    if match := re.search(r"[1-8]", text):
        return int(match.group())
    for cn, num in _TERM_CN.items():
        if cn in text:
            return num
    return None


def _requirements_from_table(table: list[list[object]]) -> dict[str, float]:
    """解析「课程平台及毕业学分要求」表，返回 {平台: 学分要求}。

    表结构（跨两行表头）：
      课程平台 | 通识教育平台 | 学科基础平台 | 专业教育平台 | 实践教育平台 | 毕业最低学分要求
      课程性质 | 通识必修 通识选修 | 学科基础 | 专业必修 专业选修 | 实践必修 |
      学分     | 40   6       | 35      | 29   28    | 21.5  | 159.5
    """
    if len(table) < 3:
        return {}
    headers = [_tight(item) for item in table[0]]
    if not any(("毕业最低" in h or "学分要求" in h) for h in headers):
        return {}
    nature_row = credit_row = None
    for row in table[1:]:
        compact = [_tight(item) for item in row]
        if compact and compact[0] == "课程性质":
            nature_row = compact
        elif compact and compact[0] == "学分":
            credit_row = compact
    if not nature_row or not credit_row:
        return {}
    result: dict[str, float] = {}
    for i in range(1, 7):
        name = nature_row[i] if i < len(nature_row) else ""
        value = credit_row[i] if i < len(credit_row) else ""
        if name and value:
            if match := re.search(r"\d+(?:\.\d+)?", value):
                result[name] = float(match.group())
    # 毕业总学分（最后一列）
    if len(credit_row) > 7 and credit_row[7]:
        if match := re.search(r"\d+(?:\.\d+)?", credit_row[7]):
            result["毕业总学分"] = float(match.group())
    return result


def _courses_from_table(table: list[list[object]], college: str, major: str, last_category: str = "未分类") -> tuple[list[dict], str]:
    """解析一张课程表。返回 (课程列表, 更新后的 last_category)。

    处理合并单元格：同一「课程性质」段内，若学期/备注只出现在某一行（合并单元格），
    则向前+向后填充到该段所有行。
    """
    if len(table) < 2:
        return [], last_category
    headers = [_tight(item) for item in table[0]]
    name_i = _index(headers, ("课程名称", "课程名"))
    credit_i = _index(headers, ("学分",))
    category_i = _index(headers, ("课程性质", "性质"))
    if category_i is None:
        category_i = _index(headers, ("课程类别", "类别"))
    term_i = _index(headers, ("开课学期", "修读学期", "学期"))
    note_i = _index(headers, ("备注",))
    if name_i is None or credit_i is None:
        return [], last_category

    # 第一遍：读取原始行
    raw: list[dict] = []
    cur_category = last_category
    for row in table[1:]:
        if len(row) <= max(name_i, credit_i):
            continue
        name = _tight(row[name_i])
        credit = _tight(row[credit_i])
        try:
            credits = float(re.search(r"\d+(?:\.\d+)?", credit).group())
        except AttributeError:
            continue
        if not name:
            continue
        if category_i is not None and len(row) > category_i:
            if rc := _tight(row[category_i]):
                cur_category = rc
        term = _to_term(row[term_i]) if term_i is not None and len(row) > term_i else None
        note = _tight(row[note_i]) if note_i is not None and len(row) > note_i else ""
        raw.append({"name": name, "credits": credits, "category": cur_category, "term": term, "note": note})

    # 第二遍：按「同一 category 连续段」做合并单元格填充（学期 + 备注）
    _fill_merged(raw)

    result = []
    for item in raw:
        category = item["category"]
        term = item["term"]
        if term is None:
            continue  # 无学期信息的行无法排入规划，跳过
        platform = next((p for p in ("通识必修", "通识选修", "学科基础", "专业必修", "专业选修", "实践必修", "实践教学") if p in category), category)
        result.append({
            "name": item["name"], "credits": item["credits"],
            "platform": "实践必修" if platform == "实践教学" else platform,
            "category": category, "semester": term,
            "required": "选修" not in category,
            "note": item["note"], "college": college, "major": major,
        })
    return result, (raw[-1]["category"] if raw else last_category)


def _fill_merged(rows: list[dict]) -> None:
    """段内（同一 category）对空的 term/note 用同段非空值填充（修复合并单元格）。

    仅在整段只有一个唯一非空值时才填充，避免把不同学期的课错误统一。
    """
    if not rows:
        return
    categories = [r["category"] for r in rows]
    i = 0
    while i < len(rows):
        j = i
        while j < len(rows) and categories[j] == categories[i]:
            j += 1
        segment = rows[i:j]
        # term：段内唯一非空值才回填
        terms = {r["term"] for r in segment if r["term"] is not None}
        if len(terms) == 1:
            only = next(iter(terms))
            for r in segment:
                if r["term"] is None:
                    r["term"] = only
        # note：段内唯一非空值才回填（如「必选」覆盖整个模块块）
        notes = {r["note"] for r in segment if r["note"]}
        if len(notes) == 1:
            only_note = next(iter(notes))
            for r in segment:
                if not r["note"]:
                    r["note"] = only_note
        i = j


# 只有疑似含课程表/学分表的页面才做昂贵的表格抽取（page.extract_tables 很慢）
_TABLE_HINT = ("课程名称", "开课学期", "修读学期", "课程性质", "课程平台", "课程类别", "毕业最低")


def parse_pdf(pdf_bytes: bytes, filename: str, progress=None) -> list[dict]:
    """Extract a best-effort catalog, keeping all results local and cacheable.

    progress(done, total) 可选回调，用于前端展示进度、避免用户误以为卡死。
    """
    grouped: OrderedDict[tuple[str, str], dict] = OrderedDict()
    last_categories: dict[tuple[str, str], str] = {}
    college = major = ""
    full_pages: list[str] = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as document:
        total = len(document.pages)
        for index, page in enumerate(document.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                full_pages.append(text.strip())
            college, major = _context(text, college, major)
            if college and major:
                key = (college, major)
                item = grouped.setdefault(key, {"college": college, "major": major, "credit_requirements": {}, "courses": [], "raw_text": [], "source_file": filename})
                # 累积该专业的完整原文（培养目标、要求、学分、课程表等），供问答使用
                if text.strip():
                    item["raw_text"].append(text.strip())
                # 仅对疑似表格页抽取表格（绝大多数叙述页跳过，显著提速）
                if any(hint in text for hint in _TABLE_HINT):
                    for table in page.extract_tables():
                        requirements = _requirements_from_table(table)
                        if requirements:
                            item["credit_requirements"].update(requirements)
                        courses, last_category = _courses_from_table(table, college, major, last_categories.get(key, "未分类"))
                        if courses:
                            item["courses"].extend(courses)
                            last_categories[key] = last_category
            if progress:
                progress(index, total)
    # 写全文旁车缓存：供 RAG 建索引复用，避免再次读取整个 PDF（省一遍全页解析）
    try:
        fulltext_path(pdf_bytes).write_text("\n".join(full_pages), encoding="utf-8")
    except OSError:
        pass
    for item in grouped.values():
        unique = {(course["name"], course["semester"], course["credits"]): course for course in item["courses"]}
        item["courses"] = list(unique.values())
        item["raw_text"] = "\n".join(item["raw_text"])
    return [item for item in grouped.values() if item["courses"]]


def cache_path(pdf_bytes: bytes) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"catalog-{hashlib.sha256(pdf_bytes).hexdigest()[:16]}.json"


def fulltext_path(pdf_bytes: bytes) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"fulltext-{hashlib.sha256(pdf_bytes).hexdigest()[:16]}.txt"


def load_or_parse(pdf_bytes: bytes, filename: str, force: bool = False, progress=None) -> tuple[list[dict], bool]:
    path = cache_path(pdf_bytes)
    if path.exists() and not force:
        return json.loads(path.read_text(encoding="utf-8")), True
    catalog = parse_pdf(pdf_bytes, filename, progress=progress)
    if not catalog:
        raise ValueError("未识别到可用课程表。请确认 PDF 含有文字层和课程设置表。")
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    return catalog, False


def load_catalog_by_cache(cache_file: str) -> list[dict]:
    """按缓存文件名加载已解析的课程目录（用于工作区切换）。"""
    path = CACHE_DIR / cache_file
    if not path.exists():
        raise FileNotFoundError(f"缓存文件不存在：{cache_file}")
    return json.loads(path.read_text(encoding="utf-8"))

