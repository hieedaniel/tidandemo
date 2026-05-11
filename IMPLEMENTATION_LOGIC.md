# 智能产品检索系统实现逻辑详解

## 一、系统架构概览

### 1.1 核心模块

```
智能产品检索系统
├── Streamlit Web 应用 (app.py)
│   ├── Tab 1: 智能检索
│   ├── Tab 2: 产品库管理
│   └── Tab 3: 规则配置
│
├── 核心引擎层 (core/)
│   ├── LLMMapper - 大模型参数提取（两步调用）
│   ├── RuleEngine - 四层规则引擎（筛选+评分）
│   ├── DataManager - SQLite 数据库管理
│   └── DocExtractor - 产品文档智能提取
│
└── 数据层 (data/)
    ├── products.db - SQLite 数据库（按类别分表）
    ├── default_config.json - 默认配置（版本控制）
    └── config.json - 用户配置覆盖
```

### 1.2 完整业务流程

```
用户输入客户需求文本
    ↓
[Step 1] LLMMapper 两步提取
    ├─ Step 1a: 类别识别（快速调用）
    │   └─ 输出: {"category": "工业相机"}
    │
    └─ Step 1b: 参数提取（详细调用）
        └─ 输出: {
            "extracted_params": [
                {"column_name": "resolution_mp", "value": 5, "operator": ">="}
            ],
            "summary": "需要高分辨率工业相机..."
        }
    ↓
[Step 2] DataManager 数据查询
    └─ 根据 category 从对应表中加载产品 DataFrame
    ↓
[Step 3] RuleEngine 四层筛选评分
    ├─ Layer 2: 特殊规格硬性过滤（veto）
    ├─ Layer 3: 重要规格加权评分（0-100分）
    ├─ Layer 4: 标签奖励加成（±5分）
    └─ Layer 5: 价格排序（升降序）
    ↓
输出：推荐产品列表（含评分明细）
```

---

## 二、大模型调用详解（所有 Prompt）

### 2.1 LLMMapper - 类别识别调用

#### **调用场景**
用户提交需求文本后，第一步快速识别产品类别。

#### **调用参数**
```python
client.messages.create(
    model="glm-5",  # 或其他模型
    max_tokens=200,  # 快速调用，限制输出长度
    system=_CAT_SYSTEM,
    messages=[{"role": "user", "content": cat_content}]
)
```

#### **System Prompt**
```
你是产品类别识别专家。根据客户的需求描述，判断最匹配的产品类别。
严格输出JSON，不含任何其他内容。
```

#### **User Prompt 模板**
```
可选产品类别：
- 工业相机
- 线扫相机
- 3D相机
- 网络摄像机
- 信息发布屏

客户需求描述：
需要一款彩色工业相机，分辨率不低于500万像素，帧率至少25fps...

输出（严格JSON）：
{"category": "最匹配的类别名称，若完全无法判断则为null"}
```

#### **输出解析**
```json
{
  "category": "工业相机"
}
```

**失败处理：**
- JSON 解析失败 → 返回 `{"category": null, "summary": "类别识别失败: ..."}`
- 类别不在配置列表 → 返回 `{"category": null, "summary": "无法识别产品类别..."}`

---

### 2.2 LLMMapper - 参数提取调用

#### **调用场景**
类别识别成功后，根据该类别的参数字典（param_schema）详细提取需求参数。

#### **调用参数**
```python
client.messages.create(
    model="glm-5",
    max_tokens=2048,  # 详细调用，允许较长输出
    system=_PARAM_SYSTEM,
    messages=[{"role": "user", "content": param_content}]
)
```

#### **System Prompt**
```
你是专业的产品参数解析专家。将客户需求转换为标准参数格式。
严格按JSON格式输出，不含任何其他内容。
```

