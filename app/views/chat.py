from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core.calc import build_plan, calculate_gpa, gpa_by_semester
from app.core.graph import build_graph


def _curriculum() -> dict:
    profile = st.session_state.profile
    if not profile:
        return {}
    for item in st.session_state.parsed_catalog:
        if item["college"] == profile["college"] and item["major"] == profile["major"]:
            return item
    return {}


def render() -> None:
    profile = st.session_state.profile
    if not profile:
        st.warning("学生信息缺失，请回到信息采集步骤重新填写。")
        if st.button("返回信息采集"):
            st.session_state.stage = 2
            st.rerun()
        return

    if msg := st.session_state.pop("flash", None):
        st.success(msg)

    curriculum = _curriculum()
    if not curriculum:
        st.warning("未找到对应专业的课程表，请确认工作区和专业选择。")
        if st.button("返回工作区"):
            st.session_state.stage = 1
            st.rerun()
        return

    # 兜底同步：若 session 无成绩单但工作区有，则加载（避免三步骤能看到、对话页为空）
    if not st.session_state.get("transcript_records") and st.session_state.get("current_workspace"):
        from app.core.workspace import get_workspace
        ws = get_workspace(st.session_state.current_workspace)
        if ws and ws.get("transcripts"):
            st.session_state.transcript_records = ws["transcripts"]

    # 左侧固定导航栏（不随对话滚动）
    with st.sidebar:
        _render_sidebar(profile)

    page = st.session_state.get("chat_page", "💬 对话")
    if page == "💬 对话":
        _render_chat(profile, curriculum)
    elif page == "📊 成绩信息":
        _render_grades()
    else:
        _render_edit()


def _render_sidebar(profile: dict) -> None:
    """左侧固定栏：工作区信息 + 视图切换 + 工作区管理入口。"""
    st.markdown(f"**🎓 {profile['major']}**")
    st.caption(f"{profile['college']} · 第 {profile['current_semester']} 学期")

    st.divider()
    st.markdown("**功能**")
    st.radio("功能导航", ("💬 对话", "📊 成绩信息", "⚙️ 修改信息"), key="chat_page", label_visibility="collapsed")

    st.divider()
    st.caption(f"当前工作区：{st.session_state.get('current_workspace', '—')}")
    if st.button("📈 升学管理", use_container_width=True):
        st.session_state.promote_page = True
        st.rerun()
    if st.button("🗂️ 管理工作区", use_container_width=True):
        st.session_state.manage_page = True
        st.rerun()


def _render_chat(profile: dict, curriculum: dict) -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "assistant", "content": f"你好！我是 {profile['major']} 培养方案助手。\n\n你可以问我：\n- **学分缺口**：「我还差多少学分」\n- **选课规划**：「怎么规划才能大四前修完」「下学期选什么课」\n- **培养方案问答**：「本专业毕业最低学分是多少」「核心课程有哪些」"}
        ]

    messages = st.session_state.messages

    # 渲染历史消息（向上堆叠，最新的在底部）
    for message in messages:
        _render_message(message)

    # 输入框：st.chat_input 天然固定在底部
    if question := st.chat_input("请输入你的问题…"):
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        graph = build_graph()
        with st.spinner("思考中…"):
            result = graph.invoke({"question": question, "profile": profile, "curriculum": curriculum})

        if result.get("plan_result"):
            plan = result["plan_result"]
            intent = result.get("intent", "plan")
            content = _plan_summary(plan, intent)
            st.session_state.messages.append({"role": "assistant", "content": content, "plan": plan, "intent": intent})
            with st.chat_message("assistant"):
                _render_plan(plan, intent)
        elif result.get("review_result"):
            review = result["review_result"]
            st.session_state.messages.append({"role": "assistant", "content": "结果", "review": review})
            with st.chat_message("assistant"):
                _render_review_result(review)
        else:
            answer = result.get("answer", "")
            st.session_state.messages.append({"role": "assistant", "content": answer})
            with st.chat_message("assistant"):
                st.markdown(answer)
                for citation in result.get("citations", []):
                    st.caption(f"来源：第 {citation['page']} 页")


