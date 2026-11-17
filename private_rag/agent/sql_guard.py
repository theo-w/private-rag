"""运行时 SQL 的只读护栏。

Agentic RAG 会让模型自己写 SQL 去查业务库，这一步必须先把「只读」落到代码里。
本模块做四件事：只允许单条 SELECT/WITH 语句、拒绝一切写操作关键字、
限制表必须在白名单内、强制一个不超过上限的 LIMIT。

它是纵深防御的一层，不是沙箱：生产环境仍应使用只读数据库账号。
"""

from __future__ import annotations

import re
from typing import Optional, Sequence, Set

# 出现即拒绝。覆盖 DML/DDL/DCL、事务控制，以及 SQLite/MySQL/PostgreSQL 的
# 文件与元数据操作（ATTACH、PRAGMA 等）。
FORBIDDEN_KEYWORDS: tuple[str, ...] = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "replace",
    "merge",
    "upsert",
    "into",
    "attach",
    "detach",
    "pragma",
    "vacuum",
    "reindex",
    "analyze",
    "grant",
    "revoke",
    "call",
    "execute",
    "exec",
    "set",
    "begin",
    "commit",
    "rollback",
    "savepoint",
    "load",
    "shutdown",
)

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STATEMENT_START = re.compile(r"^\s*(with|select)\b", re.IGNORECASE)
_TABLE_REF = re.compile(r"\b(?:from|join)\b", re.IGNORECASE)
_LIMIT_CLAUSE = re.compile(
    r"\blimit\s+(?:(\d+)\s*,\s*(\d+)|(\d+)(?:\s+offset\s+(\d+))?)\s*$",
    re.IGNORECASE,
)
# FROM 子句里的表清单，直到遇到下一个子句关键字为止
_FROM_LIST_END = re.compile(
    r"\b(?:where|group\s+by|order\s+by|limit|having|union|intersect|except|join|on|window)\b",
    re.IGNORECASE,
)
_IDENT = re.compile(r"^[`\"\[]?[A-Za-z_][\w.$]*[`\"\]]?$")
# WITH 子句定义的临时视图名：它们不是真表，不该参与白名单比对
_CTE_NAME = re.compile(
    r"(?:\bwith\b|,)\s*([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s+as\s*\(",
    re.IGNORECASE,
)


class SqlGuardError(ValueError):
    """SQL 未通过只读护栏。消息面向用户，不包含连接信息。"""


def strip_comments(sql: str) -> str:
    """去掉注释；注释里藏关键字是常见的绕过手法，先去掉再判定。"""
    text = _BLOCK_COMMENT.sub(" ", sql)
    text = _LINE_COMMENT.sub(" ", text)
    return " ".join(text.split()).strip()


def normalize_table(name: str) -> str:
    """去掉反引号/方括号与 schema 前缀，统一成小写便于白名单比对。"""
    cleaned = name.strip().strip("`\"[]")
    if "." in cleaned:
        cleaned = cleaned.rsplit(".", 1)[-1]
    return cleaned.lower()


def extract_tables(sql: str) -> Set[str]:
    """粗略提取被引用的表名（已归一化）。

    只解析 FROM/JOIN 后的表清单，够用于护栏；子查询里的表由内层 FROM 命中，
    解析不出来的形式一律视为「不在白名单」，由调用方拒绝。
    """
    text = strip_comments(sql)
    tables: Set[str] = set()
    for match in _TABLE_REF.finditer(text):
        rest = text[match.end():]
        end = _FROM_LIST_END.search(rest)
        clause = rest[: end.start()] if end else rest
        # 子句到右括号为止：CTE 与子查询的 ')' 会粘在表名上（FROM products)）
        clause = clause.split(")")[0]
        # `FROM a, b AS x` 只要逗号前的表名部分
        for piece in clause.split(","):
            token = piece.strip().split()[0] if piece.strip() else ""
            token = token.rstrip(";")
            if not token or token in {"(", "distinct"} or token.startswith("("):
                continue
            # 去掉引号包裹：`db`.`products` 这类写法中间也带引号
            cleaned = token.replace("`", "").replace('"', "").replace("[", "").replace("]", "")
            if not _IDENT.match(cleaned):
                continue
            tables.add(normalize_table(cleaned))
    return tables


def extract_cte_names(sql: str) -> Set[str]:
    """提取 WITH 子句定义的临时视图名（已归一化）。"""
    return {normalize_table(match.group(1)) for match in _CTE_NAME.finditer(strip_comments(sql))}


def _find_forbidden(text: str) -> Optional[str]:
    lowered = text.lower()
    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", lowered):
            return keyword
    return None


def enforce_limit(sql: str, max_rows: int) -> tuple[str, Optional[int]]:
    """保证语句带一个不超过 max_rows 的 LIMIT。返回 (sql, 被压缩掉的原 LIMIT)。

    正则锚定在结尾，匹配到的就是完整 LIMIT 子句，后面不会再有内容。
    """
    match = _LIMIT_CLAUSE.search(sql)
    if match is None:
        return f"{sql} LIMIT {max_rows}", None

    head = sql[: match.start()]
    if match.group(2) is not None:
        # MySQL 风格 LIMIT offset, count
        offset, requested = match.group(1), int(match.group(2))
        limit = min(requested, max_rows)
        clause = f"LIMIT {offset}, {limit}"
    else:
        requested = int(match.group(3))
        limit = min(requested, max_rows)
        clause = f"LIMIT {limit}"
        if match.group(4) is not None:
            clause += f" OFFSET {match.group(4)}"

    clamped = requested if requested > limit else None
    return f"{head}{clause}".strip(), clamped


def sanitize_sql(
    sql: str,
    *,
    allowed_tables: Sequence[str],
    max_rows: int,
) -> tuple[str, list[str]]:
    """校验并改写 SQL。通过则返回 (可执行 SQL, 警告列表)，不通过抛 SqlGuardError。"""
    text = strip_comments(sql or "")
    if not text:
        raise SqlGuardError("SQL 为空")

    text = text.rstrip(";").strip()
    if not text:
        raise SqlGuardError("SQL 为空")
    if ";" in text:
        raise SqlGuardError("只允许单条语句，不能包含分号分隔的多条 SQL")
    if not _STATEMENT_START.match(text):
        raise SqlGuardError("只允许 SELECT 或 WITH 开头的查询")

    keyword = _find_forbidden(text)
    if keyword:
        raise SqlGuardError(f"SQL 中包含写操作或元数据操作关键字：{keyword}")

    allowed = {normalize_table(name) for name in allowed_tables}
    if not allowed:
        raise SqlGuardError("没有配置允许查询的表，运行时 SQL 不可用")

    # CTE 名字只是查询内的临时视图，真实表在 CTE  body 里，已被 FROM 命中
    referenced = extract_tables(text) - extract_cte_names(text)
    if not referenced:
        raise SqlGuardError("无法从 SQL 中确认查询的表，拒绝执行")
    blocked = sorted(referenced - allowed)
    if blocked:
        raise SqlGuardError(
            "SQL 引用了未授权的表：" + "、".join(blocked) + "；允许的表：" + "、".join(sorted(allowed))
        )

    # 与 enforce_limit 用同一个判断：只有结尾没有 LIMIT 时才是「补上」
    had_limit = _LIMIT_CLAUSE.search(text) is not None
    guarded, clamped = enforce_limit(text, max_rows)
    warnings: list[str] = []
    if clamped is not None:
        warnings.append(f"LIMIT 被压缩：{clamped} -> {min(clamped, max_rows)}")
    elif not had_limit:
        warnings.append(f"自动补上 LIMIT {max_rows}")
    return guarded, warnings