#### **User Prompt 模板**
```
## 产品类别
工业相机

## 该类别标准参数字典
- 参数名: 分辨率 | 列名: resolution_mp | 单位: MP | 默认比较: >= | 说明: 统一换算为百万像素(MP)
- 参数名: 帧率 | 列名: frame_rate | 单位: fps | 默认比较: >=
- 参数名: 接口类型 | 列名: interface | 单位: 无 | 默认比较: = | 枚举值: USB3.0/GigE/Camera Link/CoaXPress
- 参数名: 快门类型 | 列名: shutter_type | 单位: 无 | 默认比较: = | 枚举值: 全局/卷帘/滚动
- 参数名: 最低工作温度 | 列名: temp_min | 单位: ℃ | 默认比较: <=
- 参数名: 最高工作温度 | 列名: temp_max | 单位: ℃ | 默认比较: >=
- 参数名: 防护等级 | 列名: protection_level | 单位: 无 | 默认比较: = | 枚举值: IP40/IP54/IP67/IP68

## 客户需求描述
需要一款彩色工业相机，分辨率不低于500万像素，帧率至少25fps，
接口要求GigE，需要全局快门，工作温度-20到70度，防护等级IP67

## 输出格式（严格JSON）
{
  "extracted_params": [
    {
      "standard_name": "参数名称（必须从参数字典中选择）",
      "column_name": "对应列名",
      "value": 参数值（数字用数字类型，文字用字符串类型）,
      "operator": "运算符: = / >= / <= / > / < / contains",
      "unit": "单位",
      "original_text": "客户原文中对应的描述片段",
      "confidence": 置信度（0.0~1.0的数字）
    }
  ],
  "summary": "一句话总结客户核心需求"
}

## 转换规则
1. 分辨率统一换算为百万像素(MP)：4K≈8.3MP，400万=4MP，200万=2MP，1080P≈2MP
2. 温度统一摄氏度：最低工作温度用<=，最高工作温度用>=
3. 客户说"至少/不低于/≥"用>=；"不超过/最大/≤"用<=；"必须是/等于"用=
4. **优先使用参数字典中标注的【默认比较方式】，除非客户明确要求其他运算符**
5. 只提取能匹配到参数字典的参数，column_name必须使用参数字典中标注的列名值
```

#### **输出示例**
```json
{
  "extracted_params": [
    {
      "standard_name": "分辨率",
      "column_name": "resolution_mp",
      "value": 5.0,
      "operator": ">=",
      "unit": "MP",
      "original_text": "分辨率不低于500万像素",
      "confidence": 0.95
    },
    {
      "standard_name": "帧率",
      "column_name": "frame_rate",
      "value": 25,
      "operator": ">=",
      "unit": "fps",
      "original_text": "帧率至少25fps",
      "confidence": 0.92
    },
    {
      "standard_name": "接口类型",
      "column_name": "interface",
      "value": "GigE",
      "operator": "=",
      "unit": "",
      "original_text": "接口要求GigE",
      "confidence": 0.98
    },
    {
      "standard_name": "快门类型",
      "column_name": "shutter_type",
      "value": "全局",
      "operator": "=",
      "unit": "",
      "original_text": "需要全局快门",
      "confidence": 0.96
    },
    {
      "standard_name": "最低工作温度",
      "column_name": "temp_min",
      "value": -20,
      "operator": "<=",
      "unit": "℃",
      "original_text": "工作温度-20到70度",
      "confidence": 0.88
    },
    {
      "standard_name": "最高工作温度",
      "column_name": "temp_max",
      "value": 70,
      "operator": ">=",
      "unit": "℃",
      "original_text": "工作温度-20到70度",
      "confidence": 0.88
    },
    {
      "standard_name": "防护等级",
      "column_name": "protection_level",
      "value": "IP67",
      "operator": "=",
      "unit": "",
      "original_text": "防护等级IP67",
      "confidence": 0.97
    }
  ],
  "summary": "需要高分辨率彩色工业相机，GigE接口，全局快门，IP67防护"
}
```

---

### 2.3 DocExtractor - 产品文档智能提取

#### **调用场景**
用户在"产品库管理"中上传产品彩页/规格书 PDF，批量提取产品参数。

