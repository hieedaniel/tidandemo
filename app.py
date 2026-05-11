import os
import json
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="智能产品检索系统",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

from core.data_manager import DataManager
from core.llm_mapper import LLMMapper
from core.rule_engine import RuleEngine
from core.doc_extractor import extract_text_from_file, extract_products_from_doc, infer_category_schema_from_docs


# ── Session state ──────────────────────────────────────────────────────────────
_DEFAULT_API_KEY  = "sk-SFV5KDIooMe5vtHlnJpWadaC4Jzk7SJC27o3xCPpuC5JrAZL"
_DEFAULT_BASE_URL = "https://aibedrock.uniview.com"
_DEFAULT_MODEL    = "glm-5"


def _init_state():
    for k, v in {
        "api_key": os.getenv("ANTHROPIC_AUTH_TOKEN", os.getenv("ANTHROPIC_API_KEY", _DEFAULT_API_KEY)),
        "base_url": os.getenv("ANTHROPIC_BASE_URL", _DEFAULT_BASE_URL),
        "model": os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", os.getenv("ANTHROPIC_MODEL", _DEFAULT_MODEL)),
        "dm": None,
        "last_extracted": None,
        "last_results": None,
        "last_input": "",
        "last_category": None,
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_state()


def get_dm() -> DataManager:
    if st.session_state.dm is None:
        st.session_state.dm = DataManager()
    return st.session_state.dm


# ── Render helpers ─────────────────────────────────────────────────────────────

def render_extracted_params(extracted: dict):
    summary = extracted.get("summary", "")
    category = extracted.get("category")
    params = extracted.get("extracted_params", [])
    error = extracted.get("error")

    if error:
        st.warning(f"解析可能不完整: {error[:200]}")

    col_s, col_c = st.columns([3, 1])
    with col_s:
        st.info(f"**需求摘要**: {summary}")
    with col_c:
        if category:
            st.success(f"**识别类别**: {category}")
        else:
            st.warning("**类别**: 未识别")

    if params:
        rows = []
        for p in params:
            rows.append({
                "参数名": p.get("standard_name", ""),
                "列名": p.get("column_name", ""),
                "运算符": p.get("operator", ""),
                "需求值": str(p.get("value", "")),
                "单位": p.get("unit", ""),
                "原文片段": p.get("original_text", ""),
                "置信度": f"{float(p.get('confidence', 0)):.0%}",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.warning("未提取到任何参数，请检查输入内容或 API Key。")


def render_results(results: pd.DataFrame, extracted: dict, global_config: dict):
    if results.empty:
        st.warning("无产品数据可供筛选。")
        return

    passed = results[results["_pass"]]
    failed = results[~results["_pass"]]
    price_dir = global_config.get("price_sort", "asc")

    st.markdown(
        f"**通过筛选**: {len(passed)} 款 &nbsp;｜&nbsp; "
        f"**被过滤**: {len(failed)} 款 &nbsp;｜&nbsp; "
        f"价格排序: {'低→高' if price_dir == 'asc' else '高→低'}"
    )

    if passed.empty:
        st.error("没有产品通过特殊规格过滤，请检查规则配置或放宽客户需求条件。")
    else:
        st.markdown("**推荐产品列表**（按综合评分排序）")
        top_n = min(10, len(passed))
        for rank, (_, row) in enumerate(passed.head(top_n).iterrows(), 1):
            total_score = row.get("_total_score", 0)
            base_score = row.get("_score", 0)
            tag_bonus = row.get("_tag_bonus", 0)
            score_detail = row.get("_score_detail", {})
            match_reasons = row.get("_match_reasons", [])

            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"#{rank}")
            name = row.get("product_name", row.get("product_id", "未知"))
            price = row.get("price", "—")
            tags = [t.strip() for t in str(row.get("tags", "")).split(";") if t.strip()]
            tag_str = "  " + "  ".join(f"`{t}`" for t in tags) if tags else ""

            header = (
                f"{medal} **{name}**"
                f"  —  综合得分 **{total_score:.1f}**"
                f"  ｜  ¥{price}"
                f"{tag_str}"
            )

            with st.expander(header, expanded=(rank <= 3)):
                ec1, ec2 = st.columns([1, 1])

                with ec1:
                    st.markdown("**评分明细**")
                    if score_detail:
                        detail_rows = []
                        for d in score_detail.values():
                            degree = d.get("match_degree", 0)
                            filled = int(min(degree, 1.0) * 10)
                            bar = "█" * filled + "░" * (10 - filled)
                            extra = " 超额" if degree > 1.0 else ""
                            detail_rows.append({
                                "规格": d.get("name", ""),
                                "要求": f"{d.get('operator','')}{d.get('required','')}",
                                "实际值": str(d.get("actual", "")),
                                "匹配度": f"{bar} {degree:.0%}{extra}",
                                "得分": f"{d.get('score', 0):.1f}",
                            })
                        st.dataframe(
                            pd.DataFrame(detail_rows),
                            use_container_width=True,
                            hide_index=True,
                        )
                    st.caption(
                        f"基础分 {base_score:.1f} + 标签加成 {tag_bonus:+.1f} = 总分 **{total_score:.1f}**"
                    )
                    if match_reasons:
                        st.success("  \n".join(match_reasons))

                with ec2:
                    st.markdown("**完整规格**")
                    display_cols = [
                        c for c in row.index
                        if not c.startswith("_") and c != "product_id"
                    ]
                    spec_data = {c: str(row[c]) for c in display_cols if c in row.index}
                    st.dataframe(
                        pd.DataFrame.from_dict(spec_data, orient="index", columns=["值"]),
                        use_container_width=True,
                    )

    if not failed.empty:
        with st.expander(f"查看被过滤产品（{len(failed)} 款）"):
            fail_rows = []
            for _, row in failed.iterrows():
                reasons = row.get("_fail_reasons", [])
                fail_rows.append({
                    "产品": row.get("product_name", ""),
                    "过滤原因": " | ".join(reasons),
                })
            st.dataframe(pd.DataFrame(fail_rows), use_container_width=True, hide_index=True)


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🔍 智能检索系统")
    st.caption("LLM + 规则引擎 · 产品匹配 Demo")
    st.divider()

    st.subheader("🔑 API 配置")
    key_input = st.text_input(
        "API Key",
        value=st.session_state.api_key,
        type="password",
        placeholder="sk-...",
        help="对应 .env 中的 ANTHROPIC_AUTH_TOKEN",
    )
    if key_input != st.session_state.api_key:
        st.session_state.api_key = key_input

    base_url_input = st.text_input(
        "Base URL（留空使用官方）",
        value=st.session_state.base_url,
        placeholder="https://aibedrock.uniview.com",
        help="对应 .env 中的 ANTHROPIC_BASE_URL",
    )
    if base_url_input != st.session_state.base_url:
        st.session_state.base_url = base_url_input

    model_input = st.text_input(
        "模型名称",
        value=st.session_state.model,
        placeholder="glm-5",
        help="对应 .env 中的 ANTHROPIC_MODEL",
    )
    if model_input != st.session_state.model:
        st.session_state.model = model_input

    if st.session_state.api_key:
        st.success(f"已配置: {st.session_state.model} ✅")
    else:
        st.warning("请输入 API Key ⚠️")

    st.divider()
    dm_sidebar = get_dm()
    counts = dm_sidebar.get_product_counts()
    total = sum(counts.values())
    st.markdown(f"**产品库总计**: {total} 款")
    for cat, cnt in counts.items():
        st.markdown(f"  · {cat}: **{cnt}** 款")


# ── Main tabs ──────────────────────────────────────────────────────────────────
tab_search, tab_products, tab_rules = st.tabs(
    ["🔍  智能检索", "📦  产品库管理", "⚙️  规则配置"]
)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — 智能检索
# ══════════════════════════════════════════════════════════════════════════════
with tab_search:
    st.header("智能产品检索")
    st.caption("粘贴客户/竞品参数描述 → Claude 解析 → 规则引擎匹配 → 推荐产品")

    col_input, col_hint = st.columns([3, 1])

    with col_hint:
        st.markdown("**示例需求（点击填入）**")
        examples = {
            "工业相机（高分辨率）": (
                "需要一款彩色工业相机，分辨率不低于500万像素，帧率至少25fps，"
                "接口要求GigE，需要全局快门，工作温度-20到70度，防护等级IP67"
            ),
            "网络摄像机（红外夜视）": (
                "需要室外用红外网络摄像机，分辨率400万，夜视距离50米以上，"
                "防护IP67，支持PoE供电，工作温度-30℃到60℃"
            ),
            "信息发布屏（55寸壁挂）": (
                "需要一台55寸壁挂式信息发布屏，全彩，亮度400cd，Android系统，"
                "支持WiFi，内存2GB以上"
            ),
        }
        for label, text in examples.items():
            if st.button(label, use_container_width=True, key=f"ex_{label}"):
                st.session_state.last_input = text
                st.rerun()

    with col_input:
        customer_text = st.text_area(
            "客户需求描述",
            value=st.session_state.last_input,
            height=180,
            placeholder="在此粘贴客户提供的竞品参数或需求描述...",
            label_visibility="collapsed",
        )

        btn1, btn2 = st.columns([2, 1])
        with btn1:
            run_search = st.button(
                "🚀  开始智能检索",
                type="primary",
                use_container_width=True,
                disabled=(not st.session_state.api_key or not customer_text.strip()),
            )
        with btn2:
            if st.button("🗑️  清空", use_container_width=True):
                st.session_state.last_extracted = None
                st.session_state.last_results = None
                st.session_state.last_input = ""
                st.session_state.last_category = None
                st.rerun()

    # ── Execute search ─────────────────────────────────────────────────────────
    if run_search and customer_text.strip():
        st.session_state.last_input = customer_text
        dm = get_dm()
        config = dm.get_config()
        categories_config = config.get("categories", {})

        st.divider()
        st.markdown("#### Step 1 · Claude 参数解析（两步识别）")
        with st.spinner("Step 1a: 识别产品类别..."):
            try:
                mapper = LLMMapper(
                    st.session_state.api_key,
                    base_url=st.session_state.base_url or None,
                    model=st.session_state.model,
                )
                extracted = mapper.extract_params(customer_text, categories_config)
                st.session_state.last_extracted = extracted
                st.session_state.last_category = extracted.get("category")
            except Exception as e:
                st.error(f"LLM 调用失败: {e}")
                extracted = None

        if extracted:
            render_extracted_params(extracted)
            category = extracted.get("category")

            if not category:
                st.error("未能识别产品类别，请在需求描述中明确产品类型（如：工业相机、网络摄像机、信息发布屏）。")
            else:
                products = dm.get_products(category)
                if products.empty:
                    st.warning(f"【{category}】产品库为空，请先在【产品库管理】中导入产品数据。")
                else:
                    st.markdown("#### Step 2 · 规则引擎筛选与评分")
                    with st.spinner("正在应用规则过滤和评分..."):
                        cat_cfg = dm.get_category_config(category)
                        global_cfg = dm.get_global_config()
                        engine = RuleEngine(cat_cfg, global_cfg)
                        results = engine.filter_and_score(products, extracted)
                        st.session_state.last_results = results
                    render_results(results, extracted, global_cfg)

    elif (
        st.session_state.last_extracted is not None
        and st.session_state.last_results is not None
    ):
        dm = get_dm()
        st.divider()
        st.markdown("#### Step 1 · Claude 参数解析（两步识别）")
        render_extracted_params(st.session_state.last_extracted)
        st.markdown("#### Step 2 · 规则引擎筛选与评分")
        render_results(
            st.session_state.last_results,
            st.session_state.last_extracted,
            dm.get_global_config(),
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — 产品库管理
# ══════════════════════════════════════════════════════════════════════════════
with tab_products:
    st.header("产品库管理")
    dm = get_dm()
    categories = dm.get_categories()

    if not categories:
        st.error("未找到产品类别配置，请检查 data/default_config.json。")
    else:
        # Category selector
        sel_cat = st.selectbox("选择产品类别", categories, key="prod_mgmt_cat")

        cat_cfg = dm.get_category_config(sel_cat)
        param_schema = cat_cfg.get("param_schema", {})
        spec_cols = [info["column"] for info in param_schema.values() if "column" in info]

        # ── AI 智能导入（产品文档） ─────────────────────────────────────────────
        with st.expander("🤖  AI 智能导入（上传产品彩页/规格文档）", expanded=False):
            st.caption(
                "支持 PDF 或 TXT 格式的产品彩页/规格书，单次最多 20 个文件。"
                "每个文件独立提取，汇总后统一预览确认再写入产品库。"
            )

            doc_files = st.file_uploader(
                "选择产品文档（可多选）",
                type=["pdf", "txt"],
                accept_multiple_files=True,
                help="Windows/Mac 均可按住 Ctrl/Cmd 多选文件",
                key=f"doc_upload_{sel_cat}",
            )

            if doc_files:
                if len(doc_files) > 20:
                    st.warning(f"单次最多 20 个文件，已自动忽略后 {len(doc_files) - 20} 个。")
                    doc_files = doc_files[:20]

                st.caption(f"已选择 **{len(doc_files)}** 个文件：" +
                           "、".join(f.name for f in doc_files))

                if not st.session_state.api_key:
                    st.warning("请先在侧边栏配置 API Key。")
                else:
                    if st.button(
                        f"🚀  开始 AI 批量提取（{len(doc_files)} 个文件）",
                        type="primary",
                        key=f"doc_extract_{sel_cat}",
                    ):
                        import anthropic as _anthropic
                        _client = _anthropic.Anthropic(
                            api_key=st.session_state.api_key,
                            **({"base_url": st.session_state.base_url}
                               if st.session_state.base_url else {}),
                        )

                        all_products: list = []
                        file_logs: list = []

                        progress_bar = st.progress(0, text="准备开始...")
                        log_placeholder = st.empty()

                        for idx, doc_file in enumerate(doc_files):
                            pct = idx / len(doc_files)
                            progress_bar.progress(
                                pct,
                                text=f"正在处理 [{idx + 1}/{len(doc_files)}]：{doc_file.name}",
                            )
                            try:
                                doc_text = extract_text_from_file(doc_file)
                                result = extract_products_from_doc(
                                    doc_text,
                                    sel_cat,
                                    param_schema,
                                    _client,
                                    st.session_state.model,
                                )
                                prods = result.get("products", [])
                                all_products.extend(prods)
                                file_logs.append({
                                    "文件": doc_file.name,
                                    "状态": f"✅ 提取 {len(prods)} 款",
                                    "摘要": result.get("doc_summary", ""),
                                })
                            except Exception as e:
                                file_logs.append({
                                    "文件": doc_file.name,
                                    "状态": "❌ 失败",
                                    "摘要": str(e)[:120],
                                })

                        progress_bar.progress(1.0, text="全部文件处理完成！")

                        st.session_state[f"doc_result_{sel_cat}"] = {
                            "products": all_products,
                            "file_logs": file_logs,
                        }
                        st.rerun()

            # ── Preview & confirm ──────────────────────────────────────────────
            doc_result = st.session_state.get(f"doc_result_{sel_cat}")
            if doc_result:
                file_logs = doc_result.get("file_logs", [])
                products_raw = doc_result.get("products", [])

                # Per-file status table
                if file_logs:
                    st.markdown("**逐文件提取结果**")
                    st.dataframe(
                        pd.DataFrame(file_logs),
                        use_container_width=True,
                        hide_index=True,
                        height=min(38 + len(file_logs) * 35, 280),
                    )

                if not products_raw:
                    st.warning("所有文件均未提取到产品记录，请检查文档内容或 API Key。")
                else:
                    st.success(
                        f"共提取 **{len(products_raw)}** 款产品（来自 {len(file_logs)} 个文件），"
                        "请确认后写入产品库。"
                    )

                    df_preview = pd.DataFrame(products_raw)
                    front = [c for c in ["product_id", "product_name", "tags", "price"]
                             if c in df_preview.columns]
                    rest = [c for c in df_preview.columns if c not in front]
                    df_preview = df_preview[front + rest]

                    st.dataframe(df_preview, use_container_width=True, height=300)

                    col_append, col_replace, col_cancel = st.columns([2, 2, 1])
                    with col_append:
                        if st.button(
                            "➕  追加写入产品库",
                            use_container_width=True,
                            key=f"doc_append_{sel_cat}",
                        ):
                            try:
                                existing = dm.get_products(sel_cat).drop(
                                    columns=["category"], errors="ignore"
                                )
                                merged = pd.concat([existing, df_preview], ignore_index=True)
                                merged = merged.drop_duplicates(subset=["product_id"], keep="last")
                                dm.save_products_df(merged, sel_cat)
                                st.session_state[f"doc_result_{sel_cat}"] = None
                                st.success(f"追加成功！产品库现有 {len(merged)} 款产品。")
                                st.rerun()
                            except Exception as e:
                                st.error(f"写入失败: {e}")
                    with col_replace:
                        if st.button(
                            "🔁  覆盖写入（替换全部）",
                            use_container_width=True,
                            key=f"doc_replace_{sel_cat}",
                        ):
                            try:
                                dm.save_products_df(df_preview, sel_cat)
                                st.session_state[f"doc_result_{sel_cat}"] = None
                                st.success(f"覆盖成功！产品库现有 {len(df_preview)} 款产品。")
                                st.rerun()
                            except Exception as e:
                                st.error(f"写入失败: {e}")
                    with col_cancel:
                        if st.button("✖  取消", use_container_width=True,
                                     key=f"doc_cancel_{sel_cat}"):
                            st.session_state[f"doc_result_{sel_cat}"] = None
                            st.rerun()

        # ── CSV/Excel 导入 ─────────────────────────────────────────────────────
        col_up, col_tip = st.columns([2, 1])

        with col_up:
            st.subheader(f"CSV/Excel 导入【{sel_cat}】")
            uploaded = st.file_uploader(
                "上传 CSV 或 Excel 文件",
                type=["csv", "xlsx", "xls"],
                help="CSV 请使用 UTF-8 编码，第一行为列名",
                key=f"upload_{sel_cat}",
            )
            if uploaded:
                try:
                    df_new = dm.import_products_for_category(uploaded, sel_cat)
                    st.session_state.dm = dm
                    st.success(f"导入成功！共 {len(df_new)} 款产品，{len(df_new.columns)} 个字段。")
                    st.rerun()
                except Exception as e:
                    st.error(f"导入失败: {e}")

            if st.button("🔄  重置数据库（恢复出厂种子数据）", use_container_width=True, key="reset_db"):
                dm.reset_to_seed()
                st.session_state.dm = dm
                st.success("数据库已重置，种子数据已恢复！")
                st.rerun()

        with col_tip:
            st.subheader("格式说明")
            st.markdown("**必需列**")
            st.markdown("- `product_id`  产品编号（主键）\n- `product_name`  产品名称\n- `tags`  标签（分号分隔）")
            st.markdown("**规格列**（与当前类别参数字典对应）")
            for pname, pinfo in param_schema.items():
                col = pinfo.get("column", "")
                unit = pinfo.get("unit", "")
                st.markdown(f"  · `{col}` — {pname}{' (' + unit + ')' if unit else ''}")

            # Download current category as CSV
            csv_bytes = dm.export_products_csv(sel_cat)
            st.download_button(
                f"⬇️  下载【{sel_cat}】产品表 CSV",
                csv_bytes,
                f"products_{sel_cat}.csv",
                "text/csv",
                key=f"dl_{sel_cat}",
            )

        st.divider()
        st.subheader(f"当前【{sel_cat}】产品库")

        products_view = dm.get_products(sel_cat)
        if products_view.empty:
            st.info(f"【{sel_cat}】产品库为空，请使用上方导入功能添加产品数据。")
        else:
            fc1, fc2 = st.columns(2)
            with fc1:
                all_tags_cat = dm.get_category_tags(sel_cat)
                sel_tag = st.selectbox("按标签筛选", ["全部"] + all_tags_cat, key=f"tag_{sel_cat}")
            with fc2:
                search_kw = st.text_input("搜索产品名称", placeholder="关键词...", key=f"kw_{sel_cat}")

            view = products_view.copy()
            if sel_tag != "全部" and "tags" in view.columns:
                view = view[view["tags"].astype(str).str.contains(sel_tag, na=False)]
            if search_kw and "product_name" in view.columns:
                view = view[view["product_name"].astype(str).str.contains(search_kw, na=False)]

            # Show only relevant columns (hide 'category' virtual column)
            show_cols = [c for c in view.columns if c != "category"]
            st.caption(f"显示 {len(view)} / {len(products_view)} 款产品 · {len(spec_cols)} 个规格字段")

            # Use data_editor for inline editing
            edited_df = st.data_editor(
                view[show_cols],
                use_container_width=True,
                height=450,
                num_rows="dynamic",
                key=f"prod_table_{sel_cat}",
                hide_index=True,
                column_config={
                    "product_id": st.column_config.TextColumn("产品编号", required=True),
                    "product_name": st.column_config.TextColumn("产品名称"),
                    "tags": st.column_config.TextColumn("标签（分号分隔）"),
                    "price": st.column_config.NumberColumn("价格（元）", min_value=0, format="%.0f"),
                },
            )

            # Save button for edited data
            save_col, reset_col = st.columns([3, 1])
            with save_col:
                if st.button("💾  保存修改", type="primary", use_container_width=True, key=f"save_edit_{sel_cat}"):
                    try:
                        # Merge back with original data (including filtered-out rows)
                        original = products_view.drop(columns=["category"], errors="ignore")
                        edited_full = original.copy()
                        # Update edited rows by product_id
                        edited_df_clean = edited_df.dropna(subset=["product_id"])
                        for _, row in edited_df_clean.iterrows():
                            pid = row["product_id"]
                            if pid in edited_full["product_id"].values:
                                edited_full.loc[edited_full["product_id"] == pid, row.index] = row.values

                        # Save to database
                        dm.save_products_df(edited_full, sel_cat)
                        st.session_state.dm = dm
                        st.success(f"已保存修改！产品库现有 {len(edited_full)} 款产品。")
                        st.rerun()
                    except Exception as e:
                        st.error(f"保存失败: {e}")

            with reset_col:
                if st.button("🔄  刷新", use_container_width=True, key=f"reset_view_{sel_cat}"):
                    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — 规则配置
# ══════════════════════════════════════════════════════════════════════════════
with tab_rules:
    st.header("规则配置")
    dm = get_dm()
    config = dm.get_config()
    categories = dm.get_categories()

    if not categories:
        st.error("未找到类别配置。")
    else:
        cat_tab_labels = categories + ["➕ 新增类别", "⚙️ 全局设置"]
        cat_tabs = st.tabs(cat_tab_labels)

        # ── Per-category rule tabs ─────────────────────────────────────────────
        for i, cat in enumerate(categories):
            with cat_tabs[i]:
                cat_cfg = config.get("categories", {}).get(cat, {})
                param_schema = cat_cfg.get("param_schema", {})

                all_spec_cols = [
                    info["column"]
                    for info in param_schema.values()
                    if info.get("column") not in ("price", "product_id", "product_name", "tags")
                ]
                col_display = {
                    info["column"]: info.get("display_name", info["column"])
                    for info in param_schema.values()
                    if info.get("column")
                }

                # ── Section A: 参数字典编辑 ────────────────────────────────────
                with st.expander("📋  编辑参数字典", expanded=False):
                    st.caption(
                        "可新增/修改/删除参数字段。修改后点击保存，系统会自动为数据库表添加新列（已有列不会删除）。"
                    )

                    edit_schema_key = f"edit_schema_{cat}"
                    cached = st.session_state.get(edit_schema_key)
                    # Sync from config when cache is absent or stale (empty while schema has data)
                    if not cached and param_schema:
                        st.session_state[edit_schema_key] = {
                            k: dict(v) for k, v in param_schema.items()
                        }
                    edit_schema: dict = st.session_state.get(edit_schema_key, {})

                    # Display existing params as editable rows
                    to_delete = []
                    param_names = list(edit_schema.keys())

                    # Header row
                    if param_names:
                        hdr_cols = st.columns([2, 2, 1, 1, 2, 1.5, 0.5])
                        for h, label in zip(hdr_cols, ["参数名", "列名(column)", "类型", "单位", "枚举值(;分隔)", "默认比较方式", ""]):
                            h.caption(label)

                    for pname in param_names:
                        info = edit_schema[pname]
                        pc1, pc2, pc3, pc4, pc5, pc6, pc7 = st.columns([2, 2, 1, 1, 2, 1.5, 0.5])
                        with pc1:
                            new_pname = st.text_input(
                                "参数名", value=pname, key=f"pn_{cat}_{pname}",
                                label_visibility="collapsed",
                            )
                        with pc2:
                            new_col = st.text_input(
                                "列名", value=info.get("column", ""), key=f"pc_{cat}_{pname}",
                                label_visibility="collapsed",
                            )
                        with pc3:
                            typ_opts = ["numeric", "text", "enum"]
                            cur_typ = info.get("type", "text")
                            new_typ = st.selectbox(
                                "类型", typ_opts,
                                index=typ_opts.index(cur_typ) if cur_typ in typ_opts else 1,
                                key=f"pt_{cat}_{pname}",
                                label_visibility="collapsed",
                            )
                        with pc4:
                            new_unit = st.text_input(
                                "单位", value=info.get("unit", ""), key=f"pu_{cat}_{pname}",
                                label_visibility="collapsed",
                            )
                        with pc5:
                            opts_str = ";".join(info.get("options", []))
                            new_opts_str = st.text_input(
                                "枚举值(分号分隔)", value=opts_str, key=f"po_{cat}_{pname}",
                                label_visibility="collapsed",
                            )
                        with pc6:
                            # Default operator based on type
                            op_map = {
                                "numeric": ["=", ">=", "<=", ">", "<"],
                                "enum": ["=", "contains"],
                                "text": ["=", "contains"],
                            }
                            cur_op = info.get("default_operator", "=")
                            avail_ops = op_map.get(new_typ, ["=", "contains"])
                            new_op = st.selectbox(
                                "默认比较", avail_ops,
                                index=avail_ops.index(cur_op) if cur_op in avail_ops else 0,
                                key=f"dop_{cat}_{pname}",
                                label_visibility="collapsed",
                            )
                        with pc7:
                            if st.button("🗑", key=f"pdel_{cat}_{pname}", help="删除此参数"):
                                to_delete.append(pname)

                        edit_schema[pname] = {
                            **info,
                            "column": new_col,
                            "type": new_typ,
                            "unit": new_unit,
                            "options": [o.strip() for o in new_opts_str.split(";") if o.strip()],
                            "default_operator": new_op,
                        }
                        if new_pname != pname:
                            edit_schema[new_pname] = edit_schema.pop(pname)

                    for d in to_delete:
                        edit_schema.pop(d, None)

                    st.divider()
                    # Add new param row
                    st.markdown("**新增参数**")
                    na1, na2, na3, na4, na5, na6 = st.columns([2, 2, 1, 1, 2, 1.5])
                    with na1:
                        new_p_name = st.text_input("参数名", key=f"new_pname_{cat}", placeholder="如：工作电压")
                    with na2:
                        new_p_col = st.text_input("列名", key=f"new_pcol_{cat}", placeholder="如：voltage_v")
                    with na3:
                        new_p_typ = st.selectbox("类型", ["numeric", "text", "enum"], key=f"new_ptyp_{cat}")
                    with na4:
                        new_p_unit = st.text_input("单位", key=f"new_punit_{cat}", placeholder="如：V")
                    with na5:
                        new_p_opts = st.text_input("枚举值", key=f"new_popts_{cat}", placeholder="值1;值2;值3")
                    with na6:
                        new_p_ops = ["=", ">=", "<=", ">", "<"] if new_p_typ == "numeric" else ["=", "contains"]
                        new_p_op = st.selectbox("默认比较", new_p_ops, key=f"new_pdop_{cat}")

                    ab1, ab2 = st.columns([1, 2])
                    with ab1:
                        if st.button("➕  添加参数行", key=f"add_param_{cat}"):
                            if new_p_name.strip() and new_p_col.strip():
                                edit_schema[new_p_name.strip()] = {
                                    "column": new_p_col.strip(),
                                    "type": new_p_typ,
                                    "unit": new_p_unit.strip(),
                                    "options": [o.strip() for o in new_p_opts.split(";") if o.strip()],
                                    "default_operator": new_p_op,
                                }
                                st.rerun()
                            else:
                                st.warning("参数名和列名不能为空。")
                    with ab2:
                        if st.button("💾  保存参数字典", type="primary", key=f"save_schema_{cat}"):
                            try:
                                dm.update_param_schema(cat, dict(edit_schema))
                                st.session_state.pop(edit_schema_key, None)
                                st.session_state.dm = None
                                st.success(f"【{cat}】参数字典已保存，数据库表已同步。")
                                st.rerun()
                            except Exception as e:
                                st.error(f"保存失败: {e}")

                st.caption(f"共 {len(param_schema)} 个参数字段")

                # ── Section B: 规则配置表单 ────────────────────────────────────
                with st.form(f"rules_form_{cat}"):
                    st.subheader("1 · 特殊规格（一票否决）")
                    st.caption("选中的列必须完全匹配客户要求，否则产品直接淘汰。")
                    cur_special = cat_cfg.get("special_specs", [])
                    new_special = st.multiselect(
                        "特殊规格列",
                        options=all_spec_cols,
                        default=[c for c in cur_special if c in all_spec_cols],
                        format_func=lambda c: f"{col_display.get(c, c)} ({c})",
                        key=f"special_{cat}",
                    )

                    st.subheader("2 · 重要规格（加权评分）")
                    st.caption("权重决定该规格对最终得分的贡献比例，系统会自动归一化。")
                    cur_imp = cat_cfg.get("important_specs", {})
                    imp_options = [c for c in all_spec_cols if c not in new_special]
                    imp_cols = st.multiselect(
                        "重要规格列",
                        options=imp_options,
                        default=[c for c in cur_imp if c in imp_options],
                        format_func=lambda c: f"{col_display.get(c, c)} ({c})",
                        key=f"imp_{cat}",
                    )

                    new_important: dict = {}
                    if imp_cols:
                        n_cols = min(len(imp_cols), 3)
                        slider_cols = st.columns(n_cols)
                        pref_opts = [">=", "<=", "=", "any"]
                        for j, col in enumerate(imp_cols):
                            with slider_cols[j % n_cols]:
                                old = cur_imp.get(col, {})
                                disp = col_display.get(col, col)
                                w = st.slider(
                                    f"{disp} 权重",
                                    0.0, 1.0,
                                    float(old.get("weight", 0.25)),
                                    0.05,
                                    key=f"w_{cat}_{col}",
                                )
                                pref_idx = pref_opts.index(old.get("preference", ">=")) \
                                    if old.get("preference", ">=") in pref_opts else 0
                                pref = st.selectbox(
                                    "比较方向",
                                    pref_opts,
                                    index=pref_idx,
                                    key=f"p_{cat}_{col}",
                                )
                                new_important[col] = {
                                    "weight": w,
                                    "display_name": old.get("display_name", disp),
                                    "preference": pref,
                                }

                    st.divider()
                    submitted_cat = st.form_submit_button(
                        f"💾  保存【{cat}】规则配置",
                        type="primary",
                        use_container_width=True,
                    )

                if submitted_cat:
                    config["categories"][cat]["special_specs"] = new_special
                    config["categories"][cat]["important_specs"] = new_important
                    dm.save_config(config)
                    st.session_state.dm = dm
                    st.success(f"【{cat}】规则配置已保存！")
                    st.rerun()

                # ── Section C: 危险操作 ────────────────────────────────────────
                with st.expander("⚠️  危险操作", expanded=False):
                    st.warning("以下操作不可恢复，请谨慎！")
                    dc1, dc2 = st.columns(2)
                    with dc1:
                        if st.button(
                            f"🗑️  删除类别「{cat}」及全部产品",
                            use_container_width=True,
                            key=f"del_cat_{cat}",
                            type="secondary",
                        ):
                            st.session_state[f"confirm_del_{cat}"] = True
                    if st.session_state.get(f"confirm_del_{cat}"):
                        st.error(f"确认删除「{cat}」？此操作会删除配置和全部产品数据，无法恢复！")
                        cc1, cc2 = st.columns(2)
                        with cc1:
                            if st.button("✅  确认删除", key=f"confirm_del_yes_{cat}", type="primary"):
                                dm.delete_category(cat)
                                st.session_state.pop(f"confirm_del_{cat}", None)
                                st.session_state.dm = None
                                st.success(f"已删除类别「{cat}」。")
                                st.rerun()
                        with cc2:
                            if st.button("❌  取消", key=f"confirm_del_no_{cat}"):
                                st.session_state.pop(f"confirm_del_{cat}", None)
                                st.rerun()

        # ── New Category tab ───────────────────────────────────────────────────
        with cat_tabs[-2]:
            st.subheader("通过产品文档新增类别")
            st.caption(
                "上传 **2 个以上**属于同一新类别的产品彩页/规格文档，"
                "AI 自动推断类别名称和参数字典，确认后注册为新类别。"
            )

            if not st.session_state.api_key:
                st.warning("请先在侧边栏配置 API Key。")
            else:
                new_cat_files = st.file_uploader(
                    "上传产品文档（至少 2 个，同一类别）",
                    type=["pdf", "txt"],
                    accept_multiple_files=True,
                    key="new_cat_files",
                )

                can_infer = new_cat_files and len(new_cat_files) >= 2

                if new_cat_files and len(new_cat_files) < 2:
                    st.info("请至少上传 2 个文件，以便 AI 识别共性参数。")

                if can_infer:
                    st.caption(f"已选 {len(new_cat_files)} 个文件：" +
                               "、".join(f.name for f in new_cat_files))

                    if st.button("🤖  AI 推断类别和参数字典", type="primary", key="infer_schema_btn"):
                        import anthropic as _anthropic
                        _client = _anthropic.Anthropic(
                            api_key=st.session_state.api_key,
                            **({"base_url": st.session_state.base_url}
                               if st.session_state.base_url else {}),
                        )
                        doc_texts = []
                        prog = st.progress(0, text="读取文档...")
                        for idx, f in enumerate(new_cat_files):
                            prog.progress((idx + 1) / len(new_cat_files),
                                          text=f"读取 {f.name}...")
                            doc_texts.append(extract_text_from_file(f))

                        with st.spinner("AI 正在分析文档，推断参数字典..."):
                            infer_result = infer_category_schema_from_docs(
                                doc_texts, _client, st.session_state.model
                            )
                        prog.empty()
                        st.session_state["new_cat_infer"] = infer_result
                        st.rerun()

                infer_result = st.session_state.get("new_cat_infer")
                if infer_result:
                    infer_error = infer_result.get("error")
                    if infer_error:
                        st.warning(f"AI 解析可能不完整: {infer_error[:200]}")

                    suggested_name = infer_result.get("category_name", "")
                    suggested_desc = infer_result.get("category_desc", "")
                    suggested_schema = infer_result.get("param_schema", {})

                    st.success(f"AI 推断完成！建议类别名：**{suggested_name}**")
                    if suggested_desc:
                        st.caption(suggested_desc)

                    # Editable category name
                    final_name = st.text_input(
                        "类别名称（可修改）",
                        value=suggested_name,
                        key="new_cat_name_input",
                    )

                    # Preview inferred schema
                    if suggested_schema:
                        st.markdown("**AI 推断的参数字典（可在注册后于规则配置中编辑）**")
                        schema_rows = []
                        for pname, info in suggested_schema.items():
                            schema_rows.append({
                                "参数名": pname,
                                "列名": info.get("column", ""),
                                "类型": info.get("type", ""),
                                "单位": info.get("unit", ""),
                                "枚举值": ";".join(info.get("options", [])),
                            })
                        st.dataframe(
                            pd.DataFrame(schema_rows),
                            use_container_width=True,
                            hide_index=True,
                            height=min(38 + len(schema_rows) * 35, 400),
                        )

                    bc1, bc2 = st.columns([2, 1])
                    with bc1:
                        if st.button(
                            "✅  注册为新类别",
                            type="primary",
                            use_container_width=True,
                            key="register_new_cat",
                            disabled=not final_name.strip(),
                        ):
                            try:
                                new_cat_cfg = {
                                    "param_schema": suggested_schema,
                                    "special_specs": [],
                                    "important_specs": {},
                                }
                                dm.add_category(final_name.strip(), new_cat_cfg)
                                st.session_state["new_cat_infer"] = None
                                st.session_state.dm = None
                                st.success(
                                    f"类别「{final_name.strip()}」注册成功！"
                                    "请前往【产品库管理】导入产品数据，"
                                    "并在规则配置中设置筛选规则。"
                                )
                                st.rerun()
                            except Exception as e:
                                st.error(f"注册失败: {e}")
                    with bc2:
                        if st.button("✖  取消", use_container_width=True, key="cancel_new_cat"):
                            st.session_state["new_cat_infer"] = None
                            st.rerun()

        # ── Global settings tab ────────────────────────────────────────────────
        with cat_tabs[-1]:
            global_cfg = config.get("global", {})

            st.subheader("标签加成")
            st.caption("正数=加分，负数=扣分。对所有类别生效。")

            tag_bonuses_cfg = global_cfg.get("tag_bonuses", {})
            all_tags = dm.get_all_tags()
            tag_list = sorted(set(all_tags) | set(tag_bonuses_cfg.keys()))

            with st.form("global_form"):
                new_tag_bonuses: dict = {}
                if tag_list:
                    bonus_cols = st.columns(min(len(tag_list), 5))
                    for j, tag in enumerate(tag_list):
                        with bonus_cols[j % len(bonus_cols)]:
                            val = st.number_input(
                                f'"{tag}"',
                                value=float(tag_bonuses_cfg.get(tag, 0)),
                                step=1.0,
                                key=f"tb_{tag}",
                            )
                            new_tag_bonuses[tag] = val
                else:
                    st.info("产品库中暂无标签数据。")
                    new_tag_bonuses = tag_bonuses_cfg

                st.subheader("价格排序")
                price_sort = st.radio(
                    "同等评分下价格方向",
                    ["asc", "desc"],
                    index=0 if global_cfg.get("price_sort", "asc") == "asc" else 1,
                    format_func=lambda x: "低→高（性价比优先）" if x == "asc" else "高→低（高端产品优先）",
                    horizontal=True,
                )

                s_col, r_col = st.columns([2, 1])
                with s_col:
                    submitted_global = st.form_submit_button(
                        "💾  保存全局设置", type="primary", use_container_width=True
                    )
                with r_col:
                    reset_btn = st.form_submit_button("🔄  恢复默认配置", use_container_width=True)

            if submitted_global:
                config["global"]["tag_bonuses"] = new_tag_bonuses
                config["global"]["price_sort"] = price_sort
                dm.save_config(config)
                st.session_state.dm = dm
                st.success("全局配置已保存！")
                st.rerun()

            if reset_btn:
                dm.reset_config()
                st.session_state.dm = dm
                st.success("已恢复默认配置！")
                st.rerun()

            with st.expander("查看完整规则 JSON（只读）"):
                st.json(config)