def _render_message(message: dict) -> None:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant" and message.get("plan"):
            _render_plan(message["plan"], message.get("intent", "plan"))
        elif message["role"] == "assistant" and message.get("review"):
            _render_review_result(message["review"])
        elif message["role"] == "assistant" and message.get("missing"):
            _render_missing(message["missing"])
        else:
            st.markdown(message["content"])


def _render_review_result(review: dict) -> None:
    """按 kind 分派渲染 review_result（graduation/compare/retake/missing/review）。"""
    kind = review.get("kind")
    if kind == "graduation":
        _render_graduation(review)
    elif kind == "compare":
        _render_compare(review)
    elif kind == "retake":
        _render_retake(review)
    elif review.get("missing_semesters") is not None:
        _render_missing(review)
    else:
        _render_review(review)


def _render_graduation(result: dict) -> None:
    """毕业达标检查。"""
    st.markdown("### 🎓 毕业达标检查")
    if result.get("can_graduate"):
        st.success(result.get("conclusion", "✅ 已满足毕业要求。"))
    else:
        st.warning(result.get("conclusion", "⚠️ 尚未达标。"))

    col1, col2 = st.columns(2)
    with col1:
        st.metric("已获学分", result.get("total_earned", 0))
    with col2:
        st.metric("毕业需修", result.get("total_required", "—"))

    # 各平台达标情况
    platforms = result.get("platforms", {})
    if platforms:
        st.markdown("**各平台达标情况**")
        rows = [{"平台": k, "要求": v["required"], "已获": v["earned"], "缺口": v["missing"],
                 "状态": "✅ 达标" if v["ok"] else "⚠️ 未达标"} for k, v in platforms.items()]
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

    missing = result.get("missing_required", [])
    if missing:
        st.markdown(f"**未修必修课（{len(missing)} 门）**")
        st.dataframe(pd.DataFrame(missing), use_container_width=True)

    if result.get("missing_semesters"):
        st.caption("注：缺少部分学期成绩单，达标结论可能不完整。")


def _render_compare(result: dict) -> None:
    """方案并排对比。"""
    st.markdown("### 📊 方案对比")
    plans = result.get("plans", [])
    if not plans:
        st.info("暂无可对比的方案。")
        return
    for p in plans:
        st.markdown(f"- **{p['name']}**：{p.get('desc', '')}")
    st.markdown("**各学期学分对比**")
    st.dataframe(pd.DataFrame(result.get("table", [])), use_container_width=True)
    st.caption("差异体现在选修课的选择策略上；必修课固定在开课学期。")


def _render_retake(result: dict) -> None:
    """重修/补考规划。"""
    st.markdown("### 🔁 重修 / 补考规划")
    if result.get("suggestion"):
        st.markdown(result["suggestion"])
    retakes = result.get("retakes", [])
    if retakes:
        st.dataframe(pd.DataFrame(retakes), use_container_width=True)
    failed = result.get("failed", [])
    if failed and not retakes:
        st.dataframe(pd.DataFrame([{"课程名称": f.get("course"), "学分": f.get("credits"), "状态": f.get("status")} for f in failed]), use_container_width=True)
    if not retakes and not failed:
        st.success("你目前没有需要重修或补考的课程。")


def _render_missing(missing: dict) -> None:
    """展示缺失哪些学期的成绩单。"""
    from app.core.calc import semester_label
    current = missing.get("current_semester", 1)
    missing_list = missing.get("missing_semesters", [])
    received = missing.get("received", set())

    st.markdown(f"**当前第 {current} 学期**")
    if missing_list:
        st.warning("检测到缺少以下学期的成绩单：\n\n" + "\n".join(f"- {label}" for label in missing_list))
    else:
        st.success("成绩单学期完整，无缺失。")

    # 已覆盖的学期
    if received:
        covered = sorted([s for s in received if s is not None])
        st.markdown("已覆盖的学期：" + "、".join(semester_label(s) for s in covered))


