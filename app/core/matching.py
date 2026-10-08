from __future__ import annotations

import unicodedata
import re
from difflib import SequenceMatcher


def normalize_name(name: str) -> str:
    """归一化课程名：全角转半角（含罗马数字、括号）、去空白、小写。

    使「大学英语I（一）」与「大学英语Ⅰ(一)」这类差异归一为同一 key。
    """
    text = unicodedata.normalize("NFKC", str(name or ""))
    text = re.sub(r"\s+", "", text)
    return text.lower()


def build_lookup(courses: list[dict]) -> dict[str, str]:
    """返回「归一化课程名 -> 原始课程名」映射，用于成绩单课程归类。"""
    return {normalize_name(course["name"]): course["name"] for course in courses}


def match_course(name: str, lookup: dict[str, str], threshold: float = 0.6) -> str | None:
    """将成绩单里的课程名匹配到培养方案课程表的原始课程名。

    先精确匹配（归一化后），失败则按相似度取最高分，超过阈值才接受。
    返回 None 表示无法可靠归类（学分将不计入平台，并提示用户）。
    """
    key = normalize_name(name)
    if key in lookup:
        return lookup[key]
    best, best_ratio = None, 0.0
    for normalized, original in lookup.items():
        ratio = SequenceMatcher(None, key, normalized).ratio()
        if ratio > best_ratio:
            best, best_ratio = original, ratio
    return best if best_ratio >= threshold else None
