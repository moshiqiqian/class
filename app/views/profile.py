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

    # 选项：大类本身 + 该大类下已分流的各专业；以及独立专业。
    # 选「已分流专业」时会解析整个大类（大类信息与分流专业不分隔），再合并。
    options: list[dict] = []
    for u in college_units:
        if u["kind"] == "category":
            options.append({"label": f"{u['name']}（大类·暂未分流）", "unit": u, "merge_major": ""})
            for child in u["children"]:
                if child.get("intro"):
                    continue
                options.append({"label": f"{child['name']}（{u['name']}·已分流）", "unit": u, "merge_major": child["name"]})
        else:
            options.append({"label": u["name"], "unit": u, "merge_major": ""})
    pick = st.selectbox("专业 / 大类", range(len(options)), format_func=lambda i: options[i]["label"], key="sel_major")
    option = options[pick]
    unit = option["unit"]

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
            st.session_state.selected_unit = {**unit, "merge_major": option["merge_major"]}
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
    merge_major = selected.get("merge_major", "") if isinstance(selected, dict) else ""

    # 入学时间：不给默认值，用占位提示（用户必须显式选择）
    min_year, max_year = enrollment_year_bounds()
    years = list(range(min_year, max_year + 1))
    saved = st.session_state.profile
    col_year, col_month = st.columns(2)
    with col_year:
        year_index = years.index(saved["enrollment_year"]) if saved and saved.get("enrollment_year") in years else None
        year = st.selectbox("入学年份", years, index=year_index, placeholder="请选择入学年份", key="profile_year")
    with col_month:
        month_index = (saved["enrollment_month"] - 1) if saved and saved.get("enrollment_month") else None
        month = st.selectbox("入学月份", list(range(1, 13)), index=month_index, placeholder="请选择入学月份（如 9）", key="profile_month")

    guessed = infer_semester(int(year), int(month)) if (year and month) else None
    if guessed is None:
        st.caption("请先选择入学年份与月份。")
    else:
        st.caption(f"按入学时间推算：当前为 **第 {guessed} 学期**（可在确认时调整）。")
    st.divider()

    major = None
    merge = False  # 是否需要把大类共同课程合并进具体专业
    if is_category and not merge_major:
        category_name = selected["name"]
        st.info(f"你是**大类**「{category_name}」招生，大类内**暂未分流**。系统**只规划分流之前**的课程信息。")
        split_semester = _detect_split_semester(catalog)
        if split_semester:
            st.caption(f"培养方案显示：该大类约在 **第 {split_semester} 学期**分流（分流前后成绩不分开、绩点一起算）。")
        major = category_name
    elif is_category and merge_major:
        st.success(f"已按**分流后专业**「{merge_major}」规划；同时并入大类「{selected['name']}」的共同课程（成绩不分隔）。")
        major = merge_major
        merge = True
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

    if st.button("确认信息，推算学期", type="primary", use_container_width=True, disabled=guessed is None):
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


def _detect_split_semester(catalog: list[dict]) -> int | None:
    """从培养方案原文自动解析大类分流学期。

    支持三类写法：
      1)「第X学期…分流/选择专业/确定专业」
      2)「专业类共同修读课程设置表（1-3学期）」→ 取末学期
      3)「专业类共同课学习年限：1.5年」→ 年限×2
    """
    import re

    text = re.sub(r"\s+", "", "".join(it.get("raw_text", "") for it in catalog))
    cn = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}

    for match in re.finditer(r"第([一二三四五六七八九十\d]+)学期", text):
        segment = text[match.start():match.start() + 50]
        if any(word in segment for word in ("分流", "划分专业", "选择专业", "确定专业", "分专业")):
            token = match.group(1)
            return int(token) if token.isdigit() else cn.get(token)

    if m := re.search(r"共同修读课程设置表[（(]([0-9]+)\s*[-~至]\s*([0-9]+)\s*学期", text):
        return int(m.group(2))

    if m := re.search(r"共同课学习年限[:：]([0-9.]+)年", text):
        return max(1, round(float(m.group(1)) * 2))

    return None


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
