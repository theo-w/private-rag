"""生成演示用 SQLite 知识库：data/sql/demo.db。

运行：
    python scripts/init_demo_db.py
随后可使用 config/sources.demo.yaml 同时入库文件与数据库。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from private_rag.config import ROOT

DB_PATH = ROOT / "data" / "sql" / "demo.db"

ROWS = [
    (1, "橙子", "水果", "橙子富含维生素 C，适合日常补充维 C；表皮较厚，常温可存放一周左右。", 12.5, "2026-01-10"),
    (2, "苹果", "水果", "苹果适合冷藏保存，可以延长保鲜时间；切开后容易氧化，建议尽快食用。", 8.0, "2026-01-12"),
    (3, "香蕉", "水果", "香蕉不适合放冰箱，低温会让表皮变黑、口感变差；建议常温阴凉处保存。", 6.5, "2026-01-15"),
    (4, "猕猴桃", "水果", "猕猴桃维生素 C 含量高，成熟后应尽快食用，也可以冷藏延缓软化。", 15.0, "2026-01-18"),
]


def main() -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("DROP TABLE IF EXISTS products")
        connection.execute(
            """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT,
                description TEXT NOT NULL,
                price REAL,
                updated_at TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO products (id, name, category, description, price, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ROWS,
        )
        connection.commit()
    print(f"✅ 演示数据库已生成：{DB_PATH}（{len(ROWS)} 条记录）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