def _plan_summary(plan: dict, intent: str) -> str:
    total_required = plan.get("total_required")
    total_earned = plan.get("total_earned")
    if total_required:
        return f"已获 {total_earned} / 需修 {total_required} 学分。"
    return ""


def _render_plan(plan: dict, intent: str) -> None:
    for warning in plan.get("warnings", []):
        st.warning(warning)

    # 学分缺口（gap 意图）：只回答缺口，不展示规划建议
    if intent == "gap":
        st.markdown("**学分缺口统计**")
        gap_rows = []
        for name, row in plan.get("gap", {}).items():
            gap_rows.append({"平台": name, "要求": row["required"], "已获": row["earned"], "缺口": row["missing"]})
        st.dataframe(pd.DataFrame(gap_rows), use_container_width=True)
        total_missing = sum(r["缺口"] for r in gap_rows)
        st.caption(f"总计缺口：**{total_missing:.1f}** 学分")
        return

    # LLM 生成的实质建议（最上方展示）
    if plan.get("suggestion"):
        st.markdown(plan["suggestion"])

    # 指定学期规划（semester_plan）
    if "target_semester" in plan:
        target = plan["target_semester"]
        st.markdown(f"**第 {target} 学期规划**（{plan['course_count']} 门课，共 {plan['total_credits']} 学分）")
        if plan["courses"]:
            st.dataframe(pd.DataFrame(plan["courses"]), use_container_width=True)
        else:
            st.info(f"第 {target} 学期培养方案无课程安排。")
        return

    # 全大学生涯规划（career）：详细叙述 + 逐学期说明
    if plan.get("mode") == "career":
        _render_career_plan(plan)
        return

    # 普通规划：学分缺口
    st.markdown("**学分缺口统计**")
    gap_rows = []
    for name, row in plan["gap"].items():
        gap_rows.append({"平台": name, "要求": row["required"], "已获": row["earned"], "缺口": row["missing"]})
    st.dataframe(pd.DataFrame(gap_rows), use_container_width=True)

    # 下学期清单
    st.markdown("**下学期选课清单**")
    if plan.get("next_courses"):
        st.dataframe(pd.DataFrame(plan["next_courses"]), use_container_width=True)
    elif "next_courses" in plan:
        st.info("下学期暂无必修课安排。")

    # 剩余学期规划
    st.markdown("**剩余学期规划**")
    if plan.get("timeline"):
        for term, courses in plan["timeline"].items():
            total = sum(c["学分"] for c in courses)
            elective = sum(c["学分"] for c in courses if c.get("性质") == "选修")
            st.markdown(f"*第 {term} 学期*（{len(courses)} 门课，共 {total} 学分，其中选修 {elective} 学分）")
            st.dataframe(pd.DataFrame(courses), use_container_width=True)
    else:
        st.info("已无剩余课程（已修完培养方案全部课程）。")

    if plan.get("retakes"):
        st.markdown("**补考 / 重修安排**")
        st.dataframe(pd.DataFrame(plan["retakes"]), use_container_width=True)


