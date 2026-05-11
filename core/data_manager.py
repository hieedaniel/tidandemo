import pandas as pd
import json
import sqlite3
import shutil
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent.parent / "data"
CONFIG_FILE = DATA_DIR / "config.json"
DEFAULT_CONFIG_FILE = DATA_DIR / "default_config.json"
DB_FILE = DATA_DIR / "products.db"
LEGACY_CSV = DATA_DIR / "products.csv"
SAMPLE_CSV = DATA_DIR / "sample_products.csv"

_COMMON_COLS = ["product_id", "product_name", "tags"]


class DataManager:
    def __init__(self):
        DATA_DIR.mkdir(exist_ok=True)
        self._config: Optional[dict] = None
        self._ensure_db()

    # ── Config ────────────────────────────────────────────────────────────────

    def get_config(self) -> dict:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                self._config = json.load(f)
        elif DEFAULT_CONFIG_FILE.exists():
            with open(DEFAULT_CONFIG_FILE, "r", encoding="utf-8") as f:
                self._config = json.load(f)
        else:
            self._config = {"global": {}, "categories": {}}
        return self._config

    def save_config(self, config: dict):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        self._config = config

    def reset_config(self):
        if DEFAULT_CONFIG_FILE.exists():
            shutil.copy(DEFAULT_CONFIG_FILE, CONFIG_FILE)
        self._config = None

    @property
    def config(self) -> dict:
        if self._config is None:
            return self.get_config()
        return self._config

    # ── Category helpers ──────────────────────────────────────────────────────

    def get_categories(self) -> list:
        return list(self.config.get("categories", {}).keys())

    def get_category_config(self, category: str) -> dict:
        return self.config.get("categories", {}).get(category, {})

    def add_category(self, category: str, cat_cfg: dict):
        """Register a new category in config and create its DB table."""
        config = self.get_config()
        if category in config.get("categories", {}):
            raise ValueError(f"类别「{category}」已存在")
        config.setdefault("categories", {})[category] = cat_cfg
        self.save_config(config)
        with self._get_conn() as conn:
            self._ensure_table(conn, category)

    def delete_category(self, category: str):
        """Remove a category from config and drop its DB table."""
        config = self.get_config()
        config.get("categories", {}).pop(category, None)
        self.save_config(config)
        with self._get_conn() as conn:
            tbl = self._table_name(category)
            conn.execute(f'DROP TABLE IF EXISTS "{tbl}"')
            conn.commit()

    def update_param_schema(self, category: str, new_schema: dict):
        """Replace param_schema for a category and evolve the DB table."""
        config = self.get_config()
        if category not in config.get("categories", {}):
            raise ValueError(f"类别「{category}」不存在")
        config["categories"][category]["param_schema"] = new_schema
        self.save_config(config)
        with self._get_conn() as conn:
            self._ensure_table(conn, category)

    def get_global_config(self) -> dict:
        return self.config.get("global", {})

    # ── SQLite helpers ────────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _table_name(category: str) -> str:
        return f"products_{category}"

    @staticmethod
    def _col_sql_type(param_info: dict) -> str:
        return "REAL" if param_info.get("type") == "numeric" else "TEXT"

    def _ensure_table(self, conn: sqlite3.Connection, category: str):
        """Create table if it doesn't exist; add missing columns for schema evolution."""
        cat_cfg = self.get_category_config(category)
        schema = cat_cfg.get("param_schema", {})
        tbl = self._table_name(category)

        col_defs = [
            "product_id TEXT PRIMARY KEY",
            "product_name TEXT",
            "tags TEXT",
        ]
        for param_info in schema.values():
            col = param_info.get("column")
            if col and col not in ("product_id", "product_name", "tags"):
                col_defs.append(f'"{col}" {self._col_sql_type(param_info)}')

        conn.execute(f'CREATE TABLE IF NOT EXISTS "{tbl}" ({", ".join(col_defs)})')
        conn.commit()

        # Schema evolution: add any new columns that don't exist yet
        existing = {row[1] for row in conn.execute(f'PRAGMA table_info("{tbl}")').fetchall()}
        for param_info in schema.values():
            col = param_info.get("column")
            if col and col not in existing:
                conn.execute(f'ALTER TABLE "{tbl}" ADD COLUMN "{col}" {self._col_sql_type(param_info)}')
        conn.commit()

    def _ensure_db(self):
        if DB_FILE.exists():
            with self._get_conn() as conn:
                for cat in self.get_categories():
                    self._ensure_table(conn, cat)
            return

        print("首次启动：初始化产品数据库...")
        with self._get_conn() as conn:
            for cat in self.get_categories():
                self._ensure_table(conn, cat)
        self._migrate_legacy_csv()
        self._seed_new_categories()
        print("数据库初始化完成。")

    def _migrate_legacy_csv(self):
        csv_path = LEGACY_CSV if LEGACY_CSV.exists() else SAMPLE_CSV
        if not csv_path.exists():
            return
        df = pd.read_csv(csv_path)
        if df.empty or "category" not in df.columns:
            return
        categories = self.get_categories()
        with self._get_conn() as conn:
            for cat, group in df.groupby("category"):
                cat = str(cat)
                if cat not in categories:
                    continue
                tbl = self._table_name(cat)
                existing_cols = [
                    row[1] for row in conn.execute(f'PRAGMA table_info("{tbl}")').fetchall()
                ]
                insert_df = group[[c for c in group.columns if c in existing_cols]].copy()
                conn.execute(f'DELETE FROM "{tbl}"')
                insert_df.to_sql(tbl, conn, if_exists="append", index=False)
        print(f"已从 {csv_path.name} 迁移历史产品数据。")

    def _seed_new_categories(self):
        ipc_rows = [
            {
                "product_id": "IPC-L2A4-IR-F40",
                "product_name": "UNV 400万红外筒型网络摄像机(4mm)",
                "tags": "新品;现货",
                "resolution_mp": 4.0, "frame_rate": 30,
                "sensor_size": '1/3.0"', "focal_length_mm": 4.0,
                "ir_mode": "红外", "ir_range_m": 50,
                "min_illumination_lux": 0.01, "compression": "超级265",
                "protection_level": "IP67", "power_supply": "DC12V/PoE",
                "temp_min": -30, "temp_max": 60, "price": 599,
            },
            {
                "product_id": "IPC-L2A4-IR-F60",
                "product_name": "UNV 400万红外筒型网络摄像机(6mm)",
                "tags": "新品;现货",
                "resolution_mp": 4.0, "frame_rate": 30,
                "sensor_size": '1/3.0"', "focal_length_mm": 6.0,
                "ir_mode": "红外", "ir_range_m": 50,
                "min_illumination_lux": 0.01, "compression": "超级265",
                "protection_level": "IP67", "power_supply": "DC12V/PoE",
                "temp_min": -30, "temp_max": 60, "price": 599,
            },
            {
                "product_id": "IPC-L2A4-WH-AEF40H",
                "product_name": "UNV 400万经济型全彩筒形网络摄像机(4mm)",
                "tags": "新品;现货;爆款",
                "resolution_mp": 4.0, "frame_rate": 25,
                "sensor_size": '1/1.8"', "focal_length_mm": 4.0,
                "ir_mode": "暖光", "ir_range_m": 30,
                "min_illumination_lux": 0.0005, "compression": "超级265",
                "protection_level": "IP67", "power_supply": "DC12V/PoE",
                "temp_min": -30, "temp_max": 60, "price": 799,
            },
            {
                "product_id": "IPC-L2A4-WH-AEF60H",
                "product_name": "UNV 400万经济型全彩筒形网络摄像机(6mm)",
                "tags": "新品;现货;爆款",
                "resolution_mp": 4.0, "frame_rate": 25,
                "sensor_size": '1/1.8"', "focal_length_mm": 6.0,
                "ir_mode": "暖光", "ir_range_m": 30,
                "min_illumination_lux": 0.0005, "compression": "超级265",
                "protection_level": "IP67", "power_supply": "DC12V/PoE",
                "temp_min": -30, "temp_max": 60, "price": 799,
            },
            {
                "product_id": "IPC-Y2A2-IR-F40",
                "product_name": "UNV 200万红外筒型网络摄像机(4mm)",
                "tags": "现货",
                "resolution_mp": 2.0, "frame_rate": 30,
                "sensor_size": '1/2.8"', "focal_length_mm": 4.0,
                "ir_mode": "红外", "ir_range_m": 50,
                "min_illumination_lux": 0.01, "compression": "超级265",
                "protection_level": "IP67", "power_supply": "DC12V/PoE",
                "temp_min": -30, "temp_max": 60, "price": 399,
            },
            {
                "product_id": "IPC-Y2A2-IR-F60",
                "product_name": "UNV 200万红外筒型网络摄像机(6mm)",
                "tags": "现货",
                "resolution_mp": 2.0, "frame_rate": 30,
                "sensor_size": '1/2.8"', "focal_length_mm": 6.0,
                "ir_mode": "红外", "ir_range_m": 50,
                "min_illumination_lux": 0.01, "compression": "超级265",
                "protection_level": "IP67", "power_supply": "DC12V/PoE",
                "temp_min": -30, "temp_max": 60, "price": 399,
            },
        ]
        display_rows = [
            {
                "product_id": "MW3343-GW",
                "product_name": "UNV MW3343-GW 43寸信息发布屏",
                "tags": "新品;现货",
                "screen_size_inch": 43, "panel_type": "IPS",
                "display_resolution": "1920×1080", "brightness_nits": 400,
                "contrast_ratio": "1200:1", "os_version": "Android 11",
                "ram_gb": 2, "rom_gb": 32, "wifi": "支持",
                "install_type": "壁挂", "max_power_w": 52,
                "temp_min": 0, "temp_max": 40, "price": 3999,
            },
            {
                "product_id": "MW3355-GW",
                "product_name": "UNV MW3355-GW 55寸信息发布屏",
                "tags": "新品;现货",
                "screen_size_inch": 55, "panel_type": "IPS",
                "display_resolution": "1920×1080", "brightness_nits": 400,
                "contrast_ratio": "1200:1", "os_version": "Android 11",
                "ram_gb": 2, "rom_gb": 32, "wifi": "支持",
                "install_type": "壁挂", "max_power_w": 87,
                "temp_min": 0, "temp_max": 40, "price": 5999,
            },
            {
                "product_id": "MW3355-L",
                "product_name": "UNV MW3355-L 55寸信息发布屏(立式)",
                "tags": "新品;现货",
                "screen_size_inch": 55, "panel_type": "IPS",
                "display_resolution": "1080×1920", "brightness_nits": 300,
                "contrast_ratio": "1200:1", "os_version": "Android 11",
                "ram_gb": 2, "rom_gb": 32, "wifi": "支持",
                "install_type": "立式", "max_power_w": 85,
                "temp_min": 0, "temp_max": 40, "price": 7999,
            },
        ]

        categories = self.get_categories()
        with self._get_conn() as conn:
            if "网络摄像机" in categories:
                tbl = self._table_name("网络摄像机")
                pd.DataFrame(ipc_rows).to_sql(tbl, conn, if_exists="append", index=False)
                print(f"已写入网络摄像机种子数据（{len(ipc_rows)} 条）。")
            if "信息发布屏" in categories:
                tbl = self._table_name("信息发布屏")
                pd.DataFrame(display_rows).to_sql(tbl, conn, if_exists="append", index=False)
                print(f"已写入信息发布屏种子数据（{len(display_rows)} 条）。")

    # ── Products CRUD ──────────────────────────────────────────────────────────

    def get_products(self, category: str) -> pd.DataFrame:
        tbl = self._table_name(category)
        with self._get_conn() as conn:
            try:
                df = pd.read_sql_query(f'SELECT * FROM "{tbl}"', conn)
                df["category"] = category
                return df
            except Exception:
                return pd.DataFrame()

    def get_all_products(self) -> pd.DataFrame:
        frames = [self.get_products(cat) for cat in self.get_categories()]
        frames = [f for f in frames if not f.empty]
        return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()

    def save_products_df(self, df: pd.DataFrame, category: str):
        df_save = df.drop(columns=["category"], errors="ignore").copy()
        tbl = self._table_name(category)
        with self._get_conn() as conn:
            self._ensure_table(conn, category)
            conn.execute(f'DELETE FROM "{tbl}"')
            conn.commit()
            df_save.to_sql(tbl, conn, if_exists="append", index=False)

    def import_products_for_category(self, uploaded_file, category: str) -> pd.DataFrame:
        name = uploaded_file.name
        df = pd.read_csv(uploaded_file) if name.endswith(".csv") else pd.read_excel(uploaded_file)
        self.save_products_df(df, category)
        return self.get_products(category)

    def export_products_csv(self, category: str) -> bytes:
        df = self.get_products(category).drop(columns=["category"], errors="ignore")
        return df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

    def get_product_counts(self) -> dict:
        counts = {}
        with self._get_conn() as conn:
            for cat in self.get_categories():
                tbl = self._table_name(cat)
                try:
                    result = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()
                    counts[cat] = result[0] if result else 0
                except Exception:
                    counts[cat] = 0
        return counts

    def get_category_tags(self, category: str) -> list:
        df = self.get_products(category)
        all_tags: set = set()
        if not df.empty and "tags" in df.columns:
            for tv in df["tags"].dropna():
                all_tags.update(t.strip() for t in str(tv).split(";") if t.strip())
        return sorted(all_tags)

    def get_all_tags(self) -> list:
        all_tags: set = set()
        for cat in self.get_categories():
            all_tags.update(self.get_category_tags(cat))
        return sorted(all_tags)

    def reset_to_seed(self):
        """Drop all product tables and re-seed from scratch."""
        with self._get_conn() as conn:
            for cat in self.get_categories():
                tbl = self._table_name(cat)
                conn.execute(f'DROP TABLE IF EXISTS "{tbl}"')
            conn.commit()
        DB_FILE.unlink(missing_ok=True)
        self._ensure_db()
