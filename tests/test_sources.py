"""sources.yaml 解析与环境变量插值测试。"""

from __future__ import annotations

import pytest

from private_rag.config import DOCS_DIR, ROOT
from private_rag.sources import (
    ConfigError,
    FileSource,
    SqlSource,
    WebSource,
    default_sources_config,
    load_sources_config,
    source_key_for,
)


def _write(tmp_path, content: str):
    path = tmp_path / "sources.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_all_source_types_with_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DSN", "sqlite:///tmp/demo.db")
    path = _write(
        tmp_path,
        """
sources:
  - type: file
    path: data/docs
    tags: [local]
  - type: web
    urls: [https://example.com/a]
    tags: [web]
  - type: sql
    dsn: ${DEMO_DSN}
    query: SELECT id, body FROM articles
    id_column: id
    content_columns: [body]
    tags: [business]
""",
    )
    config = load_sources_config(path)
    assert [type(source).__name__ for source in config.sources] == [
        "FileSource",
        "WebSource",
        "SqlSource",
    ]
    assert config.sources[0].tags == ["local"]
    # 相对 sqlite 路径会按项目根目录展开
    assert str(ROOT / "tmp/demo.db") in config.sources[2].dsn


def test_env_default_value(tmp_path, monkeypatch):
    monkeypatch.delenv("NOT_SET_DSN", raising=False)
    path = _write(
        tmp_path,
        """
sources:
  - type: sql
    dsn: ${NOT_SET_DSN:-sqlite:///data/sql/demo.db}
    query: SELECT id, body FROM articles
    id_column: id
    content_columns: [body]
""",
    )
    config = load_sources_config(path)
    assert config.sources[0].dsn.startswith("sqlite:////")
    assert str(ROOT / "data/sql/demo.db") in config.sources[0].dsn


def test_missing_env_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("REQUIRED_DSN", raising=False)
    path = _write(
        tmp_path,
        """
sources:
  - type: sql
    dsn: ${REQUIRED_DSN}
    query: SELECT 1
    id_column: id
    content_columns: [id]
""",
    )
    with pytest.raises(ConfigError, match="REQUIRED_DSN"):
        load_sources_config(path)


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load_sources_config(tmp_path / "nope.yaml")


def test_empty_sources_raises(tmp_path):
    path = _write(tmp_path, "sources: []\n")
    with pytest.raises(ConfigError, match="没有 sources"):
        load_sources_config(path)


def test_web_source_requires_urls(tmp_path):
    path = _write(
        tmp_path,
        """
sources:
  - type: web
    urls: []
""",
    )
    with pytest.raises(ConfigError, match="url"):
        load_sources_config(path)


def test_sql_source_requires_content_columns():
    with pytest.raises(ValueError):
        SqlSource(
            dsn="sqlite:///x.db",
            query="SELECT 1",
            id_column="id",
            content_columns=[],
        )


def test_source_keys_are_stable_and_distinct(tmp_path):
    source = FileSource(path="data/docs")
    assert source_key_for(source) == source_key_for(FileSource(path="data/docs"))
    assert source_key_for(source) != source_key_for(FileSource(path=str(tmp_path)))
    assert source_key_for(WebSource(urls=["https://a.com"])).startswith("web:")
    assert source_key_for(
        SqlSource(dsn="sqlite:///x.db", query="SELECT 1", id_column="id", content_columns=["id"])
    ).startswith("sql:")


def test_default_sources_config_points_to_docs():
    config = default_sources_config()
    assert len(config.sources) == 1
    assert isinstance(config.sources[0], FileSource)
    assert config.sources[0].path == str(DOCS_DIR)