#### **调用参数**
```python
client.messages.create(
    model="glm-5",
    max_tokens=4096,  # 文档提取需要更长输出
    system=_DOC_SYSTEM,
    messages=[{"role": "user", "content": doc_prompt}]
)
```

#### **System Prompt**
```
你是专业的产品规格提取专家。从产品彩页/规格文档中提取所有产品型号的完整参数。
严格按JSON格式输出，不含任何其他内容。
```

#### **User Prompt 模板**
```
## 目标产品类别
工业相机

## 参数字典（column_name 是数据库列名，必须原样用作 JSON key）
- 参数名: 分辨率 | 列名: resolution_mp | 单位: MP | 默认比较: >=
- 参数名: 帧率 | 列名: frame_rate | 单位: fps | 默认比较: >=
- 参数名: 接口类型 | 列名: interface | 单位: 无 | 枚举值: USB3.0/GigE/Camera Link/CoaXPress
- 参数名: 快门类型 | 列名: shutter_type | 单位: 无 | 枚举值: 全局/卷帘/滚动
- 参数名: 感光元件 | 列名: sensor_type | 单位: 无 | 枚举值: CMOS/CCD
- 参数名: 色彩模式 | 列名: color_mode | 单位: 无 | 枚举值: 彩色/黑白
- 参数名: 像素尺寸 | 列名: pixel_size_um | 单位: μm
- 参数名: 最低工作温度 | 列名: temp_min | 单位: ℃ | 默认比较: <=
- 参数名: 最高工作温度 | 列名: temp_max | 单位: ℃ | 默认比较: >=
- 参数名: 防护等级 | 列名: protection_level | 单位: 无 | 枚举值: IP40/IP54/IP67/IP68
- 参数名: 价格 | 列名: price | 单位: 元

## 产品文档内容
UNV 工业相机 IPC-L2A4-IR-F40
分辨率：400万像素（4MP）
帧率：25fps
接口类型：GigE
感光元件：CMOS
快门类型：全局快门
色彩模式：彩色
像素尺寸：5.5μm
工作温度：-20℃ ~ 60℃
防护等级：IP67
价格：¥5500
...

## 输出格式（严格JSON）
{
  "products": [
    {
      "product_id": "产品型号编码（如 IPC-L2A4-IR-F40，必填）",
      "product_name": "完整产品名称（必填）",
      "tags": "标签，分号分隔（如 新品;现货），没有则填空字符串",
      "price": null,
      "resolution_mp": 数字或null,
      "frame_rate": 数字或null,
      "interface": "GigE" 或 "USB3.0"/"Camera Link"/"CoaXPress" 或null,
      "shutter_type": "全局" 或 "卷帘"/"滚动" 或null,
      "sensor_type": "CMOS" 或 "CCD" 或null,
      "color_mode": "彩色" 或 "黑白" 或null,
      "pixel_size_um": 数字或null,
      "temp_min": 数字或null,
      "temp_max": 数字或null,
      "protection_level": "IP67" 或 "IP40"/"IP54"/"IP68" 或null
    }
  ],
  "doc_summary": "文档简述（一句话）"
}

## 提取规则
1. 每个独立型号为一条记录；焦距/规格不同的视为不同型号
2. 所有列名必须使用参数字典中的 column_name（英文下划线格式，不要用中文参数名）
3. 数值型参数只填纯数字（不含单位），枚举/文字型填字符串，文档未提及的填 null
4. 分辨率换算为百万像素（MP）：200万→2.0，400万→4.0，800万→8.0，1200万→12.0
5. 温度：最低工作温度→temp_min，最高工作温度→temp_max（填数字，不含℃符号）
6. price：有明确价格则填数字，否则 null
```