def _render_career_plan(plan: dict) -> None:
    """全大学生涯规划：默认给一套方案 + 当前学期选课 + 逐学期模板 + 可定制提示。"""
    st.markdown("### 📋 大学四年整体规划")

    plans = plan.get("plans")
    if not plans:
        timeline = plan.get("timeline", {})
        plans = [{"name": "整体规划", "desc": "", "timeline": timeline, "school": {}, "loads": {}, "current_courses": []}]

    current = plan.get("current_semester", 1)
    total_required = plan.get("total_required")
    p = plans[0]  # 默认只展示第一套

    st.markdown(
        f"- **当前学期**：第 {current} 学期\n"
        f"- **毕业需修**：{total_required if total_required else '—'} 学分\n"
        f"- **说明**：所有**必修课由学校安排**（无需自选）；你只需**自选通识选修课和专业选修课**，系统按平台**缺口**给出建议；课程按**开课学期**安排。"
    )
    st.divider()

    # 当前学期具体选课（重点）
    cur_courses = p.get("current_courses", [])
    if cur_courses:
        cur_credits = sum(c["学分"] for c in cur_courses)
        st.success(f"**本学期（第 {current} 学期）建议选课 {len(cur_courses)} 门、共 {cur_credits:.1f} 学分：**")
        st.dataframe(pd.DataFrame(cur_courses), use_container_width=True)
    else:
        st.info(f"第 {current} 学期无需你自行选课（或已修完该学期课程）。")

    # 各学期负担（覆盖 1-8 学期）
    loads = p.get("loads", {})
    if loads:
        st.markdown("**各学期负担**")
        load_df = pd.DataFrame([
            {"学期": f"第{t}学期（大{['一','二','三','四'][(t-1)//2]}{'上' if t%2==1 else '下'}）",
             "需自选学分": v.get("需自选", 0), "学校安排学分": v.get("学校安排", 0), "合计": v.get("合计", 0)}
            for t, v in sorted(loads.items())
        ])
        st.dataframe(load_df, use_container_width=True)

    # 选修课缺口与建议
    eg = p.get("elective_gap", {})
    if eg:
        st.markdown("**选修课还需修读**：" + "、".join(f"{k} {v} 学分" for k, v in eg.items()))
        st.caption("建议把选修分散到剩余学期，每学期 1~2 门，避免集中在大四。")

    # 逐学期模板（始终展示 1-8 学期）
    st.markdown("**逐学期选课模板**")
    for term in range(1, 9):
        rows = p["timeline"].get(term, [])
        school = p.get("school", {}).get(term, [])
        year, half = (term + 1) // 2, "上" if term % 2 == 1 else "下"
        total = sum(c["学分"] for c in rows)
        st.markdown(f"**第 {term} 学期（大{['一','二','三','四'][year-1]}{half}）** · 需自选 {len(rows)} 门 / {total:.1f} 学分")
        if rows:
            st.markdown("　需自选（选修）：" + "、".join(f"{c['课程名称']}({c['学分']})" for c in rows))
        else:
            st.markdown("　需自选（选修）：无")
        if school:
            st.markdown("　学校安排：" + "、".join(f"{c['课程名称']}({c['学分']})" for c in school))
        else:
            st.markdown("　学校安排：无")

    # 导出 Excel
    try:
        st.download_button("⬇️ 下载选课表（Excel）", data=_plan_to_excel(p),
                           file_name="选课规划.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True)
    except Exception:
        pass

    st.info("💡 以上是为你生成的默认方案。**如需其他定制**（如「提前集中修完」「某学期减负」「对比不同方案」），"
            "可以直接告诉我，我会针对性重新生成。")


def _plan_to_excel(p: dict) -> bytes:
    """把选课方案导出为排版清晰的 Excel（按学期分块，含需选/学校安排）。"""
    import io
    rows = []
    for term in range(1, 9):
        timeline = p.get("timeline", {}).get(term, [])
        school = p.get("school", {}).get(term, [])
        if not timeline and not school:
            continue
        year, half = (term + 1) // 2, "上" if term % 2 == 1 else "下"
        label = f"第{term}学期（大{['一','二','三','四'][year-1]}{half}）"
        for c in timeline:
            rows.append({"学期": label, "类型": "需自选", "课程名称": c.get("课程名称", ""),
                         "类别": c.get("类别", ""), "学分": c.get("学分", "")})
        for c in school:
            rows.append({"学期": label, "类型": "学校安排", "课程名称": c.get("课程名称", ""),
                         "类别": c.get("类别", ""), "学分": c.get("学分", "")})
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="选课规划")
        ws = writer.sheets["选课规划"]
        # 列宽
        for col, width in zip("ABCDE", (18, 10, 28, 14, 8)):
            ws.column_dimensions[col].width = width
    return buf.getvalue()


