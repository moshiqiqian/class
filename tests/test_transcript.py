import pandas as pd

from app.core.transcript import records_from_dataframe, semester_from_filename


def test_filename_semester_recognition():
    assert semester_from_filename("2025-2026-1-成绩单.xlsx") == 1


def test_record_parsing_and_pass_state():
    frame = pd.DataFrame({"课程名称": ["数据结构", "英语"], "成绩": [85, 59], "学分": [4, 3], "学期": [3, 3]})
    records = records_from_dataframe(frame)
    assert records[0]["passed"] is True
    assert records[1]["passed"] is False
    assert records[0]["semester"] == 3
