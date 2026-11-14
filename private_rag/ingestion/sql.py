"""SQL 连接器：通过 SQLAlchemy 把查询结果的每一行变成知识库文档。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from private_rag.models import SourceDocument
from private_rag.sources import SqlSource


def redact_dsn(dsn: str) -> str:
    """只保留驱动、主机与库名，绝不把用户名/密码写进日志或元数据。"""
    try:
        url = make_url(dsn)
        database = url.database or ""
        if url.host:
            return f"{url.drivername}://{url.host}/{database}"
        return f"{url.drivername}://{database}"
    except Exception:
        return "sql://<unknown>"


def _cell(value: Any) -> str:
    return "" if value is None else str(value)


def load_sql_documents(source: SqlSource, source_key: str) -> tuple[list[SourceDocument], list[str]]:
    documents: list[SourceDocument] = []
    errors: list[str] = []

    try:
        engine = create_engine(source.dsn, pool_pre_ping=True)
    except ModuleNotFoundError as exc:
        errors.append(
            f"缺少数据库驱动（{exc.name}）。MySQL 可安装 pymysql，PostgreSQL 可安装 psycopg2-binary。"
        )
        return documents, errors
    except Exception as exc:
        errors.append(f"无法创建数据库连接：{exc}")
        return documents, errors

    try:
        with engine.connect() as connection:
            result = connection.execute(text(source.query))
            columns = list(result.keys())
            rows = result.fetchmany(source.max_rows + 1)

        if len(rows) > source.max_rows:
            rows = rows[: source.max_rows]
            errors.append(f"结果超过 max_rows={source.max_rows}，已截断")

        required = {source.id_column, *source.content_columns}
        missing = required - set(columns)
        if missing:
            errors.append(f"查询结果缺少列：{', '.join(sorted(missing))}")
            return documents, errors

        label = source.label or redact_dsn(source.dsn)
        for row in rows:
            mapping = dict(zip(columns, row))
            pk = _cell(mapping.get(source.id_column))
            if not pk:
                errors.append("跳过一条 id_column 为空的记录")
                continue

            parts = [
                f"{column}: {_cell(mapping.get(column))}"
                for column in source.content_columns
                if mapping.get(column) is not None
            ]
            body = "\n".join(parts).strip()
            if not body:
                continue

            title = (
                _cell(mapping.get(source.title_column))
                if source.title_column and mapping.get(source.title_column) is not None
                else f"{label} #{pk}"
            )
            updated_at = (
                _cell(mapping.get(source.updated_at_column))
                if source.updated_at_column and mapping.get(source.updated_at_column) is not None
                else None
            )
            documents.append(
                SourceDocument(
                    doc_id=f"{source_key}:{pk}",
                    text=body,
                    title=title,
                    source_type="sql",
                    source_uri=f"{label}#{pk}",
                    source_key=source_key,
                    updated_at=updated_at,
                    tags=list(source.tags),
                    extra={"pk": pk, "label": label, "table": source.label},
                )
            )
    except Exception as exc:
        errors.append(f"SQL 查询失败：{exc}")
    finally:
        engine.dispose()

    return documents, errors
