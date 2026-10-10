from __future__ import annotations

import streamlit as st

from app.core.calc import enrollment_year_bounds, infer_semester, semester_message
from app.state import reset_after


def render() -> None:
    st.header("步骤 B · 学生信息采集")

    saved = st.session_state.profile
    if saved and st.session_state.get("parsed"):
        st.info(
            f"当前已保存：{saved['college']} · {saved['major']} · "
            f"入学 {saved['enrollment_year']} 年 {saved['enrollment_month']} 月 · "
            f"第 {saved['current_semester']} 学期"
        )

    if not st.session_state.get("parsed"):
        _render_parse_step()
        return

    _render_profile_step()

    if st.session_state.get("dialog_open") and "_confirm_major" in st.session_state:
        _confirm_dialog(
            st.session_state.get("_confirm_college", ""),
            st.session_state.get("_confirm_major", ""),
            int(st.session_state.get("_confirm_year", 2023)),
            int(st.session_state.get("_confirm_month", 9)),
        )


# --------------------------- 第一阶段：选专业并解析 ---------------------------

def _render_parse_step() -> None:
    toc = st.session_state.get("toc") or {}
    units = toc.get("units") or []
    pdf_bytes = st.session_state.get("curriculum_pdf_bytes")

    st.subheader("① 选择专业 / 大类并解析")
    if not pdf_bytes:
        st.warning("请先在「1. 培养方案解析」上传培养方案 PDF。")
        return

    if not units:
        st.info("未发现目录（可能是**单一专业**的培养方案）。点击下方按钮解析全文。")
        if st.button("解析全文", type="primary", use_container_width=True):
            st.session_state.selected_unit = None
            _do_parse(pdf_bytes, None, "", "", key="all")
        return

    colleges = list(dict.fromkeys(u["college"] for u in units))
    college = st.selectbox("学院", colleges, key="sel_college")
    college_units = [u for u in units if u["college"] == college]

    def _label(u: dict) -> str:
        if u["kind"] == "category":
            subs = [c["name"] for c in u["children"] if not c.get("intro")]
            return f"{u['name']}（大类 · 大二分流：{'/'.join(subs) if subs else '见方案'}）"
        return u["name"]

    pick = st.selectbox("专业 / 大类", range(len(college_units)), format_func=lambda i: _label(college_units[i]), key="sel_major")
    unit = college_units[pick]

    scope = st.radio(
        "解析范围",
        ("仅所选专业 / 大类（推荐）", "全部专业（全解析，较慢）"),
        horizontal=True,
        key="sel_scope",
    )
    st.caption("解析范围越小越快、越精确；「全部专业」会解析整份方案，耗时较长。")

    if st.button("开始解析", type="primary", use_container_width=True):
        if scope.startswith("全部"):
            st.session_state.selected_unit = None
            _do_parse(pdf_bytes, None, "", "", key="all")
        else:
            st.session_state.selected_unit = unit
            _do_parse(
                pdf_bytes,
                list(range(unit["start_idx"], unit["end_idx"])),
                unit["college"],
                unit["name"],
                key=f"{unit['start_idx']}-{unit['end_idx']}",
            )


def _do_parse(pdf_bytes: bytes, page_indices, seed_college: str, seed_major: str, key: str) -> None:
    from app.core.extract_courses import parse_selection
    from app.core.rag import index_text
    from app.core.workspace import bind_pdf

    pdf_name = st.session_state.get("curriculum_pdf_name") or "培养方案.pdf"
    bar = st.progress(0.0, text="正在解析培养方案…（准确性优先，请勿刷新）")
    try:
        catalog, cache_file = parse_selection(
            pdf_bytes, pdf_name, page_indices, seed_college=seed_college, seed_major=seed_major, key=key,
            progress=lambda d, t: bar.progress(min(d / max(t, 1), 1.0), text=f"正在解析培养方案… {d}/{t} 页"),
        )
    except Exception as error:  # noqa: BLE001 展示给用户
        bar.empty()
        st.error(f"解析失败：{error}")
        return
    bar.empty()

    ws = st.session_state.get("pending_workspace") or st.session_state.get("current_workspace")
    if ws:
        bind_pdf(ws, pdf_name, cache_file)
        st.session_state.current_workspace = ws
    st.session_state.parsed_catalog = catalog
    st.session_state.last_cache_file = cache_file
    st.session_state.parsed = True

    # RAG 索引：一次性同步建立（只索引所选专业/大类的原文）
    text = "\n\n".join(item.get("raw_text", "") for item in catalog)
    ibar = st.progress(0.0, text="正在建立检索索引…（首次需加载模型，可能需要几分钟）")
    try:
        index_text(text, pdf_name, progress=lambda d, t: ibar.progress(min(d / max(t, 1), 1.0), text=f"正在建立检索索引… {d}/{t}"))
    except Exception as error:  # noqa: BLE001 索引失败不影响结构化问答
        st.session_state.flash = f"⚠️ 解析完成，但检索索引建立失败：{error}"
        ibar.empty()
        st.rerun()
        return
    ibar.empty()

    st.session_state.flash = "✅ 解析完成，请继续填写入学信息。"
    st.rerun()


