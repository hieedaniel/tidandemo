# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

智能产品检索系统 — an LLM + rule-engine product matching app for UNV/宇视科技 products (industrial cameras, network cameras, digital signage). A salesperson pastes customer requirements; the system identifies the product category, extracts structured parameters via LLM, then filters and scores the per-category product catalog using a configurable rule engine.

## Commands

```bash
# Install dependencies (Windows venv)
venv/Scripts/pip install -r requirements.txt

# Run the app
venv/Scripts/streamlit run app.py
```

No test suite exists.

## Environment Setup

Copy `.env.example` to `.env`. The app reads three env vars:

```
ANTHROPIC_AUTH_TOKEN=...      # API key
ANTHROPIC_BASE_URL=...        # Optional: proxy endpoint (e.g. Uniview internal AI gateway)
ANTHROPIC_MODEL=...           # Model name, e.g. glm-5 or claude-3-5-sonnet-20241022
```

The `LLMMapper` passes `base_url` to `anthropic.Anthropic()` so any OpenAI-compatible proxy works as a drop-in.

## Architecture

### Core Layer (`core/`)

**`DataManager`** — manages SQLite database (`data/products.db`) with one table per product category.
- On first run: auto-migrates `products.csv` into category tables and seeds new categories from inline data.
- Key methods: `get_products(category)`, `save_products_df(df, category)`, `import_products_for_category(file, category)`, `get_category_config(category)`, `get_global_config()`.
- Config cascade: `data/config.json` (user-saved) → `data/default_config.json` → `{}`.
- `get_categories()` reads category names from config; each category maps to a SQLite table named `products_{category}`.
- `_ensure_table` creates tables with columns derived from `param_schema`, and performs schema evolution (ALTER TABLE ADD COLUMN) for new fields.

**`LLMMapper`** — two-step LLM extraction:
1. **Category identification**: sends all category names, returns `{"category": "..."}`.
2. **Parameter extraction**: uses the identified category's `param_schema` to build a detailed prompt, returns `{"extracted_params": [...], "summary": "..."}`.
- `extract_params(customer_text, categories_config)` merges both steps and attaches `"category"` to the result.

**`RuleEngine`** — 4-active-layer pipeline (products are pre-filtered by category at DB query time):
1. ~~Category filter~~ — skipped; products already come from the right table
2. **Special specs veto** — columns in `cat_cfg["special_specs"]`; any mismatch eliminates the product
3. **Weighted scoring** — columns in `cat_cfg["important_specs"]`, weights auto-normalized; score 0–100, up to +20 bonus for exceeding requirements
4. **Tag bonus** — adds/subtracts points from `global_cfg["tag_bonuses"]`
5. **Price sort** — tie-breaking by price (direction from `global_cfg["price_sort"]`)

Constructor: `RuleEngine(category_config, global_config)` — receives category-specific and global configs separately.

### App Layer (`app.py`)

Single-file Streamlit UI with three tabs:
- **智能检索** — customer text → two-step LLM → load category products → `RuleEngine` → ranked results with score breakdown
- **产品库管理** — category selector at top; per-category CSV import/export; dynamic column display based on category's `param_schema`
- **规则配置** — `st.tabs()` with one tab per category (each with its own `st.form`) + global settings tab; no nested forms

All state lives in `st.session_state` including the `DataManager` singleton.

### Config Structure (`data/default_config.json`)

```json
{
  "global": {
    "price_sort": "asc",
    "tag_bonuses": { "新品": 5, "爆款": 4, ... }
  },
  "categories": {
    "工业相机": {
      "param_schema": { "分辨率": {"column": "resolution_mp", "type": "numeric", "unit": "MP", ...}, ... },
      "special_specs": ["interface", "shutter_type", "protection_level"],
      "important_specs": { "resolution_mp": {"weight": 0.35, "preference": ">="}, ... }
    },
    "网络摄像机": { ... },
    "信息发布屏": { ... }
  }
}
```

Currently 5 categories: 工业相机, 线扫相机, 3D相机, 网络摄像机, 信息发布屏.

### Data Files (`data/`)

- `products.db` — SQLite database; auto-created on first run; one table per category
- `default_config.json` — version-controlled baseline with per-category schemas; never modified at runtime
- `config.json` — user-saved overrides; written by Tab 3 rule editor; takes precedence over default
- `products.csv` / `sample_products.csv` — legacy CSV used only for one-time migration into SQLite on first run

## Key Patterns

- **Per-category isolation**: each category has its own DB table (different columns) and its own `param_schema`/`special_specs`/`important_specs`. Adding a new category requires adding it to `default_config.json` and running the app once to create the table.
- **Two-step LLM extraction**: category identification is a separate fast call (max_tokens=200); parameter extraction uses only the matched category's schema, keeping prompts focused and reducing hallucination across unrelated fields.
- **Schema evolution**: `_ensure_table` compares `PRAGMA table_info` against `param_schema` and issues `ALTER TABLE ADD COLUMN` for any new fields — existing data is preserved when adding params to a category.
- **Proxy-compatible LLM client**: `ANTHROPIC_BASE_URL` routes to any compatible endpoint without code changes.
