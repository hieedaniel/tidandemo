import json
import re
from io import BytesIO
from typing import Optional


_DOC_SYSTEM = """你是专业的产品规格提取专家。从产品彩页/规格文档中提取所有产品型号的完整参数。
严格按JSON格式输出，不含任何其他内容。"""

_SCHEMA_SYSTEM = """你是产品数据库设计专家。根据多份产品文档，推断产品类别名称和完整的参数字典结构。
严格按JSON格式输出，不含任何其他内容。"""

_SCHEMA_PROMPT = """## 任务
分析以下产品文档内容，推断出：
1. 产品类别名称（简洁中文，如"工业相机"、"激光测距仪"）
2. 该类别所有产品共有的参数字典

## 产品文档内容（来自 {file_count} 份文档）
{doc_texts}

## 输出格式（严格JSON）
{{
  "category_name": "类别名称（简洁中文，不超过6字）",
  "category_desc": "类别简介（一句话）",
  "param_schema": {{
    "参数中文名": {{
      "column": "英文下划线列名（全小写，如 resolution_mp）",
      "type": "numeric 或 text 或 enum",
      "unit": "单位（无则填空字符串）",
      "options": ["枚举值1", "枚举值2"],
      "hint": "简短说明（可选）"
    }}
  }}
}}

## 设计规则
1. column 命名：全小写英文 + 下划线，简洁且语义明确（如 screen_size_inch、temp_min）
2. 数值型参数（尺寸/温度/重量/功率等）→ type=numeric，unit 填对应单位
3. 有固定枚举值（接口类型/防护等级/颜色等）→ type=enum，options 列举常见值
4. 纯文本描述 → type=text
5. options 字段：type=enum 时必填，type=numeric/text 时填 []
6. 必须包含 price 参数：{{"column":"price","type":"numeric","unit":"元","options":[]}}
7. 参数数量建议 8~20 个，只保留产品间有差异的关键规格"""

_DOC_PROMPT = """## 目标产品类别
{category}

## 参数字典（column_name 是数据库列名，必须原样用作 JSON key）
{param_dict}

## 产品文档内容
{doc_text}

## 输出格式（严格JSON）
{{
  "products": [
    {{
      "product_id": "产品型号编码（如 IPC-L2A4-IR-F40，必填）",
      "product_name": "完整产品名称（必填）",
      "tags": "标签，分号分隔（如 新品;现货），没有则填空字符串",
      "price": null,
{param_columns_hint}
    }}
  ],
  "doc_summary": "文档简述（一句话）"
}}

## 提取规则
1. 每个独立型号为一条记录；焦距/规格不同的视为不同型号
2. 所有列名必须使用参数字典中的 column_name（英文下划线格式，不要用中文参数名）
3. 数值型参数只填纯数字（不含单位），枚举/文字型填字符串，文档未提及的填 null
4. 分辨率换算为百万像素（MP）：200万→2.0，400万→4.0，800万→8.0，1200万→12.0
5. 温度：最低工作温度→temp_min，最高工作温度→temp_max（填数字，不含℃符号）
6. price：有明确价格则填数字，否则 null"""


def extract_text_from_file(uploaded_file) -> str:
    """Extract plain text from an uploaded PDF or TXT file."""
    name = uploaded_file.name.lower()
    raw = uploaded_file.read()

    if name.endswith(".pdf"):
        text = _extract_pdf_text(raw)
    else:
        text = raw.decode("utf-8", errors="replace")

    # Keep first 6000 + last 4000 chars — covers cover page + spec table
    if len(text) > 12000:
        text = text[:6000] + "\n…（中间内容已省略）…\n" + text[-4000:]

    return text


def _extract_pdf_text(content: bytes) -> str:
    try:
        import pdfplumber
    except ImportError as exc:
        raise ImportError("请先安装 pdfplumber：pip install pdfplumber") from exc

    parts: list[str] = []
    with pdfplumber.open(BytesIO(content)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            if page_text:
                parts.append(page_text)

            # Extract tables (spec sheets often use tables)
            for table in page.extract_tables() or []:
                for row in table:
                    if row:
                        row_str = " | ".join(str(c).strip() for c in row if c is not None and str(c).strip())
                        if row_str:
                            parts.append(row_str)

    return "\n".join(parts)


def extract_products_from_doc(
    doc_text: str,
    category: str,
    param_schema: dict,
    llm_client,
    model: str,
) -> dict:
    """Send document text to LLM and return structured product list."""
    from core.llm_mapper import _build_param_dict_str, _parse_json_safe, _extract_text

    param_dict_str = _build_param_dict_str(param_schema)

    # Build per-column output hints for the prompt
    col_hints: list[str] = []
    for param_name, info in param_schema.items():
        col = info.get("column", "")
        typ = info.get("type", "text")
        opts = info.get("options", [])
        if not col or col in ("product_id", "product_name", "tags", "price"):
            continue
        if typ == "numeric":
            hint = "数字或null"
        elif opts:
            hint = f'"{opts[0]}" 或 {"/".join(repr(o) for o in opts[1:])} 或null'
        else:
            hint = "字符串或null"
        col_hints.append(f'      "{col}": {hint}')

    param_columns_hint = ",\n".join(col_hints)

    prompt = _DOC_PROMPT.format(
        category=category,
        param_dict=param_dict_str,
        doc_text=doc_text,
        param_columns_hint=param_columns_hint,
    )

    msg = llm_client.messages.create(
        model=model,
        max_tokens=4096,
        system=_DOC_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    return _parse_json_safe(_extract_text(msg))


def infer_category_schema_from_docs(
    doc_texts: list[str],
    llm_client,
    model: str,
) -> dict:
    """Infer a new category name + param_schema from 2+ product documents.

    Returns dict with keys: category_name, category_desc, param_schema.
    """
    from core.llm_mapper import _parse_json_safe, _extract_text

    combined = ""
    for i, text in enumerate(doc_texts, 1):
        combined += f"\n\n--- 文档 {i} ---\n{text[:4000]}"

    prompt = _SCHEMA_PROMPT.format(
        file_count=len(doc_texts),
        doc_texts=combined.strip(),
    )

    msg = llm_client.messages.create(
        model=model,
        max_tokens=4096,
        system=_SCHEMA_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    return _parse_json_safe(_extract_text(msg))
