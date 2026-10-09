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


def semester_label(semester: int) -> str:
    """把绝对学期号（1-8）转为「第 N 学年第 1/2 学期」。

    第1学期=第1学年第1学期，第2学期=第1学年第2学期，第3学期=第2学年第1学期……
    """
    year = (semester + 1) // 2
    term = 1 if semester % 2 == 1 else 2
    return f"第 {year} 学年第 {term} 学期"


def missing_semester_labels(current_semester: int, received_semesters: set[int | None]) -> list[str]:
    """返回缺失学期的可读标签（如「第 2 学年第 1 学期」）。"""
    return [semester_label(s) for s in missing_semesters(current_semester, received_semesters)]


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
    """按 4 分制加权平均计算绩点：GPA = Σ(学分 × 绩点) / Σ学分。

    优先使用记录自带的 gpa 字段（成绩单「绩点」列），否则用成绩换算。
    """
    total_credits = 0.0
    total_points = 0.0
    detail = []
    for record in records:
        if not record.get("passed"):
            continue  # 不及格课程不计入绩点
        credits = float(record.get("credits", 0))
        point = record.get("gpa")
        if point is None:
            point = grade_point(record.get("score"))
        total_credits += credits
        total_points += credits * float(point)
        detail.append({"course": record["course"], "score": record.get("score"), "credits": credits, "grade_point": float(point), "semester": record.get("semester")})
    gpa = round(total_points / total_credits, 2) if total_credits else 0.0
    return {"gpa": gpa, "total_credits": total_credits, "detail": detail}


def gpa_by_semester(records: list[dict]) -> dict[int, dict]:
    """按学期分组计算绩点。返回 {学期: {gpa, total_credits, courses}}。"""
    by_sem: dict[int, list[dict]] = {}
    for record in records:
        sem = record.get("semester")
        if sem is None:
            continue
        by_sem.setdefault(int(sem), []).append(record)
    result = {}
    for sem, recs in sorted(by_sem.items()):
        total_credits = 0.0
        total_points = 0.0
        passed_recs = [r for r in recs if r.get("passed")]
        for r in passed_recs:
            credits = float(r.get("credits", 0))
            point = r.get("gpa")
            if point is None:
                point = grade_point(r.get("score"))
            total_credits += credits
            total_points += credits * float(point)
        gpa = round(total_points / total_credits, 2) if total_credits else 0.0
        result[sem] = {
            "gpa": gpa,
            "total_credits": round(total_credits, 1),
            "courses": recs,
        }
    return result


def summarize_transcripts(records: list[dict], course_platforms: dict[str, str], statuses: dict[str, str], match=None) -> tuple[dict[str, float], list[str], list[dict]]:
    """将成绩单记录归集到平台。返回 (各平台学分, 已通过课程列表, 不及格课程列表)。

    优先使用记录自带的 platform 字段（成绩单「课程性质」列），
    否则用 course_platforms 匹配（match 为课程名匹配器）。
    """
    credits, completed, failed = empty_credits(), [], []
    for record in records:
        name = record["course"]
        status = statuses.get(name, "")
        passed = bool(record.get("passed")) or status == "已通过补考"
        if not passed:
            failed.append({**record, "status": status or "待确认"})
            continue
        platform = record.get("platform")
        if not platform:
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

    # 已修课程回顾（之前学期）
    completed_courses = _completed_courses(curriculum, completed)

    # 按模式排布剩余课程
    timeline = _schedule(remaining, current, mode)

    # 重修安排
    next_term = min(current + 1, 8)
    retakes = [{"课程名称": item["course"], "学分": item.get("credits", 0), "状态": item.get("status"), "建议学期": f"第 {next_term} 学期"} for item in profile.get("failed_courses", []) if item.get("status") != "已通过补考"]

    warnings = [f"缺少第 {', '.join(map(str, profile['missing_semesters']))} 学期成绩单，学分缺口可能偏大。"] if profile.get("missing_semesters") else []

    # 历史学期（已修课程按学期分组，用于「规划/回顾之前学期」）
    history: dict[int, list[dict]] = {}
    for row in completed_courses:
        term = int(row.get("原始学期", "第 0").replace("第 ", "").replace(" 学期", "") or 0)
        history.setdefault(term, []).append(row)

    return {
        "mode": mode,
        "gap": gap,
        "timeline": timeline,
        "next_courses": timeline.get(next_term, []),
        "retakes": retakes,
        "warnings": warnings,
        "total_required": total_required,
        "total_earned": total_earned,
        "completed_courses": completed_courses,   # 已修课程（用于回顾）
        "history": dict(sorted(history.items())), # 历史学期分组
        "current_semester": current,
    }


def _completed_courses(curriculum: dict, completed: set[str]) -> list[dict]:
    """返回已修课程列表（按学期排序），用于回顾之前的学期。"""
    result = []
    for course in curriculum.get("courses", []):
        if course["name"] in completed:
            result.append(_course_row(course))
    return sorted(result, key=lambda c: int(c.get("semester", 0)) if str(c.get("semester", 0)).isdigit() else 0)


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

    # 无剩余学期（已到第 8 学期及以后）：按课程原始学期归位，避免除零
    if not remaining_terms:
        timeline: dict[int, list[dict]] = {}
        for course in ordered:
            term = int(course.get("semester", 8))
            timeline.setdefault(term, []).append(_course_row(course))
        return dict(sorted(timeline.items()))

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