def _render_review(review: dict) -> None:
    """总览：绩点 + 学分进度 + 各学期绩点一览（详细课程请去「成绩信息」页查看）。"""
    if review.get("gpa"):
        col1, col2 = st.columns(2)
        with col1:
            st.metric("绩点（GPA）", review["gpa"]["gpa"])
        with col2:
            st.metric("已获学分", review["gpa"]["total_credits"])

    if review.get("total_required"):
        st.progress(min(1.0, review["total_earned"] / review["total_required"]))
        st.caption(f"学分进度：{review['total_earned']} / {review['total_required']}")

    # 各学期绩点总览（不展开每学期的课程明细）
    records = st.session_state.get("transcript_records", [])
    if records:
        by_sem = gpa_by_semester(records)
        if by_sem:
            st.markdown("**各学期绩点一览**")
            overview = pd.DataFrame([
                {"学期": f"第 {s} 学期", "平均绩点": v["gpa"], "学分": v["total_credits"], "课程数": len(v["courses"])}
                for s, v in by_sem.items()
            ])
            st.dataframe(overview, use_container_width=True)
            st.caption("查看某学期详细课程，请切换到「📊 成绩信息」页。")
    else:
        st.info("暂无成绩单数据。请先在「过往学分」步骤上传成绩单。")


def _render_grades() -> None:
    records = st.session_state.get("transcript_records", [])
    if not records:
        st.info("暂无成绩单数据。请先在「过往学分」步骤上传成绩单。")
        return

    gpa_info = calculate_gpa(records)

    # 顶部总览（更丰富）
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("总绩点（GPA）", gpa_info["gpa"])
    with col2:
        st.metric("已获学分", gpa_info["total_credits"])
    with col3:
        passed = sum(1 for r in records if r.get("passed"))
        st.metric("已修课程数", f"{passed} / {len(records)}")

    st.divider()

    # 按学期分组
    by_sem = gpa_by_semester(records)
    if not by_sem:
        st.info("成绩单中未识别到学期信息，无法按学期分组展示。")
        return

    # 8 学期下拉切换，不堆在一起
    from app.core.calc import semester_label
    available_sems = sorted(by_sem.keys())
    options = {f"第 {s} 学期（{semester_label(s)}）": s for s in available_sems}
    selected_label = st.selectbox("选择学期查看", list(options.keys()), key="grade_sem_select")
    selected_sem = options[selected_label]

    info = by_sem[selected_sem]
    st.markdown(f"**第 {selected_sem} 学期** · 平均绩点 **{info['gpa']}** · 学分 **{info['total_credits']}**")
    courses_df = pd.DataFrame([{
        "课程名称": r["course"],
        "成绩": r["score"],
        "学分": r["credits"],
        "绩点": r.get("gpa") if r.get("gpa") is not None else r.get("grade_point"),
        "是否及格": "是" if r.get("passed") else "否",
    } for r in info["courses"]])
    st.dataframe(courses_df, use_container_width=True)

    # 各学期绩点总览（小表格，快速对比）
    st.divider()
    st.markdown("**各学期绩点一览**")
    overview = pd.DataFrame([
        {"学期": f"第 {s} 学期", "平均绩点": v["gpa"], "学分": v["total_credits"], "课程数": len(v["courses"])}
        for s, v in by_sem.items()
    ])
    st.dataframe(overview, use_container_width=True)

    # 不及格课程
    failed = [r for r in records if not r.get("passed")]
    if failed:
        st.divider()
        st.markdown("**不及格课程**")
        st.dataframe(pd.DataFrame([{"课程名称": r["course"], "成绩": r["score"], "学分": r["credits"], "学期": r.get("semester")} for r in failed]), use_container_width=True)


