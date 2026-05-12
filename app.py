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
        # Clear previous search results before starting new search
        st.session_state.last_extracted = None
        st.session_state.last_results = None
        st.session_state.last_category = None

        st.session_state.last_input = customer_text
        dm = get_dm()
        dm._config = None  # Force fresh config read so Tab 3 rule changes take effect immediately
        config = dm.get_config()
        categories_config = config.get("categories", {})

        st.divider()

        # 创建主进度容器
        progress_container = st.container()

        with progress_container:
            # ========== Step 1: LLM 参数解析（两步识别） ==========
            st.markdown("#### 🧠 Step 1 · Claude 智能参数解析")

            # Step 1a: 类别识别
            step1a_status = st.status("**Step 1a · 产品类别识别**", state="running", expanded=True)

            try:
                step1a_status.write("📋 正在分析客户需求文本...")
                step1a_status.write(f"输入文本长度：{len(customer_text)} 字符")

                step1a_status.write("🔍 正在准备类别列表...")
                available_categories = list(categories_config.keys())
                step1a_status.write(f"可用类别：{', '.join(available_categories)}")

                mapper = LLMMapper(
                    st.session_state.api_key,
                    base_url=st.session_state.base_url or None,
                    model=st.session_state.model,
                )

                step1a_status.write(f"🤖 正在调用 LLM ({st.session_state.model})...")
                step1a_status.write("Prompt 构建完成：")
                step1a_status.code(
                    f"System: 产品类别识别专家\n"
                    f"User: 可选类别列表 + 客户需求描述\n"
                    f"Max Tokens: 200 (快速调用)",
                    language="text"
                )

                # 执行类别识别
                categories = available_categories
                cat_content = f"可选产品类别：\n{chr(10).join(f'- {c}' for c in categories)}\n\n客户需求描述：\n{customer_text}"

                step1a_status.write("正在发送请求到大模型...")
                import anthropic
                kwargs = {"api_key": st.session_state.api_key}
                if st.session_state.base_url:
                    kwargs["base_url"] = st.session_state.base_url

                client = anthropic.Anthropic(**kwargs)
                cat_msg = client.messages.create(
                    model=st.session_state.model,
                    max_tokens=200,
                    system="你是产品类别识别专家。根据客户的需求描述，判断最匹配的产品类别。",
                    messages=[{"role": "user", "content": cat_content}],
                )

                # 解析类别识别结果
                import json
                import re
                cat_text = ""
                for block in cat_msg.content:
                    if hasattr(block, "text"):
                        cat_text = block.text

                step1a_status.write(f"✅ 收到响应（{len(cat_text)} 字符）")

                # 显示原始响应内容（便于调试）
                with step1a_status.expander("查看 LLM 原始响应"):
                    st.code(cat_text, language="text")

                step1a_status.write("正在解析 JSON 结果...")

                # JSON 解析（增强容错）
                cat_text_clean = cat_text.strip()
                fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", cat_text_clean)
                if fence:
                    cat_text_clean = fence.group(1).strip()

                # 尝试多种解析方式
                category = None
                parse_error = None

                # 方法1：直接解析
                try:
                    cat_result = json.loads(cat_text_clean)
                    category = cat_result.get("category")
                    step1a_status.write(f"✅ JSON 解析成功（方法1：直接解析）")
                except json.JSONDecodeError as e1:
                    parse_error = str(e1)
                    step1a_status.write(f"⚠️ 方法1失败：{parse_error}")

                    # 方法2：提取 {...} 模式
                    m = re.search(r"\{[^{}]*\}", cat_text_clean, re.DOTALL)
                    if m:
                        try:
                            cat_result = json.loads(m.group())
                            category = cat_result.get("category")
                            step1a_status.write(f"✅ JSON 解析成功（方法2：正则提取）")
                        except json.JSONDecodeError as e2:
                            step1a_status.write(f"⚠️ 方法2失败：{str(e2)}")

                    # 方法3：尝试添加缺失的引号或括号
                    if not category:
                        # 尝试修复常见格式问题
                        fixed_text = cat_text_clean
                        if not fixed_text.startswith("{"):
                            fixed_text = "{" + fixed_text
                        if not fixed_text.endswith("}"):
                            fixed_text = fixed_text + "}"
                        if '"category"' not in fixed_text and 'category' in fixed_text:
                            fixed_text = fixed_text.replace('category', '"category"')

                        try:
                            cat_result = json.loads(fixed_text)
                            category = cat_result.get("category")
                            step1a_status.write(f"✅ JSON 解析成功（方法3：格式修复）")
                        except json.JSONDecodeError as e3:
                            step1a_status.write(f"⚠️ 方法3失败：{str(e3)}")

                # 最终结果处理
                if category and category in available_categories:
                    step1a_status.write(f"🎯 类别识别结果：{category}")
                    step1a_status.update(label=f"**✅ Step 1a 完成 · 类别识别成功：{category}**", state="complete", expanded=False)
                    st.success(f"🎯 **识别类别**：{category}")
                else:
                    step1a_status.write(f"⚠️ JSON 解析未成功，尝试文本提取...")

                    # 方法4：从纯文本中提取类别名称（兜底方案）
                    # 尝试匹配markdown格式：**类别名称**
                    md_match = re.search(r'\*\*(.+?)\*\*', cat_text)
                    if md_match:
                        potential_cat = md_match.group(1).strip()
                        step1a_status.write(f"发现 markdown 格式：**{potential_cat}**")
                        if potential_cat in available_categories:
                            category = potential_cat
                            step1a_status.write(f"✅ 从文本中提取类别成功（方法4：markdown匹配）")

                    # 方法5：直接匹配文本中的类别名称
                    if not category:
                        for avail_cat in available_categories:
                            if avail_cat in cat_text:
                                category = avail_cat
                                step1a_status.write(f"✅ 从文本中匹配类别成功（方法5：直接匹配）")
                                break

                    # 最终判断
                    if category and category in available_categories:
                        step1a_status.write(f"🎯 类别识别结果：{category}")
                        step1a_status.update(label=f"**✅ Step 1a 完成 · 类别识别成功：{category}**", state="complete", expanded=False)
                        st.success(f"🎯 **识别类别**：{category}")
                    else:
                        step1a_status.write(f"❌ 所有解析方法失败")
                        step1a_status.write(f"解析结果：{category if category else 'None'}")
                        step1a_status.write(f"原始响应：{cat_text}")

                        if category and category not in available_categories:
                            step1a_status.write(f"⚠️ 类别不在配置列表中：{category}")

                        step1a_status.update(label="**❌ Step 1a 失败 · 无法识别类别**", state="error", expanded=True)
                        st.error(f"未能识别产品类别。原始响应：`{cat_text}`")
                        extracted = None

            except Exception as e:
                step1a_status.update(label=f"**❌ Step 1a 失败 · {str(e)[:100]}**", state="error", expanded=True)
                st.error(f"类别识别失败: {e}")
                extracted = None

            # 如果类别识别成功，继续参数提取
            if category and category in available_categories:
                # Step 1b: 参数提取
                step1b_status = st.status("**Step 1b · 参数提取**", state="running", expanded=True)

                try:
                    step1b_status.write(f"📂 正在加载【{category}】参数字典...")
                    cat_cfg = categories_config[category]
                    param_schema = cat_cfg.get("param_schema", {})
                    step1b_status.write(f"参数字典包含 {len(param_schema)} 个字段")

                    # 构建参数字典字符串
                    param_dict_lines = []
                    for name, info in param_schema.items():
                        col = info.get("column", "")
                        unit = info.get("unit", "无") or "无"
                        default_op = info.get("default_operator", "=")
                        opts = info.get("options", [])
                        line = f"- 参数名: {name} | 列名: {col} | 单位: {unit} | 默认比较: {default_op}"
                        if opts:
                            line += f" | 枚举值: {'/'.join(opts)}"
                        param_dict_lines.append(line)

                    param_dict_str = chr(10).join(param_dict_lines)
                    step1b_status.write("参数字典构建完成")

                    with step1b_status.expander("查看参数字典详情"):
                        st.code(param_dict_str, language="text")

                    step1b_status.write(f"🤖 正在调用 LLM ({st.session_state.model})...")
                    step1b_status.write("Prompt 构建完成：")
                    step1b_status.code(
                        f"System: 产品参数解析专家\n"
                        f"User: 类别 + 参数字典 + 客户需求 + 转换规则\n"
                        f"Max Tokens: 2048 (详细调用)",
                        language="text"
                    )

                    step1b_status.write("正在发送请求到大模型...")

                    # 构建参数提取 prompt
                    param_content = f"""## 产品类别
{category}

## 该类别标准参数字典
{param_dict_str}

## 客户需求描述
{customer_text}

## 输出格式（严格JSON）
 {{
  "extracted_params": [
    {{
      "standard_name": "参数名称",
      "column_name": "对应列名",
      "value": 参数值,
      "operator": "运算符",
      "unit": "单位",
      "original_text": "原文片段",
      "confidence": 置信度
    }}
  ],
  "summary": "一句话总结"
}}

## 转换规则
1. 分辨率统一换算为MP
2. 温度统一摄氏度
3. 优先使用默认比较方式
4. 只提取匹配参数字典的参数"""

                    # 调用 LLM 参数提取
                    param_msg = client.messages.create(
                        model=st.session_state.model,
                        max_tokens=2048,
                        system="你是专业的产品参数解析专家。将客户需求转换为标准参数格式。",
                        messages=[{"role": "user", "content": param_content}],
                    )

                    # 解析参数提取结果
                    from core.llm_mapper import _parse_json_safe, _extract_text
                    param_text = _extract_text(param_msg)

                    step1b_status.write(f"✅ 收到响应（{len(param_text)} 字符）")

                    with step1b_status.expander("查看 LLM 原始响应"):
                        st.code(param_text, language="text")

                    step1b_status.write("正在解析 JSON 结果...")
                    result = _parse_json_safe(param_text)

                    # 解析失败时显示错误但不中断流程
                    if result.get("error"):
                        step1b_status.write(f"⚠️ JSON 解析遇到问题：{result['error'][:200]}")

                    extracted_params = result.get("extracted_params", [])
                    summary = result.get("summary", "")

                    step1b_status.write(f"✅ 成功提取 {len(extracted_params)} 个参数")

                    if extracted_params:
                        # 显示提取的参数摘要
                        params_summary = []
                        for p in extracted_params:
                            name = p.get("standard_name", "")
                            value = p.get("value", "")
                            op = p.get("operator", "")
                            params_summary.append(f"{name} {op} {value}")

                        step1b_status.write("提取参数摘要：")
                        step1b_status.code(chr(10).join(params_summary), language="text")

                    step1b_status.update(label=f"**✅ Step 1b 完成 · 参数提取成功（{len(extracted_params)} 个参数）**", state="complete", expanded=False)

                    # 组合完整的 extracted 结果
                    extracted = {
                        "category": category,
                        "extracted_params": extracted_params,
                        "summary": summary
                    }
                    st.session_state.last_extracted = extracted
                    st.session_state.last_category = category

                    # 显示提取结果摘要
                    st.info(f"**需求摘要**：{summary}")

                except Exception as e:
                    step1b_status.update(label=f"**❌ Step 1b 失败 · {str(e)[:100]}**", state="error", expanded=True)
                    st.error(f"参数提取失败: {e}")
                    extracted = None

            # ========== Step 2: 规则引擎筛选与评分 ==========
            if extracted and category:
                st.markdown("#### ⚙️ Step 2 · 规则引擎筛选与评分")

                step2_status = st.status("**Step 2 · 规则引擎处理**", state="running", expanded=True)

                try:
                    step2_status.write(f"📊 正在从数据库加载【{category}】产品...")
                    products = dm.get_products(category)

                    if products.empty:
                        step2_status.update(label="**⚠️ Step 2 警告 · 产品库为空**", state="complete", expanded=False)
                        st.warning(f"【{category}】产品库为空，请先导入产品数据。")
                    else:
                        step2_status.write(f"✅ 加载 {len(products)} 款产品")

                        # 显示产品统计
                        with step2_status.expander("查看产品库统计"):
                            st.dataframe(
                                products[["product_id", "product_name", "price"]].head(10),
                                use_container_width=True,
                                hide_index=True
                            )

                        step2_status.write("🔧 正在初始化规则引擎...")
                        cat_cfg = dm.get_category_config(category)
                        global_cfg = dm.get_global_config()
                        engine = RuleEngine(cat_cfg, global_cfg)

                        step2_status.write("正在应用规则过滤和评分...")

                        # 显示规则配置摘要
                        special_specs = cat_cfg.get("special_specs", [])
                        important_specs = cat_cfg.get("important_specs", {})

                        step2_status.write(f"特殊规格过滤：{len(special_specs)} 个字段")
                        step2_status.code(f"字段：{', '.join(special_specs)}", language="text")

                        step2_status.write(f"重要规格评分：{len(important_specs)} 个字段")
                        weights_info = []
                        for col, cfg in important_specs.items():
                            weights_info.append(f"{col}: 权重 {cfg.get('weight', 0)}")
                        step2_status.code(chr(10).join(weights_info), language="text")

                        # 执行规则引擎
                        results = engine.filter_and_score(products, extracted)
                        st.session_state.last_results = results

                        # 统计结果
                        passed = results[results["_pass"]]
                        failed = results[~results["_pass"]]

                        step2_status.write(f"✅ 筛选完成")
                        step2_status.write(f"通过筛选：{len(passed)} 款")
                        step2_status.write(f"被过滤：{len(failed)} 款")

                        if len(passed) > 0:
                            top_score = passed.iloc[0]["_total_score"]
                            top_product = passed.iloc[0].get("product_name", "")
                            step2_status.write(f"🏆 最高得分：{top_score:.1f} 分 - {top_product}")

                        step2_status.update(label=f"**✅ Step 2 完成 · 推荐 {len(passed)} 款产品**", state="complete", expanded=False)

                        # 渲染结果
                        render_results(results, extracted, global_cfg)

                except Exception as e:
                    step2_status.update(label=f"**❌ Step 2 失败 · {str(e)[:100]}**", state="error", expanded=True)
                    st.error(f"规则引擎处理失败: {e}")

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

                # ── Section C: 配置导出 ────────────────────────────────────────
                with st.expander("📦  配置导出与导入", expanded=False):
                    st.caption(
                        "导出当前类别的完整筛选规则配置（包含参数字典、特殊规格、重要规格权重），"
                        "用于备份或导入到正式环境。"
                    )

                    ec1, ec2 = st.columns(2)
                    with ec1:
                        # 导出按钮
                        cat_full_config = config.get("categories", {}).get(cat, {})
                        export_data = {
                            "category_name": cat,
                            "export_time": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "config": cat_full_config,
                            "export_notes": "此文件包含完整的产品筛选规则配置，可导入到正式环境使用。"
                        }
                        export_json = json.dumps(export_data, ensure_ascii=False, indent=2)
                        export_bytes = export_json.encode("utf-8")
                        export_filename = f"config_{cat}_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.json"

                        st.download_button(
                            f"⬇️  导出【{cat}】完整配置",
                            export_bytes,
                            export_filename,
                            "application/json",
                            key=f"export_config_{cat}",
                            use_container_width=True,
                            type="primary",
                        )

                        st.caption(f"导出内容包含：")
                        st.markdown("- ✅ 参数字典（param_schema）")
                        st.markdown("- ✅ 特殊规格列表（special_specs）")
                        st.markdown("- ✅ 重要规格权重（important_specs）")
                        st.markdown("- ✅ 显示名称、单位、枚举值等元数据")

                    with ec2:
                        st.markdown("**导入配置（其他环境）**")
                        st.caption(
                            "将导出的 JSON 文件导入到正式环境的规则配置页面，"
                            "覆盖当前类别的配置。"
                        )

                        # 导入文件上传
                        import_file = st.file_uploader(
                            "选择配置 JSON 文件",
                            type=["json"],
                            key=f"import_config_{cat}",
                            help="上传从其他环境导出的配置文件",
                        )

                        if import_file:
                            try:
                                import_content = import_file.read().decode("utf-8")
                                import_data = json.loads(import_content)

                                # 验证导入数据结构
                                if "config" not in import_data:
                                    st.error("导入文件格式不正确：缺少 config 字段")
                                elif "category_name" not in import_data:
                                    st.error("导入文件格式不正确：缺少 category_name 字段")
                                else:
                                    imported_cat = import_data.get("category_name")
                                    imported_config = import_data.get("config", {})

                                    # 显示导入预览
                                    st.success(f"✅ 文件验证通过：类别【{imported_cat}】")
                                    st.caption(f"导出时间：{import_data.get('export_time', '未知')}")

                                    with st.container():
                                        st.markdown("**导入内容预览：**")

                                        # 参数字典预览
                                        imported_schema = imported_config.get("param_schema", {})
                                        st.markdown(f"- 参数字典：{len(imported_schema)} 个参数")
                                        if imported_schema:
                                            param_preview = pd.DataFrame([
                                                {
                                                    "参数名": pname,
                                                    "列名": info.get("column", ""),
                                                    "类型": info.get("type", ""),
                                                    "单位": info.get("unit", ""),
                                                    "枚举值": ";".join(info.get("options", []))
                                                }
                                                for pname, info in imported_schema.items()
                                            ])
                                            st.dataframe(param_preview, use_container_width=True, hide_index=True)

                                        # 特殊规格预览
                                        imported_special = imported_config.get("special_specs", [])
                                        st.markdown(f"- 特殊规格：{len(imported_special)} 个列")
                                        if imported_special:
                                            st.code(", ".join(imported_special), language="text")

                                        # 重要规格预览
                                        imported_important = imported_config.get("important_specs", {})
                                        st.markdown(f"- 重要规格：{len(imported_important)} 个列")
                                        if imported_important:
                                            imp_preview = pd.DataFrame([
                                                {
                                                    "列名": col,
                                                    "权重": info.get("weight", 0),
                                                    "偏好": info.get("preference", ">=")
                                                }
                                                for col, info in imported_important.items()
                                            ])
                                            st.dataframe(imp_preview, use_container_width=True, hide_index=True)

                                    # 导入确认
                                    st.warning(
                                        "⚠️ 导入将覆盖当前类别的所有配置！建议先导出当前配置作为备份。"
                                    )

                                    apply_col, cancel_col = st.columns(2)
                                    with apply_col:
                                        if st.button(
                                            "✅  应用导入配置",
                                            type="primary",
                                            use_container_width=True,
                                            key=f"apply_import_{cat}",
                                        ):
                                            # 应用导入的配置到当前类别
                                            config["categories"][cat] = imported_config
                                            dm.save_config(config)

                                            # 更新数据库表结构（同步新增列）
                                            dm.update_param_schema(cat, imported_config.get("param_schema", {}))

                                            st.session_state.dm = dm
                                            st.success(
                                                f"✅ 【{cat}】配置已成功导入！包含 {len(imported_schema)} 个参数，"
                                                f"{len(imported_special)} 个特殊规格，{len(imported_important)} 个重要规格。"
                                            )
                                            st.rerun()

                                    with cancel_col:
                                        if st.button(
                                            "❌  取消导入",
                                            use_container_width=True,
                                            key=f"cancel_import_{cat}",
                                        ):
                                            st.session_state.pop(f"import_config_{cat}", None)
                                            st.rerun()

                            except json.JSONDecodeError:
                                st.error("导入文件格式错误：JSON 解析失败")
                            except Exception as e:
                                st.error(f"导入失败：{e}")

                        # 导入说明
                        with st.popover("📖 查看导入步骤"):
                            st.markdown("""
                            **在正式环境导入步骤：**

                            1. 将导出的 JSON 文件传输到正式环境服务器
                            2. 在正式环境打开本应用 → 规则配置 → 选择对应类别
                            3. 展开【配置导出与导入】区域
                            4. 点击【导入配置】按钮，选择 JSON 文件
                            5. 预览配置内容，确认无误后点击【应用配置】

                            **注意事项：**
                            - 导入会覆盖当前类别的所有规则配置
                            - 请在导入前备份当前配置（先导出再导入）
                            - 导入不会影响产品数据，仅更新筛选规则
                            - 导入会自动同步数据库表结构（新增列）
                            """)

                st.divider()

                # ── Section D: 危险操作 ────────────────────────────────────────
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