#### **输出示例**
```json
{
  "products": [
    {
      "product_id": "IPC-L2A4-IR-F40",
      "product_name": "UNV 工业相机 IPC-L2A4-IR 400万像素 GigE接口",
      "tags": "新品",
      "price": 5500,
      "resolution_mp": 4.0,
      "frame_rate": 25,
      "interface": "GigE",
      "shutter_type": "全局",
      "sensor_type": "CMOS",
      "color_mode": "彩色",
      "pixel_size_um": 5.5,
      "temp_min": -20,
      "temp_max": 60,
      "protection_level": "IP67"
    }
  ],
  "doc_summary": "工业相机产品规格表，包含多个型号"
}
```

---

### 2.4 DocExtractor - 新类别推断调用

#### **调用场景**
用户上传的产品文档不属于现有类别，需要从文档中推断新的类别名称和参数结构。

#### **调用参数**
```python
client.messages.create(
    model="glm-5",
    max_tokens=4096,
    system=_SCHEMA_SYSTEM,
    messages=[{"role": "user", "content": schema_prompt}]
)
```

#### **System Prompt**
```
你是产品数据库设计专家。根据多份产品文档，推断产品类别名称和完整的参数字典结构。
严格按JSON格式输出，不含任何其他内容。
```

#### **User Prompt 模板**
```
## 任务
分析以下产品文档内容，推断出：
1. 产品类别名称（简洁中文，如"工业相机"、"激光测距仪"）
2. 该类别所有产品共有的参数字典

## 产品文档内容（来自 3 份文档）

--- 文档 1 ---
激光测距仪 LRF-500
测量范围：0-500米
精度：±2mm
激光波长：905nm
防护等级：IP65
工作温度：-10℃ ~ 50℃
...

--- 文档 2 ---
激光测距仪 LRF-1000
测量范围：0-1000米
精度：±1mm
...

--- 文档 3 ---
...

## 输出格式（严格JSON）
{
  "category_name": "类别名称（简洁中文，不超过6字）",
  "category_desc": "类别简介（一句话）",
  "param_schema": {
    "参数中文名": {
      "column": "英文下划线列名（全小写，如 resolution_mp）",
      "type": "numeric 或 text 或 enum",
      "unit": "单位（无则填空字符串）",
      "options": ["枚举值1", "枚举值2"],
      "hint": "简短说明（可选）"
    }
  }
}

## 设计规则
1. column 命名：全小写英文 + 下划线，简洁且语义明确（如 screen_size_inch、temp_min）
2. 数值型参数（尺寸/温度/重量/功率等）→ type=numeric，unit 填对应单位
3. 有固定枚举值（接口类型/防护等级/颜色等）→ type=enum，options 列举常见值
4. 纯文本描述 → type=text
5. options 字段：type=enum 时必填，type=numeric/text 时填 []
6. 必须包含 price 参数：{"column":"price","type":"numeric","unit":"元","options":[]}
7. 参数数量建议 8~20 个，只保留产品间有差异的关键规格
```

#### **输出示例**
```json
{
  "category_name": "激光测距仪",
  "category_desc": "高精度激光距离测量设备",
  "param_schema": {
    "测量范围": {
      "column": "measure_range_m",
      "type": "numeric",
      "unit": "米",
      "options": [],
      "hint": "最大测量距离"
    },
    "精度": {
      "column": "accuracy_mm",
      "type": "numeric",
      "unit": "mm",
      "options": []
    },
    "激光波长": {
      "column": "laser_wavelength_nm",
      "type": "numeric",
      "unit": "nm",
      "options": []
    },
    "防护等级": {
      "column": "protection_level",
      "type": "enum",
      "unit": "",
      "options": ["IP54", "IP65", "IP67"],
      "hint": "设备防护等级"
    },
    "最低工作温度": {
      "column": "temp_min",
      "type": "numeric",
      "unit": "℃",
      "options": []
    },
    "最高工作温度": {
      "column": "temp_max",
      "type": "numeric",
      "unit": "℃",
      "options": []
    },
    "价格": {
      "column": "price",
      "type": "numeric",
      "unit": "元",
      "options": []
    }
  }
}
```

---

## 三、规则引擎实现逻辑详解

### 3.1 四层筛选评分流程

