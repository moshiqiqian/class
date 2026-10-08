from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

PLATFORMS = ("通识必修", "通识选修", "学科基础", "专业必修", "专业选修", "实践必修")

# 入学年份的合理范围：基本学制 4 年，最长 6 年，留 1 年余量
_MAX_STUDY_YEARS = 7


def infer_semester(enrollment_year: int, month: int, today: date | None = None) -> int:
    """按中国大学学年推算当前学期（1-8）。

    规则：一学年 = 两学期。秋季学期（9月 - 次年1月）为每学年的第 1 学期，
    春季学期（2月 - 7月）为第 2 学期。以 9 月入学为起点。
    """
    current = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    y, m = current.year, current.month
    if m >= 9:  # 9-12 月：本自然年入学的秋季学期
        academic_year = y - enrollment_year
        term = academic_year * 2 + 1
    elif m == 1:  # 1 月：上一自然年开始的秋季学期（跨年）
        academic_year = (y - 1) - enrollment_year
        term = academic_year * 2 + 1
    else:  # 2-7 月：上一自然年开始的春季学期
        academic_year = (y - 1) - enrollment_year
        term = academic_year * 2 + 2
    # 钳制到 1-12（防止入学年份异常导致溢出）
    return max(1, min(12, term))


def semester_message(semester: int) -> str | None:
    if semester < 1:
        return "入学时间不合理，请检查入学年份。"
    if semester > 8:
        return "已超出基本学制 4 年（可延长至 6 年），请按实际情况调整。"
    return None


def enrollment_year_bounds(today: date | None = None) -> tuple[int, int]:
    """返回入学年份的合理输入范围 (最小, 最大)，防止越界溢出。"""
    current = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    return current.year - _MAX_STUDY_YEARS, current.year + 1


def empty_credits() -> dict[str, float]:
    return {platform: 0.0 for platform in PLATFORMS}


def calculate_gap(requirements: dict[str, float], earned: dict[str, float]) -> dict[str, dict[str, float]]:
    result = {}
    for platform, required in requirements.items():
        if platform == "毕业总学分":
            continue
        completed = float(earned.get(platform, 0))
        result[platform] = {"required": float(required), "earned": completed, "missing": max(0.0, float(required) - completed)}
    return result


def missing_semesters(current_semester: int, received_semesters: set[int | None]) -> list[int]:
    """返回期望已完成但缺失的学期（不含在读学期）。"""
    expected = set(range(1, max(1, current_semester)))
    return sorted(expected - {item for item in received_semesters if item is not None})


def grade_point(score: object) -> float:
    """4 分制绩点换算：90+→4.0，80-89→3.0，70-79→2.0，60-69→1.0，<60→0。"""
    try:
        value = float(score)
    except (TypeError, ValueError):
        return 0.0
    if value >= 90:
        return 4.0
    if value >= 80:
        return 3.0
    if value >= 70:
        return 2.0
    if value >= 60:
        return 1.0
    return 0.0


def calculate_gpa(records: list[dict]) -> dict:
    """按 4 分制加权平均计算绩点：GPA = Σ(学分 × 绩点) / Σ学分。"""
    total_credits = 0.0
    total_points = 0.0
    detail = []
    for record in records:
        if not record.get("passed"):
            continue  # 不及格课程不计入绩点（学分通常不获得）
        credits = float(record.get("credits", 0))
        point = grade_point(record.get("score"))
        total_credits += credits
        total_points += credits * point
        detail.append({"course": record["course"], "score": record.get("score"), "credits": credits, "grade_point": point})
    gpa = round(total_points / total_credits, 2) if total_credits else 0.0
    return {"gpa": gpa, "total_credits": total_credits, "detail": detail}


def summarize_transcripts(records: list[dict], course_platforms: dict[str, str], statuses: dict[str, str], match=None) -> tuple[dict[str, float], list[str], list[dict]]:
    """将成绩单记录归集到平台。返回 (各平台学分, 已通过课程列表, 不及格课程列表)。"""
    credits, completed, failed = empty_credits(), [], []
    for record in records:
        name = record["course"]
        status = statuses.get(name, "")
        passed = bool(record.get("passed")) or status == "已通过补考"
        if not passed:
            failed.append({**record, "status": status or "待确认"})
            continue
        platform = course_platforms.get(match(name) if match else name)
        if platform:
            credits[platform] += float(record["credits"])
        completed.append(name)
    return credits, list(dict.fromkeys(completed)), failed


