import anthropic
import json
import re
from typing import Optional


_CAT_SYSTEM = """你是产品类别识别专家。根据客户的需求描述，判断最匹配的产品类别。
严格输出JSON，不含任何其他内容。"""

_CAT_PROMPT = """可选产品类别：
{categories}

客户需求描述：
{customer_text}

输出（严格JSON）：
{{"category": "最匹配的类别名称，若完全无法判断则为null"}}"""


_PARAM_SYSTEM = """你是专业的产品参数解析专家。将客户需求转换为标准参数格式。
严格按JSON格式输出，不含任何其他内容。"""

_PARAM_PROMPT = """## 产品类别
{category}

## 该类别标准参数字典
{param_dict}

## 客户需求描述
{customer_text}

## 输出格式（严格JSON）
{{
  "extracted_params": [
    {{
      "standard_name": "参数名称（必须从参数字典中选择）",
      "column_name": "对应列名",
      "value": 参数值（数字用数字类型，文字用字符串类型）,
      "operator": "运算符: = / >= / <= / > / < / contains",
      "unit": "单位",
      "original_text": "客户原文中对应的描述片段",
      "confidence": 置信度（0.0~1.0的数字）
    }}
  ],
  "summary": "一句话总结客户核心需求"
}}

## 转换规则
1. 分辨率统一换算为百万像素(MP)：4K≈8.3MP，400万=4MP，200万=2MP，1080P≈2MP
2. 温度统一摄氏度：最低工作温度用<=，最高工作温度用>=
3. 客户说"至少/不低于/≥"用>=；"不超过/最大/≤"用<=；"必须是/等于"用=
4. **优先使用参数字典中标注的【默认比较方式】，除非客户明确要求其他运算符**
5. 只提取能匹配到参数字典的参数，column_name必须使用参数字典中标注的列名值"""


class LLMMapper:
    def __init__(self, api_key: str, base_url: Optional[str] = None, model: str = "claude-sonnet-4-6"):
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = anthropic.Anthropic(**kwargs)
        self.model = model

    def extract_params(self, customer_text: str, categories_config: dict) -> dict:
        """Two-step extraction: (1) identify category, (2) extract params with category schema."""
        categories = list(categories_config.keys())

        # Step 1: category identification
        cat_content = _CAT_PROMPT.format(
            categories="\n".join(f"- {c}" for c in categories),
            customer_text=customer_text,
        )
        try:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                system=_CAT_SYSTEM,
                messages=[{"role": "user", "content": cat_content}],
            )
            cat_result = _parse_json_safe(_extract_text(msg))
            category = cat_result.get("category")
        except Exception as e:
            return {
                "category": None,
                "extracted_params": [],
                "summary": f"类别识别失败: {e}",
                "error": str(e),
            }

        if category not in categories_config:
            category = None

        if not category:
            return {
                "category": None,
                "extracted_params": [],
                "summary": "无法识别产品类别，请在需求描述中明确产品类型",
            }

        # Step 2: parameter extraction with category-specific schema
        cat_cfg = categories_config[category]
        param_schema = cat_cfg.get("param_schema", {})
        param_dict_str = _build_param_dict_str(param_schema)

        param_content = _PARAM_PROMPT.format(
            category=category,
            param_dict=param_dict_str,
            customer_text=customer_text,
        )
        try:
            msg2 = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=_PARAM_SYSTEM,
                messages=[{"role": "user", "content": param_content}],
            )
            result = _parse_json_safe(_extract_text(msg2))
        except Exception as e:
            result = {
                "extracted_params": [],
                "summary": f"参数提取失败: {e}",
                "error": str(e),
            }

        result["category"] = category
        return result


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_param_dict_str(param_schema: dict) -> str:
    lines = []
    for name, info in param_schema.items():
        col = info.get("column", "")
        unit = info.get("unit", "无") or "无"
        hint = info.get("hint", "")
        opts = info.get("options", [])
        default_op = info.get("default_operator", "=")
        line = f"- 参数名: {name} | 列名: {col} | 单位: {unit} | 默认比较: {default_op}"
        if opts:
            line += f" | 枚举值: {'/'.join(opts)}"
        if hint:
            line += f" | 说明: {hint}"
        lines.append(line)
    return "\n".join(lines)


def _extract_text(message) -> str:
    """Return the text from the first TextBlock, skipping ThinkingBlock etc."""
    for block in message.content:
        if hasattr(block, "text"):
            return block.text
    return ""


def _parse_json_safe(text: str) -> dict:
    text = text.strip()

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Greedy: outermost { ... }
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    return {
        "category": None,
        "extracted_params": [],
        "summary": "参数解析失败，请检查输入内容或 API Key",
        "error": text[:300],
    }