```
输入：产品 DataFrame + extracted_params
├─ Layer 1: 类别过滤（已跳过，产品已按类别从 DB 加载）
│
├─ Layer 2: 特殊规格硬性过滤（Veto）
│   配置: special_specs = ["interface", "shutter_type", "protection_level"]
│   规则: 任何不匹配的产品直接淘汰（_pass=False）
│   示例: 客户要求 interface="GigE"，产品为 USB3.0 → 淘汰
│
├─ Layer 3: 重要规格加权评分（0-100分 + 超额奖励）
│   配置: important_specs = {
│       "resolution_mp": {"weight": 0.35, "preference": ">="},
│       "frame_rate":    {"weight": 0.30, "preference": ">="},
│       "pixel_size_um": {"weight": 0.20, "preference": "any"},
│       "temp_min":      {"weight": 0.15, "preference": "<="}
│   }
│   规则: 权重自动归一化，匹配度 0.0-1.2（超额可达 1.2）
│   公式: score = weight * match_degree * 100
│
├─ Layer 4: 标签奖励加成（±分）
│   配置: tag_bonuses = {"新品": 5, "爆款": 4, "高毛利": 3, "清仓": -3}
│   规则: 根据产品 tags 字段累计加成
│   示例: 产品标签 "新品;现货" → +5 + +2 = +7分
│
└─ Layer 5: 价格排序（升降序）
    配置: price_sort = "asc" 或 "desc"
    规则: 总分相同时，按价格排序
```

### 3.2 匹配度计算详解

#### **数值型参数匹配度（match_degree）**

```python
def _match_degree(product_val, required_val, operator: str) -> float:
    """
    返回 0.0–1.2（>1 表示超额满足要求）

    规则示例：
    - operator=">=", required=5, actual=5.0 → match_degree=1.0（完全匹配）
    - operator=">=", required=5, actual=6.0 → match_degree=1.08（超额 20%，奖励 8%）
    - operator=">=", required=5, actual=4.5 → match_degree=0.72（90% 匹配，扣 20%）
    - operator="<=", required=-20, actual=-25 → match_degree=1.1（超额满足，更低温）
    - operator="=", actual==required → match_degree=1.0
    - operator="=", actual≠required → match_degree=0.0
    """
```

**超额奖励机制：**
- >= 运算符：实际值超出需求 20% → 匹配度可达 1.2（奖励分）
- <= 运算符：实际值更优（如更低价格、更低温）→ 匹配度可达 1.1

#### **枚举型/布尔型参数匹配度**

```python
# 能力等级（支持/不支持）
_CAPABILITY_SCALE = [
    ["不支持", "无", "否", "no", "false", "none"],  # rank=0
    ["支持", "有", "是", "yes", "true"],              # rank=1
]

# 规则：产品能力等级 ≥ 要求等级 → match_degree=1.0（或 1.1）
# 示例：要求"不支持"，产品"支持" → 满足（更高级能力兼容低需求）
```

### 3.3 评分示例计算

**假设配置：**
```json
{
  "important_specs": {
    "resolution_mp": {"weight": 0.35, "preference": ">="},
    "frame_rate":    {"weight": 0.30, "preference": ">="}
  }
}
```

**客户需求：**
```json
[
  {"column_name": "resolution_mp", "value": 5.0, "operator": ">="},
  {"column_name": "frame_rate", "value": 25, "operator": ">="}
]
```

**产品 A 规格：**
```json
{
  "resolution_mp": 5.0,
  "frame_rate": 30
}
```

**评分计算：**
```
1. 权重归一化：
   - total_weight = 0.35 + 0.30 = 0.65
   - resolution_weight = 0.35 / 0.65 = 0.538
   - frame_rate_weight = 0.30 / 0.65 = 0.462

2. 分辨率匹配度：
   - required=5.0, actual=5.0, operator=">="
   - match_degree = 1.0（完全匹配）
   - score_resolution = 0.538 * 1.0 * 100 = 53.8分

3. 帧率匹配度：
   - required=25, actual=30, operator=">="
   - match_degree = 1.0 + (30-25)/25 * 0.4 = 1.08（超额奖励）
   - score_frame_rate = 0.462 * 1.08 * 100 = 49.9分

4. 基础总分：
   - _score = 53.8 + 49.9 = 103.7分

5. 标签加成：
   - tags = "新品;现货"
   - _tag_bonus = 5 + 2 = +7分

6. 最终得分：
   - _total_score = 103.7 + 7 = 110.7分
```

