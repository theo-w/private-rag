"""生成评估专用 SQLite：data/sql/demo_eval.db。

运行：
    python scripts/init_eval_db.py
随后用 config/sources.eval.agent.v8.yaml 给 agentic 评估组挂上这份库。

为什么单独一份库、却又沿用演示库的引用标签（决策见 docs/adr/0008）：

- 演示库只有 4 行，v8 扩充集需要更多行才能给每条样本一段互不相同的证据；
  直接给演示库加行会让 v7 那几条依赖「这四种水果」的样本参考答案失效，
  老读数也对不上，所以演示库 scripts/init_demo_db.py 与 data/sql/demo.db 一字不改。
- 新库做成超集：前 4 行逐字照抄演示库，再加 8 行。这样 v7 集与新集都能查同一份库，
  `--dataset` 合并两份评估集一次跑时不会有一半样本查不到数据。
- `ToolRegistry` 按工具名登记，一份 sources 配置里两个 sql 源会互相覆盖，
  实际只有一个 sql_query 工具（private_rag/agent/tools.py:283），所以引用标签必须共用
  `demo.products`：两个标签只能活一个，另一半样本的 source_hit 会恒为 0。
- 新增行的 category 一律不含「水果」二字，否则 v7 的「这四种水果」类样本在合并跑里
  会被 `category LIKE '%水果%'` 这类写法连带查出来，语义被带偏。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from private_rag.config import ROOT

DB_PATH = ROOT / "data" / "sql" / "demo_eval.db"

# 前 4 行与 scripts/init_demo_db.py 的 ROWS 逐字段相等（tests/test_evaluation.py 钉住这条），
# 只是在演示库的列之外补上储存方式、供应商、库存三个字段。
ROWS = [
    (1, "橙子", "水果", "橙子富含维生素 C，适合日常补充维 C；表皮较厚，常温可存放一周左右。", "常温阴凉", "华南果园", 40, 12.5, "2026-01-10"),
    (2, "苹果", "水果", "苹果适合冷藏保存，可以延长保鲜时间；切开后容易氧化，建议尽快食用。", "冷藏 0-4℃", "华北果园", 65, 8.0, "2026-01-12"),
    (3, "香蕉", "水果", "香蕉不适合放冰箱，低温会让表皮变黑、口感变差；建议常温阴凉处保存。", "常温阴凉", "东南亚直采", 30, 6.5, "2026-01-15"),
    (4, "猕猴桃", "水果", "猕猴桃维生素 C 含量高，成熟后应尽快食用，也可以冷藏延缓软化。", "冷藏 0-4℃", "西北果园", 25, 15.0, "2026-01-18"),
    (5, "蓝莓", "浆果", "蓝莓娇嫩易压，收到后应尽快食用，未洗的可以冷藏存放三四天。", "冷藏 0-4℃", "绿源鲜达", 80, 28.0, "2026-02-02"),
    (6, "牛油果", "热带果品", "牛油果硬的时候常温放两三天催熟，捏着发软就要当天吃，切开吃不完需冷藏并淋柠檬汁。", "常温催熟后冷藏", "南美果品", 35, 18.0, "2026-02-05"),
    (7, "芒果", "热带果品", "芒果常温催熟，表皮起皱前食用，成熟后冷藏可多放两天，但香气会变淡。", "常温催熟后冷藏", "海南果园", 60, 9.5, "2026-02-08"),
    (8, "核桃", "坚果", "核桃带壳干燥常温能放半年，剥壳后必须密封冷藏，受潮会变苦。", "密封干燥常温", "西北山货", 120, 32.0, "2026-02-11"),
    (9, "腰果", "坚果", "腰果开封后务必密封并冷藏，敞放两周就会返软失去脆度。", "密封冷藏", "西北山货", 90, 45.0, "2026-02-14"),
    (10, "桂圆干", "干货", "桂圆干未开封阴凉干燥处可放一年，开袋后尽快吃完，返潮容易发霉。", "阴凉干燥", "华南干货", 200, 26.0, "2026-02-17"),
    (11, "鳕鱼", "海鲜", "鳕鱼冷冻保存不要反复解冻，冷藏化冻后当天必须烹煮，否则腥味明显。", "冷冻 -18℃", "渤海渔港", 25, 58.0, "2026-02-20"),
    (12, "酸奶", "乳制品", "酸奶全程冷藏，过期一天未拆封一般还能喝，胀包就直接扔掉。", "全程冷藏 2-6℃", "北方牧场", 150, 7.5, "2026-02-23"),
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
                storage TEXT,
                supplier TEXT,
                stock INTEGER,
                price REAL,
                updated_at TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO products (id, name, category, description, storage, supplier, stock, price, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ROWS,
        )
        connection.commit()
    print(f"✅ 评估专用数据库已生成：{DB_PATH}（{len(ROWS)} 条记录）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