def _remaining_courses(curriculum: dict, current_semester: int, completed: set[str]) -> list[dict]:
    """返回尚未修读的课程（学期 > 当前学期 且不在已修列表）。"""
    result = []
    for course in curriculum.get("courses", []):
        term = int(course.get("semester", 0))
        if term <= current_semester or course["name"] in completed:
            continue
        result.append(course)
    return result


def build_plan(profile: dict, curriculum: dict, advice: dict[str, str] | None = None, mode: str = "balanced") -> dict:
    """生成选课规划。mode 支持：
    - balanced：平均分配（每学期负担均衡）
    - early：尽可能大四前修完（提前集中）
    - original：按培养方案原始开课学期
    """
    current = int(profile["current_semester"])
    advice = advice or {}
    completed = set(profile.get("completed_courses", []))
    remaining = _remaining_courses(curriculum, current, completed)

    # 缺口（确定性计算，LLM 不改数值）
    gap = calculate_gap(curriculum.get("credit_requirements", {}), profile.get("completed_credits", {}))
    total_required = curriculum.get("credit_requirements", {}).get("毕业总学分")
    total_earned = sum(float(v) for v in profile.get("completed_credits", {}).values())

    # 按模式排布课程到学期
    timeline = _schedule(remaining, current, mode)

    # 重修安排
    next_term = min(current + 1, 8)
    retakes = [{"课程名称": item["course"], "学分": item.get("credits", 0), "状态": item.get("status"), "建议学期": f"第 {next_term} 学期"} for item in profile.get("failed_courses", []) if item.get("status") != "已通过补考"]

    warnings = [f"缺少第 {', '.join(map(str, profile['missing_semesters']))} 学期成绩单，学分缺口可能偏大。"] if profile.get("missing_semesters") else []

    return {
        "mode": mode,
        "gap": gap,
        "timeline": timeline,
        "next_courses": timeline.get(next_term, []),
        "retakes": retakes,
        "warnings": warnings,
        "total_required": total_required,
        "total_earned": total_earned,
    }


def _schedule(courses: list[dict], current_semester: int, mode: str) -> dict[int, list[dict]]:
    """把剩余课程排布到 剩余学期（current+1 .. 8）。"""
    if not courses:
        return {}
    if mode == "original":
        # 按原始开课学期，只保留 > current 的
        timeline: dict[int, list[dict]] = {}
        for course in courses:
            term = int(course.get("semester", 0))
            timeline.setdefault(term, []).append(_course_row(course))
        return dict(sorted(timeline.items()))

    # balanced / early：重新排布
    remaining_terms = list(range(current_semester + 1, 9))
    if mode == "early":
        # 大四前修完：目标在第 7 学期结束前修完，第 8 学期尽量留空（只毕业环节）
        remaining_terms = list(range(current_semester + 1, 8))

    # 必修优先、按原始学期排序
    required = [c for c in courses if c.get("required", True)]
    electives = [c for c in courses if not c.get("required", True)]
    ordered = sorted(required, key=lambda c: int(c.get("semester", 0))) + sorted(electives, key=lambda c: int(c.get("semester", 0)))

    # 平均分配到各剩余学期
    n_terms = len(remaining_terms)
    timeline = {t: [] for t in remaining_terms}
    for index, course in enumerate(ordered):
        term = remaining_terms[index % n_terms]
        timeline[term].append(_course_row(course))
    return {t: v for t, v in timeline.items() if v}


def _course_row(course: dict) -> dict:
    return {
        "课程名称": course["name"],
        "类别": course.get("category", course.get("platform", "")),
        "学分": float(course["credits"]),
        "原始学期": f"第 {course.get('semester', 0)} 学期",
        "性质": "必修" if course.get("required", True) else "选修",
    }