---

## 四、数据库架构设计

### 4.1 SQLite 按类别分表策略

**核心设计：**
- 每个产品类别对应一个独立的表：`products_工业相机`, `products_网络摄像机`
- 表结构由 `param_schema` 动态生成，包含所有该类别的参数列
- 支持Schema演化：新增参数自动 `ALTER TABLE ADD COLUMN`

**示例表结构（工业相机）：**
```sql
CREATE TABLE products_工业相机 (
    product_id TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    tags TEXT,
    price REAL,
    resolution_mp REAL,      -- 分辨率(MP)
    frame_rate REAL,         -- 帧率(fps)
    interface TEXT,          -- 接口类型
    shutter_type TEXT,       -- 快门类型
    sensor_type TEXT,        -- 感光元件
    color_mode TEXT,         -- 色彩模式
    pixel_size_um REAL,      -- 像素尺寸(μm)
    temp_min REAL,           -- 最低工作温度(℃)
    temp_max REAL,           -- 最高工作温度(℃)
    protection_level TEXT    -- 防护等级
);
```

### 4.2 配置文件三层级架构

**优先级：config.json > default_config.json > 空默认值**

```
data/
├── default_config.json    # 版本控制的基准配置（不可修改）
│   └─ 包含：全局配置 + 各类别 schema + special_specs + important_specs
│
└── config.json            # 用户保存的配置覆盖（可修改）
    └─ 优先级高于 default_config.json
```

**读取逻辑：**
```python
def get_config():
    # 1. 尝试读取 config.json（用户保存的）
    if os.path.exists("data/config.json"):
        user_cfg = load_json("data/config.json")

    # 2. 读取 default_config.json（基准）
    default_cfg = load_json("data/default_config.json")

    # 3. 合并：用户配置覆盖默认配置
    return merge_configs(default_cfg, user_cfg)
```

---

## 五、前端交互流程

### 5.1 Tab 1 - 智能检索完整流程

```python
# 1. 用户输入客户需求文本
customer_text = st.text_area("客户需求描述")

# 2. 点击"开始智能检索"
if run_search:
    # Step 1a: 类别识别
    mapper = LLMMapper(api_key, base_url, model)
    extracted = mapper.extract_params(customer_text, categories_config)

    # Step 1b: 参数提取
    # （已在 extract_params 内部完成，两步合一）

    # Step 2: 从数据库加载该类别产品
    category = extracted.get("category")
    products = dm.get_products(category)

    # Step 3: 规则引擎筛选评分
    engine = RuleEngine(cat_cfg, global_cfg)
    results = engine.filter_and_score(products, extracted)

    # Step 4: 渲染推荐列表
    render_results(results, extracted, global_cfg)
```

### 5.2 Tab 2 - 产品库管理流程

**手动 CSV 导入：**
```python
# 1. 选择类别
category = st.selectbox("选择产品类别")

# 2. 上传 CSV
uploaded_file = st.file_uploader("上传 CSV", type="csv")

# 3. 解析预览
df = pd.read_csv(uploaded_file)
st.dataframe(df)

# 4. 确认导入
dm.save_products_df(df, category)
```

**AI 文档智能导入：**
```python
# 1. 上传产品文档（PDF/TXT，支持多选）
doc_files = st.file_uploader("选择产品文档", accept_multiple_files=True)

# 2. 提取文本
for file in doc_files:
    doc_text = extract_text_from_file(file)

# 3. LLM 提取参数
result = extract_products_from_doc(doc_text, category, param_schema, llm_client, model)

# 4. 批量预览确认
products_df = pd.DataFrame(result["products"])
st.dataframe(products_df)

# 5. 写入数据库
dm.save_products_df(products_df, category)
```

