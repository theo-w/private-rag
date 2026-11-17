"""运行时 SQL 只读护栏：该放的只读查询要放，该拦的写操作与越权表必须拦住。"""

from __future__ import annotations

import pytest

from private_rag.agent.sql_guard import (
    SqlGuardError,
    enforce_limit,
    extract_cte_names,
    extract_tables,
    sanitize_sql,
    strip_comments,
)

ALLOWED = ["products", "orders"]


def run_guard(sql: str, *, max_rows: int = 20, allowed=ALLOWED) -> tuple[str, list[str]]:
    return sanitize_sql(sql, allowed_tables=allowed, max_rows=max_rows)


class Test注释:
    def test_line_comment_removed(self):
        assert strip_comments("SELECT id -- 注：删除\nFROM t") == "SELECT id FROM t"

    def test_block_comment_removed(self):
        assert strip_comments("SELECT /* 藏在里面 */ id FROM t") == "SELECT id FROM t"

    def test_comment_cannot_hide_keyword(self):
        # 注释里写 delete 不会因为「看起来是注释」被放行到数据库，而是先被剥离
        guarded, _ = run_guard("SELECT id FROM products -- delete everything\n")
        assert guarded == "SELECT id FROM products LIMIT 20"


class TestTableExtraction:
    def test_from_and_join(self):
        sql = "SELECT * FROM products JOIN orders ON orders.pid = products.id WHERE x = 1"
        assert extract_tables(sql) == {"products", "orders"}

    def test_comma_separated_tables(self):
        assert extract_tables("SELECT * FROM products, orders WHERE 1 = 1") == {
            "products",
            "orders",
        }

    def test_schema_prefix_and_quotes(self):
        assert extract_tables("SELECT * FROM `mydb`.`products` AS p LIMIT 1") == {"products"}

    def test_table_inside_subquery(self):
        assert extract_tables("SELECT * FROM (SELECT id FROM products LIMIT 5) x") == {"products"}

    def test_cte_names_recognised(self):
        sql = "WITH cheap AS (SELECT id FROM products), hot AS (SELECT id FROM cheap) SELECT * FROM hot"
        assert extract_cte_names(sql) == {"cheap", "hot"}
        # 原始解析会把 CTE 名也当表名，白名单比对时由 sanitize_sql 减掉
        assert extract_tables(sql) == {"products", "cheap", "hot"}


class TestAllowedQueries:
    def test_plain_select_gets_limit(self):
        guarded, warnings = run_guard("SELECT * FROM products")
        assert guarded == "SELECT * FROM products LIMIT 20"
        assert warnings == ["自动补上 LIMIT 20"]

    def test_trailing_semicolon_accepted(self):
        guarded, _ = run_guard("SELECT id FROM products;")
        assert guarded == "SELECT id FROM products LIMIT 20"

    def test_cte_accepted(self):
        # CTE 是查询内的临时视图，不该被当成未授权的表
        guarded, _ = run_guard("WITH t AS (SELECT id FROM products) SELECT * FROM t")
        assert guarded.endswith("LIMIT 20")

    def test_multiple_ctes_accepted(self):
        sql = "WITH a AS (SELECT id FROM products), b AS (SELECT id FROM a) SELECT * FROM b"
        guarded, _ = run_guard(sql)
        assert guarded.endswith("LIMIT 20")

    def test_join_of_two_allowed_tables(self):
        guarded, _ = run_guard("SELECT p.id FROM products p JOIN orders o ON o.pid = p.id")
        assert guarded.endswith("LIMIT 20")

    def test_limit_within_cap_kept_as_is(self):
        guarded, warnings = run_guard("SELECT id FROM products LIMIT 3")
        assert guarded == "SELECT id FROM products LIMIT 3"
        assert warnings == []

    def test_offset_preserved(self):
        guarded, warnings = run_guard("SELECT id FROM products LIMIT 3 OFFSET 7")
        assert guarded == "SELECT id FROM products LIMIT 3 OFFSET 7"
        assert warnings == []


class TestLimitEnforcement:
    def test_large_limit_clamped(self):
        guarded, warnings = run_guard("SELECT id FROM products LIMIT 100")
        assert guarded == "SELECT id FROM products LIMIT 20"
        assert warnings == ["LIMIT 被压缩：100 -> 20"]

    def test_mysql_style_offset_count_clamped(self):
        guarded, _ = run_guard("SELECT id FROM products LIMIT 5, 100")
        assert guarded == "SELECT id FROM products LIMIT 5, 20"

    def test_enforce_limit_appends(self):
        assert enforce_limit("SELECT 1", 5) == ("SELECT 1 LIMIT 5", None)

    def test_enforce_limit_reports_clamped_value(self):
        assert enforce_limit("SELECT id FROM t LIMIT 100", 20) == (
            "SELECT id FROM t LIMIT 20",
            100,
        )


class TestRejected:
    def test_empty_sql(self):
        with pytest.raises(SqlGuardError):
            run_guard("   ")

    def test_comment_only_sql(self):
        with pytest.raises(SqlGuardError, match="为空"):
            run_guard("-- 只有注释")

    def test_multi_statement_rejected(self):
        with pytest.raises(SqlGuardError, match="单条语句"):
            run_guard("SELECT id FROM products; DROP TABLE products")

    def test_non_select_rejected(self):
        with pytest.raises(SqlGuardError, match="SELECT"):
            run_guard("DELETE FROM products")

    def test_read_function_name_also_blocked(self):
        # replace() 是只读函数，但它与写操作同名，保守起见一并拒绝
        with pytest.raises(SqlGuardError, match="replace"):
            run_guard("SELECT replace(name, 'a', 'b') FROM products")

    def test_pragma_rejected(self):
        with pytest.raises(SqlGuardError, match="pragma"):
            run_guard("SELECT * FROM products WHERE pragma = 1")

    def test_unauthorized_table_rejected(self):
        with pytest.raises(SqlGuardError, match="未授权的表"):
            run_guard("SELECT * FROM secrets")

    def test_unauthorized_table_in_join_rejected(self):
        with pytest.raises(SqlGuardError, match="users"):
            run_guard("SELECT * FROM products JOIN users u ON u.pid = products.id")

    def test_no_from_clause_rejected(self):
        # 看不出查了哪张表就不放行，避免把护栏变成摆设
        with pytest.raises(SqlGuardError, match="无法从 SQL 中确认"):
            run_guard("SELECT 1")

    def test_no_allowed_tables_configured(self):
        with pytest.raises(SqlGuardError, match="没有配置允许查询的表"):
            run_guard("SELECT id FROM products", allowed=[])