def _render_edit() -> None:
    profile = st.session_state.profile
    st.markdown("修改学生信息（修改后不会清空已录入的成绩单数据）")

    # 内联编辑，不跳回向导页面
    catalog = st.session_state.parsed_catalog
    colleges = list(dict.fromkeys(item["college"] for item in catalog))

    college = st.selectbox("学院", colleges, index=colleges.index(profile["college"]) if profile["college"] in colleges else 0, key="edit_college")
    majors = [item["major"] for item in catalog if item["college"] == college]
    major = st.selectbox("专业", majors, index=majors.index(profile["major"]) if profile["major"] in majors else 0, key="edit_major")

    col_year, col_month = st.columns(2)
    with col_year:
        year = st.number_input("入学年份", min_value=2000, max_value=2030, value=profile.get("enrollment_year", 2023), step=1, key="edit_year")
    with col_month:
        month = st.selectbox("入学月份", list(range(1, 13)), index=profile.get("enrollment_month", 9) - 1, key="edit_month")

    current = st.number_input("当前学期", min_value=1, max_value=12, value=profile.get("current_semester", 1), step=1, key="edit_semester")

    if st.button("保存修改", type="primary", use_container_width=True):
        # 保留成绩单等已有数据，只更新基础信息
        profile["college"] = college
        profile["major"] = major
        profile["enrollment_year"] = int(year)
        profile["enrollment_month"] = int(month)
        profile["current_semester"] = int(current)
        st.session_state.profile = profile
        # 同步保存到工作区
        from app.core.workspace import save_profile
        if st.session_state.get("current_workspace"):
            save_profile(st.session_state.current_workspace, profile)
        st.success("学生信息已更新。")
        st.rerun()

    # ---- 成绩单管理 ----
    st.divider()
    st.markdown("**成绩单管理**")
    records = st.session_state.get("transcript_records", [])
    if records:
        st.success(f"当前已保存 {len(records)} 条成绩记录（可在「📊 成绩信息」查看）。")
    else:
        st.info("当前无成绩记录。上传成绩单后可查看绩点、已修课程等。")

    with st.expander("上传 / 重新上传成绩单", expanded=not records):
        st.caption("提示：可前往教务网导出「全部成绩单」，一次上传即可。")
        uploads = st.file_uploader("成绩单文件", type=["pdf", "xlsx", "xls", "csv", "txt"], accept_multiple_files=True, key="edit_transcript_uploads")

        if uploads:
            from app.core.transcript import parse_upload, semester_from_academic_label, semester_from_filename
            new_records = []
            for up in uploads:
                term = semester_from_academic_label(up.name, profile.get("enrollment_year")) or semester_from_filename(up.name)
                try:
                    new_records.extend(parse_upload(up, int(term) if term else None, profile.get("enrollment_year")))
                except Exception as e:
                    st.error(f"{up.name} 解析失败：{e}")

            if new_records:
                # 预览解析结果，等用户确认
                st.markdown(f"**解析预览**（{len(new_records)} 条，请核对后确认）")
                st.dataframe(pd.DataFrame([{
                    "课程名称": r["course"], "成绩": r["score"], "学分": r["credits"],
                    "学期": r.get("semester"), "绩点": r.get("gpa"),
                } for r in new_records]), use_container_width=True)

                if st.button("✓ 确认上传", type="primary", use_container_width=True):
                    # 合并（按课程+学期去重，新记录覆盖旧记录）
                    merged = {f"{r['course']}|{r.get('semester')}": r for r in records}
                    for r in new_records:
                        merged[f"{r['course']}|{r.get('semester')}"] = r
                    st.session_state.transcript_records = list(merged.values())
                    from app.core.workspace import save_transcripts
                    if st.session_state.get("current_workspace"):
                        save_transcripts(st.session_state.current_workspace, st.session_state.transcript_records)
                    st.session_state.transcript_just_uploaded = True
                    st.rerun()

    # 上传成功提示（rerun 后显示一次）
    if st.session_state.pop("transcript_just_uploaded", False):
        st.success(f"✓ 成绩单上传成功！已保存 {len(st.session_state.get('transcript_records', []))} 条记录，可切换到「📊 成绩信息」查看。")