### 5.3 Tab 3 - 规则配置流程

```python
# 1. 每个类别一个独立 Tab
for category in categories:
    with st.tab(category):
        cat_cfg = dm.get_category_config(category)

        # 2. 配置特殊规格（硬性过滤）
        special_specs = st.multiselect("特殊规格", all_columns, default=cat_cfg["special_specs"])

        # 3. 配置重要规格（权重评分）
        for column in all_columns:
            weight = st.slider(f"{column} 权重", 0.0, 1.0, default_weight)
            preference = st.radio("偏好", ["=", ">=", "<=", "any"])

        # 4. 保存配置
        if st.button("保存配置"):
            new_cfg = build_config(...)
            save_json("data/config.json", new_cfg)
```

---

## 六、关键技术点总结

### 6.1 Prompt 设计原则

1. **两步分离策略：**
   - 类别识别：快速、低成本（max_tokens=200）
   - 参数提取：详细、高精度（max_tokens=2048）

2. **强制 JSON 输出：**
   - System Prompt 明确要求"严格输出JSON，不含任何其他内容"
   - 输出解析支持：markdown fence 提取、正则匹配、容错解析

3. **参数字典驱动：**
   - User Prompt 动态生成参数字典（基于 param_schema）
   - 强制使用 column_name（英文列名）而非中文参数名

4. **转换规则内置：**
   - 分辨率统一换算（MP）
   - 温度拆分（temp_min/temp_max）
   - 运算符智能推断（>=, <=, =）

### 6.2 规则引擎创新点

1. **四层渐进式筛选：**
   - Layer 2: 硬性过滤（快速淘汰）
   - Layer 3: 加权评分（精细量化）
   - Layer 4: 标签加成（业务规则）
   - Layer 5: 价格排序（业务决策）

2. **超额奖励机制：**
   - 匹配度可达 1.2（超额满足需求）
   - 避免过度惩罚"接近但不完全匹配"的产品

3. **权重自动归一化：**
   - 配置权重可为任意比例（如 0.35:0.30:0.20）
   - 自动归一化至总和=1.0

### 6.3 数据库设计亮点

1. **按类别分表：**
   - 避免跨类别字段冗余
   - 支持不同类别不同参数结构

2. **Schema 演化：**
   - 新增参数自动 `ALTER TABLE ADD COLUMN`
   - 不影响已有数据

3. **配置层级化：**
   - default_config.json（版本控制）
   - config.json（用户定制）
   - 动态合并优先级

---

## 七、完整调用链路图

```
用户输入需求文本
    ↓
[app.py] Tab 1 智能检索
    ↓
[LLMMapper.extract_params]
    ├─ Step 1a: 类别识别
    │   ├─ 构建类别列表 Prompt
    │   ├─ 调用 LLM (max_tokens=200)
    │   └─ 解析 {"category": "..."}
    │
    └─ Step 1b: 参数提取
        ├─ 获取该类别 param_schema
        ├─ 构建参数字典 Prompt
        ├─ 调用 LLM (max_tokens=2048)
        └─ 解析 {"extracted_params": [...], "summary": "..."}
    ↓
[DataManager.get_products]
    ├─ 从 products_{category} 表查询所有产品
    └─ 返回 DataFrame
    ↓
[RuleEngine.filter_and_score]
    ├─ Layer 2: 特殊规格硬性过滤
    │   └─ 遍历 special_specs，不匹配则 _pass=False
    │
    ├─ Layer 3: 重要规格加权评分
    │   ├─ 遍历 important_specs
    │   ├─ 计算每个参数的 match_degree
    │   └─ 累加 score = weight * match_degree * 100
    │
    ├─ Layer 4: 标签加成
    │   └─ 遍历 tags，累加 tag_bonuses
    │
    └─ Layer 5: 价格排序
        └─ 按总分 + 价格排序
    ↓
[app.py] 渲染推荐列表
    ├─ 显示 Top 10 产品
    ├─ 展开评分明细（匹配度可视化）
    └─ 显示被过滤产品原因
```