def build_semester_plan(profile: dict, curriculum: dict, target_semester: int) -> dict:
    """为指定学期（含历史学期）重新规划课程。

    列出该学期培养方案应修的课程，并对比学生实际修读情况，
    标记「已修 / 建议补修 / 建议修读」。
    """
    completed = set(profile.get("completed_courses", []))
    courses = [c for c in curriculum.get("courses", []) if int(c.get("semester", 0)) == int(target_semester)]

    rows = []
    for c in courses:
        row = _course_row(c)
        if c["name"] in completed:
            row["状态"] = "✅ 已修"
        elif int(c.get("semester", 0)) < profile.get("current_semester", 1):
            row["状态"] = "⚠️ 未修（建议补修）"
        else:
            row["状态"] = "建议修读"
        rows.append(row)

    # 必修在前
    rows.sort(key=lambda r: (r["性质"] != "必修", r["课程名称"]))
    total = sum(r["学分"] for r in rows)
    required_total = sum(r["学分"] for r in rows if r["性质"] == "必修")

    return {
        "target_semester": int(target_semester),
        "courses": rows,
        "total_credits": round(total, 1),
        "required_credits": round(required_total, 1),
        "course_count": len(rows),
        "gap": calculate_gap(curriculum.get("credit_requirements", {}), profile.get("completed_credits", {})),
        "total_required": curriculum.get("credit_requirements", {}).get("毕业总学分"),
        "total_earned": sum(float(v) for v in profile.get("completed_credits", {}).values()),
        "warnings": [],
    }


# 每学期学分上限（总学分 / 选修学分）
MAX_CREDITS_PER_SEMESTER = 26.0
MAX_ELECTIVE_CREDITS_PER_SEMESTER = 8.0


def build_career_plan(profile: dict, curriculum: dict) -> dict:
    """全大学生涯整体规划（第 1 至第 8 学期全覆盖）。

    规则：
    - 忽略学生当前进度，当作大一新生，完整规划培养方案全部课程。
    - 必修课：全部必须修读，按培养方案原始开课学期安排。
    - 选修课：只需修够各平台要求的选修学分，不必全修。
    - 每学期有学分上限（总学分、选修学分），超出则顺延。
    """
    requirements = curriculum.get("credit_requirements", {})

    courses = curriculum.get("courses", [])
    # 生涯规划：忽略学生当前进度，当作全新生，完整规划培养方案的全部课程
    required_courses = [c for c in courses if c.get("required", True)]
    elective_courses = [c for c in courses if not c.get("required", True)]

    # 各平台选修要求（按培养方案完整要求，不扣减已修）
    elective_gap = {}
    for platform, req in requirements.items():
        if platform == "毕业总学分" or "选修" not in platform:
            continue
        elective_gap[platform] = float(req)

    timeline: dict[int, list[dict]] = {s: [] for s in range(1, 9)}
    # 每学期已用学分
    used_total = {s: 0.0 for s in range(1, 9)}
    used_elective = {s: 0.0 for s in range(1, 9)}

    # 1. 必修课：按原始学期，超出上限顺延
    for c in sorted(required_courses, key=lambda c: int(c.get("semester", 1))):
        term = max(int(c.get("semester", 1)), 1)
        # 顺延到不超上限的学期
        t = term
        while t <= 8 and used_total[t] + float(c["credits"]) > MAX_CREDITS_PER_SEMESTER:
            t += 1
        if t > 8:
            t = 8  # 兜底
        row = _course_row(c)
        row["状态"] = "建议修读"
        timeline[t].append(row)
        used_total[t] += float(c["credits"])

    # 2. 选修课：按平台缺口挑选，修够即停；受每学期选修上限约束
    for platform, gap in elective_gap.items():
        picked = 0.0
        pool = sorted([c for c in elective_courses if c.get("platform") == platform or c.get("category") == platform], key=lambda c: int(c.get("semester", 1)))
        for c in pool:
            if picked >= gap:
                break
            term = max(int(c.get("semester", 1)), 1)
            t = term
            # 找到有选修容量且总学分不超的学期
            while t <= 8 and (used_elective[t] + float(c["credits"]) > MAX_ELECTIVE_CREDITS_PER_SEMESTER
                              or used_total[t] + float(c["credits"]) > MAX_CREDITS_PER_SEMESTER):
                t += 1
            if t > 8:
                continue  # 无容量，放弃该选修（只需修够即可）
            row = _course_row(c)
            row["状态"] = "建议选修"
            timeline[t].append(row)
            used_total[t] += float(c["credits"])
            used_elective[t] += float(c["credits"])
            picked += float(c["credits"])

    # 每学期补充统计
    result_timeline = {}
    for t, rows in timeline.items():
        if not rows:
            continue
        result_timeline[t] = rows

    return {
        "mode": "career",
        "timeline": result_timeline,
        "gap": {k: {"required": v, "earned": 0, "missing": v} for k, v in requirements.items() if k != "毕业总学分"},
        "total_required": requirements.get("毕业总学分"),
        "total_earned": 0,
        "warnings": [],
        "max_credits_per_semester": MAX_CREDITS_PER_SEMESTER,
        "max_elective_per_semester": MAX_ELECTIVE_CREDITS_PER_SEMESTER,
    }