# --------------------------- 第二阶段：入学信息与确认 ---------------------------

def _render_profile_step() -> None:
    catalog = st.session_state.parsed_catalog or []
    if not catalog:
        st.error("未解析出课程，请返回上一步重新解析。")
        return

    selected = st.session_state.get("selected_unit")
    college = catalog[0].get("college", "")
    is_category = isinstance(selected, dict) and selected.get("kind") == "category"

    # 先取入学时间，据以推算当前学期（决定大类是否已分流）
    min_year, max_year = enrollment_year_bounds()
    saved = st.session_state.profile
    col_year, col_month = st.columns(2)
    with col_year:
        default_year = saved["enrollment_year"] if saved else min(2023, max_year)
        year = st.number_input("入学年份", min_value=min_year, max_value=max_year, value=default_year, step=1, key="profile_year")
    with col_month:
        default_month = saved["enrollment_month"] if saved else 9
        month = st.selectbox("入学月份", list(range(1, 13)), index=default_month - 1, key="profile_month")
    guessed = infer_semester(int(year), int(month))
    st.caption(f"按入学时间推算：当前为 **第 {guessed} 学期**（可在下一步确认时调整）。")
    st.divider()

    major = None
    merge = False  # 是否需要把大类共同课程合并进具体专业
    if is_category:
        category_name = selected["name"]
        subs = [c["name"] for c in selected.get("children", []) if not c.get("intro")]
        st.info(f"你是**大类**「{category_name}」招生。大类**大一统一培养，之后可能分流**，系统**只规划分流之前**的课程信息。")
        split_semester = int(st.number_input(
            "该大类预计分流学期（通常第 3 学期）", min_value=2, max_value=8,
            value=int(selected.get("split_semester", 3) or 3), step=1, key="cat_split_sem",
        ))
        _remember_split_semester(split_semester)

        if guessed >= split_semester:
            st.warning("你已到大类分流学期，请选择你**已分流**的具体专业（分流前后成绩不分开、绩点一起算）。")
            major = st.selectbox("分流后的专业", subs or [it["major"] for it in catalog], key="category_major")
            merge = True
        elif guessed + 1 >= split_semester:
            st.warning("你**下学期**将进行专业分流。若要做生涯规划，请先选择**计划分流的专业**；否则只按大类规划。")
            options = ["（暂不分流，只规划大类）"] + (subs or [it["major"] for it in catalog])
            choice = st.selectbox("计划分流专业（可选）", options, key="category_major_future")
            if choice == options[0]:
                major = category_name
            else:
                major = choice
                merge = True
        else:
            major = category_name
            st.caption(f"当前处于分流前，规划只包含大类「{category_name}」的共同课程。")
    else:
        majors = [it["major"] for it in catalog]
        if len(majors) == 1:
            major = majors[0]
            st.caption(f"学院：{college}　专业：{major}")
        else:
            labels = [f"{it['college']} · {it['major']}" for it in catalog]
            pick = st.selectbox(
                "选择专业（当前含多个专业）", range(len(catalog)),
                format_func=lambda i: labels[i], key="profile_major_sel",
            )
            college = catalog[pick].get("college", college)
            major = catalog[pick]["major"]

    if st.button("确认信息，推算学期", type="primary", use_container_width=True):
        if is_category:
            catalog = _apply_category(catalog, selected["name"], major, merge)
            st.session_state.parsed_catalog = catalog
            _persist_catalog(catalog)
        st.session_state["_confirm_college"] = college
        st.session_state["_confirm_major"] = major
        st.session_state["_confirm_year"] = int(year)
        st.session_state["_confirm_month"] = int(month)
        st.session_state.dialog_open = True
        st.rerun()


