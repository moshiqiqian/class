from datetime import date

from app.core.calc import infer_semester, missing_semesters, semester_message


def test_2023_enrollment_is_semester_seven_in_october_2026():
    assert infer_semester(2023, 9, date(2026, 10, 8)) == 7


def test_spring_semester_inference():
    # 2023.9 入学，2026.3 为第 6 学期（大三下学期）
    assert infer_semester(2023, 9, date(2026, 3, 1)) == 6


def test_missing_semesters_excludes_current_term():
    assert missing_semesters(5, {1, 3, 4}) == [2]


def test_out_of_standard_duration_message():
    assert semester_message(9) is not None

