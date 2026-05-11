import pandas as pd
import numpy as np
from typing import Optional


class RuleEngine:
    """
    Five-layer filtering and scoring pipeline.
    Products are pre-filtered by category at DB query time, so layer 1 is a pass-through.
      1. Category check (pass-through — products already come from the right table)
      2. Special specs hard filter (veto)
      3. Important specs weighted scoring
      4. Tag bonus
      5. Price sort
    """

    def __init__(self, category_config: dict, global_config: dict):
        self.cat_cfg = category_config
        self.global_cfg = global_config

    def filter_and_score(self, products: pd.DataFrame, extracted: dict) -> pd.DataFrame:
        if products.empty:
            return products

        df = products.copy()
        df["_pass"] = True
        df["_score"] = 0.0
        df["_tag_bonus"] = 0.0
        df["_match_reasons"] = [[] for _ in range(len(df))]
        df["_fail_reasons"] = [[] for _ in range(len(df))]
        df["_score_detail"] = [{} for _ in range(len(df))]

        params = extracted.get("extracted_params", [])

        df = self._layer2_special(df, params)
        df = self._layer3_important(df, params)
        df = self._layer4_tags(df)

        df["_total_score"] = df["_score"] + df["_tag_bonus"]

        passed = df[df["_pass"]].copy()
        failed = df[~df["_pass"]].copy()

        price_asc = self.global_cfg.get("price_sort", "asc") == "asc"
        price_col = "price"

        if price_col in passed.columns:
            passed = passed.sort_values(
                ["_total_score", price_col],
                ascending=[False, price_asc],
            )
        else:
            passed = passed.sort_values("_total_score", ascending=False)

        return pd.concat([passed, failed], ignore_index=True)

    # ── Layer 2: Special specs (hard veto) ────────────────────────────────────

    def _layer2_special(self, df: pd.DataFrame, params: list) -> pd.DataFrame:
        special = set(self.cat_cfg.get("special_specs", []))
        for param in params:
            col = param.get("column_name", "")
            if col not in special or col not in df.columns:
                continue
            required = param.get("value")
            op = param.get("operator", "=")
            name = param.get("standard_name", col)
            for idx in df.index:
                if not df.at[idx, "_pass"]:
                    continue
                pval = df.at[idx, col]
                ok = _compare(pval, required, op)
                if ok:
                    df.at[idx, "_match_reasons"] = df.at[idx, "_match_reasons"] + [
                        f"✓ {name}: {pval}"
                    ]
                else:
                    df.at[idx, "_pass"] = False
                    df.at[idx, "_fail_reasons"] = df.at[idx, "_fail_reasons"] + [
                        f"✗ 特殊规格不符 [{name}]: 需要 {op}{required}，实际 {pval}"
                    ]
        return df

    # ── Layer 3: Important specs scoring ──────────────────────────────────────

    def _layer3_important(self, df: pd.DataFrame, params: list) -> pd.DataFrame:
        important = self.cat_cfg.get("important_specs", {})
        if not important:
            return df

        raw_weights = {col: cfg.get("weight", 0) for col, cfg in important.items()}
        total_w = sum(raw_weights.values()) or 1.0

        by_col = {p.get("column_name"): p for p in params if p.get("column_name")}

        for col, cfg in important.items():
            if col not in by_col or col not in df.columns:
                continue
            param = by_col[col]
            required = param.get("value")
            op = param.get("operator", ">=")
            w = raw_weights[col] / total_w
            disp = cfg.get("display_name", col)

            for idx in df.index:
                if not df.at[idx, "_pass"]:
                    continue
                pval = df.at[idx, col]
                degree = _match_degree(pval, required, op)
                contribution = round(w * degree * 100, 2)
                df.at[idx, "_score"] += contribution
                df.at[idx, "_score_detail"][col] = {
                    "name": disp,
                    "required": required,
                    "actual": pval,
                    "operator": op,
                    "match_degree": round(degree, 3),
                    "weight": round(w, 3),
                    "score": contribution,
                }
        return df

    # ── Layer 4: Tag bonus ────────────────────────────────────────────────────

    def _layer4_tags(self, df: pd.DataFrame) -> pd.DataFrame:
        bonuses = self.global_cfg.get("tag_bonuses", {})
        if not bonuses or "tags" not in df.columns:
            return df
        for idx in df.index:
            tags = [t.strip() for t in str(df.at[idx, "tags"]).split(";") if t.strip()]
            df.at[idx, "_tag_bonus"] = sum(bonuses.get(t, 0) for t in tags)
        return df


# ── Pure comparison helpers ───────────────────────────────────────────────────

# Capability scale: higher index = stronger capability.
# If product_val ranks >= required_val, it satisfies an "=" requirement.
_CAPABILITY_SCALE: list[list[str]] = [
    ["不支持", "无", "否", "no", "false", "none"],
    ["支持", "有", "是", "yes", "true"],
]

def _capability_rank(val: str) -> int:
    """Return capability rank (0-based). -1 means not a capability value."""
    v = val.strip().lower()
    for rank, group in enumerate(_CAPABILITY_SCALE):
        if v in group:
            return rank
    return -1


def _compare(product_val, required_val, operator: str) -> bool:
    try:
        if pd.isna(product_val) or str(product_val).strip() == "":
            return False
        if operator == "contains":
            return str(required_val).strip().lower() in str(product_val).strip().lower()
        if operator == "=":
            # Try numeric comparison first to handle 55.0 == 55, 4.0 == 4, etc.
            try:
                return float(str(product_val).replace(",", "")) == float(str(required_val).replace(",", ""))
            except (ValueError, TypeError):
                pass
            # Capability fields: product having MORE capability always satisfies the requirement
            p_rank = _capability_rank(str(product_val))
            r_rank = _capability_rank(str(required_val))
            if p_rank != -1 and r_rank != -1:
                return p_rank >= r_rank
            return str(product_val).strip().lower() == str(required_val).strip().lower()
        pf = float(str(product_val).replace(",", ""))
        rf = float(str(required_val).replace(",", ""))
        return {">=": pf >= rf, "<=": pf <= rf, ">": pf > rf, "<": pf < rf}.get(operator, pf == rf)
    except (ValueError, TypeError):
        return str(product_val).strip().lower() == str(required_val).strip().lower()


def _match_degree(product_val, required_val, operator: str) -> float:
    """Returns 0.0–1.2 (>1 means exceeds requirement)."""
    try:
        if pd.isna(product_val):
            return 0.0
        if operator in ("=", "contains", "any"):
            if not _compare(product_val, required_val, operator):
                return 0.0
            # Give a small bonus when capability exceeds requirement (e.g. "支持" vs "不支持")
            p_rank = _capability_rank(str(product_val))
            r_rank = _capability_rank(str(required_val))
            if p_rank != -1 and r_rank != -1 and p_rank > r_rank:
                return 1.1
            return 1.0
        pf = float(str(product_val).replace(",", ""))
        rf = float(str(required_val).replace(",", ""))
        if rf == 0:
            return 1.0
        if operator == ">=":
            if pf >= rf:
                return min(1.2, 1.0 + (pf - rf) / rf * 0.4)
            return max(0.0, (pf / rf) * 0.8)
        if operator == "<=":
            if pf <= rf:
                return min(1.2, 1.0 + (rf - pf) / rf * 0.2)
            return max(0.0, (rf / pf) * 0.6)
    except (ValueError, TypeError):
        return 1.0 if _compare(product_val, required_val, "=") else 0.0
    return 0.0
