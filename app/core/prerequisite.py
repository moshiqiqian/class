from __future__ import annotations

from pathlib import Path

import yaml

PREREQ_DIR = Path("data/prerequisites")


def advisory_notes(major: str, completed_courses: list[str], directory: Path = PREREQ_DIR) -> dict[str, str]:
    """返回「课程 -> 建议先修提示」，仅作参考，不阻断选课。"""
    path = directory / f"{major}.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    completed = set(completed_courses)
    result: dict[str, str] = {}
    for item in data.get(major, []):
        missing = [course for course in item.get("prerequisites", []) if course not in completed]
        if missing:
            result[item["course"]] = f"选课建议：建议先完成 {'、'.join(missing)}（非强制约束）"
    return result
