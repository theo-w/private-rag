"""SQL 连接器测试：行级文档、列校验、截断与 DSN 脱敏。"""

from __future__ import annotations

import sqlite3

from private_rag.ingestion.sql import load_sql_documents, redact_dsn
from private_rag.sources import SqlSource, source_key_for


def _demo_db(path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, description TEXT, updated_at TEXT)"
        )
        connection.executemany(
            "INSERT INTO products (id, name, description, updated_at) VALUES (?, ?, ?, ?)",
            [
                (1, "橙子", "橙子富含维生素 C。", "2026-01-10"),
                (2, "香蕉", "香蕉不适合放冰箱。", "2026-01-15"),
            ],
        )
        connection.commit()


def _source(db_path, **overrides) -> SqlSource:
    payload = dict(
        dsn=f"sqlite:///{db_path}",
        query="SELECT id, name, description, updated_at FROM products ORDER BY id",
        id_column="id",
        title_column="name",
        content_columns=["name", "description"],
        updated_at_column="updated_at",
        label="demo.products",
        tags=["business"],
    )
    payload.update(overrides)
    return SqlSource(**payload)


def test_rows_become_documents(tmp_path):
    db_path = tmp_path / "demo.db"
    _demo_db(db_path)
    source = _source(db_path)
    documents, errors = load_sql_documents(source, source_key_for(source))

    assert errors == []
    assert len(documents) == 2
    first = documents[0]
    assert first.doc_id.endswith(":1")
    assert first.title == "橙子"
    assert first.source_type == "sql"
    assert "name: 橙子" in first.text
    assert "description: 橙子富含维生素 C。" in first.text
    assert first.updated_at == "2026-01-10"
    assert first.tags == ["business"]
    assert first.extra["pk"] == "1"
    assert first.extra["table"] == "demo.products"
    assert first.source_uri == "demo.products#1"


def test_max_rows_truncates_and_reports(tmp_path):
    db_path = tmp_path / "demo.db"
    _demo_db(db_path)
    documents, errors = load_sql_documents(_source(db_path, max_rows=1), "sql-key")
    assert len(documents) == 1
    assert any("max_rows" in error for error in errors)


def test_missing_columns_are_reported(tmp_path):
    db_path = tmp_path / "demo.db"
    _demo_db(db_path)
    source = _source(db_path, query="SELECT id, name FROM products", content_columns=["description"])
    documents, errors = load_sql_documents(source, "sql-key")
    assert documents == []
    assert any("缺少列" in error for error in errors)


def test_redact_dsn_removes_credentials():
    redacted = redact_dsn("mysql+pymysql://user:secret@db.example.com/shop")
    assert "secret" not in redacted
    assert "user" not in redacted
    assert "db.example.com" in redacted
    assert "shop" in redacted