def _remember_split_semester(split_semester: int) -> None:
    """记录分流学期，便于后续按第3学期等触发分流提示。"""
    st.session_state.split_semester = split_semester


def _apply_category(catalog: list[dict], category_name: str, major_name: str, merge: bool) -> list[dict]:
    """大类处理：需合并则「大类共同课程 + 分流专业」，否则只保留大类共同课程。"""
    if merge:
        merged = _merge_category(catalog, category_name, major_name)
        if merged:
            merged[0]["category"] = category_name
        return merged
    item = next((it for it in catalog if it["major"] == category_name), catalog[0])
    item = dict(item)
    item["category"] = category_name
    return [item]


def _merge_category(catalog: list[dict], category_name: str, major_name: str) -> list[dict]:
    """大类：把「大类共同课程」合并进所选分流专业，形成单一专业画像。"""
    category = next((it for it in catalog if it["major"] == category_name), None)
    chosen = next((it for it in catalog if it["major"] == major_name), None)
    if chosen is None:
        return catalog
    if category is None or category is chosen:
        return [chosen]
    seen = set()
    courses = []
    for course in list(category.get("courses", [])) + list(chosen.get("courses", [])):
        dedup_key = (course["name"], course["semester"], course["credits"])
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        courses.append(course)
    merged = dict(chosen)
    merged["courses"] = courses
    merged["raw_text"] = "\n".join(filter(None, [category.get("raw_text", ""), chosen.get("raw_text", "")]))
    reqs = dict(category.get("credit_requirements") or {})
    reqs.update(chosen.get("credit_requirements") or {})
    merged["credit_requirements"] = reqs
    merged["category"] = category_name
    return [merged]


def _persist_catalog(catalog: list[dict]) -> None:
    cache_file = st.session_state.get("last_cache_file")
    if not cache_file:
        return
    import json

    from app.core.extract_courses import CACHE_DIR

    try:
        (CACHE_DIR / cache_file).write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# --------------------------- 学期确认对话框 ---------------------------

@st.dialog("确认当前学期")
def _confirm_dialog(college: str, major: str, year: int, month: int) -> None:
    guessed = infer_semester(year, month)
    st.markdown(f"根据入学时间，推算当前为 **第 {guessed} 学期**。")
    if warning := semester_message(guessed):
        st.warning(warning)

    choice = st.radio("是否确认？", ("确认无误", "需要调整"), horizontal=True)
    if choice == "确认无误":
        if st.button("保存并继续", type="primary", use_container_width=True):
            _save_profile(college, major, year, month, max(1, guessed))
            st.session_state.dialog_open = False
            _next_stage(max(1, guessed))
    else:
        adjusted = st.number_input("调整后的当前学期", min_value=1, max_value=12, value=max(1, guessed), step=1)
        st.caption("允许按休学、提前修读等真实情况调整。")
        if st.button("保存调整并继续", type="primary", use_container_width=True):
            _save_profile(college, major, year, month, int(adjusted))
            st.session_state.dialog_open = False
            _next_stage(int(adjusted))


def _next_stage(current_semester: int) -> None:
    """学生信息确认后 → 进入本学期课表步骤。"""
    st.session_state.stage = 3
    st.rerun()


def _save_profile(college: str, major: str, year: int, month: int, current_semester: int) -> None:
    existing = st.session_state.profile or {}
    profile = {
        "college": college,
        "major": major,
        "enrollment_year": year,
        "enrollment_month": month,
        "current_semester": current_semester,
        "completed_credits": existing.get("completed_credits", {}),
        "completed_courses": existing.get("completed_courses", []),
        "failed_courses": existing.get("failed_courses", []),
        "missing_semesters": existing.get("missing_semesters", []),
    }
    st.session_state.profile = profile
    from app.core.workspace import save_profile

    if st.session_state.get("current_workspace"):
        save_profile(st.session_state.current_workspace, profile)
    reset_after(2)