---

## 八、错误处理与容错机制

### 8.1 LLM 调用失败处理

```python
try:
    msg = client.messages.create(...)
    result = _parse_json_safe(_extract_text(msg))
except Exception as e:
    return {
        "category": None,
        "extracted_params": [],
        "summary": f"类别识别失败: {e}",
        "error": str(e)
    }
```

### 8.2 JSON 解析容错

```python
def _parse_json_safe(text: str) -> dict:
    # 1. 去除 markdown fence (```json ... ```)
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()

    # 2. 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 3. 正则匹配最外层 {...}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    # 4. 最终失败，返回错误信息
    return {
        "error": text[:300],
        "extracted_params": []
    }
```

---

## 九、配置示例完整解读

### 9.1 全局配置

```json
{
  "global": {
    "price_sort": "asc",           // 价格排序方向（低→高）
    "tag_bonuses": {
      "新品": 5,                   // 新品加 5分
      "爆款": 4,                   // 爆款加 4分
      "高毛利": 3,                 // 高毛利加 3分
      "现货": 2,                   // 现货加 2分
      "清仓": -3                   // 清仓减 3分
    }
  }
}
```

### 9.2 类别配置示例（工业相机）

```json
{
  "工业相机": {
    "param_schema": {
      "分辨率": {
        "column": "resolution_mp",      // 数据库列名（英文）
        "type": "numeric",              // 参数类型
        "unit": "MP",                   // 单位
        "display_name": "分辨率",       // 前端显示名
        "hint": "统一换算为MP",         // 提取提示
        "default_operator": ">="        // 默认运算符
      },
      "接口类型": {
        "column": "interface",
        "type": "enum",                 // 枚举型
        "options": ["USB3.0", "GigE"],  // 可选值
        "default_operator": "="
      }
    },

    "special_specs": ["interface", "shutter_type", "protection_level"],
    // 特殊规格：硬性过滤（不匹配直接淘汰）

    "important_specs": {
      "resolution_mp": {"weight": 0.35, "preference": ">="},
      "frame_rate":    {"weight": 0.30, "preference": ">="},
      "pixel_size_um": {"weight": 0.20, "preference": "any"},
      "temp_min":      {"weight": 0.15, "preference": "<="}
    }
    // 重要规格：加权评分（权重总和=1.0，preference 指示偏好方向）
  }
}
```

---

## 十、未来优化方向

### 10.1 Prompt 优化

1. **Few-shot Learning：**
   - 在 Prompt 中加入成功提取案例，提升准确率

2. **动态温度调整：**
   - 类别识别：低温度（temperature=0.1，确定性高）
   - 参数提取：中等温度（temperature=0.5，容错性好）

3. **批量文档处理：**
   - 当前单次调用处理一个文档
   - 可优化为一次调用处理多个文档（减少 API 调用次数）

### 10.2 规则引擎增强

1. **非线性评分：**
   - 当前线性评分（weight * match_degree * 100）
   - 可引入阶梯式奖励（超过阈值后奖励更高）

2. **组合规则：**
   - 当前单个参数独立评分
   - 可支持参数组合规则（如：分辨率 + 帧率组合奖励）

3. **历史数据学习：**
   - 从用户选择历史中学习权重调整建议

---

## 结语

本系统通过 **两步 LLM 调用 + 四层规则引擎** 的组合架构，实现了从客户需求文本到精准产品推荐的完整流程。核心创新点在于：

1. **Prompt 设计：** 参数字典驱动、强制 JSON 输出、转换规则内置
2. **规则引擎：** 硬性过滤 + 加权评分 + 超额奖励 + 标签加成
3. **数据库架构：** 按类别分表、Schema 演化、配置层级化

通过本文档的详细解读，可以清晰理解每个模块的实现逻辑和 Prompt 设计思路，便于后续维护和扩展。