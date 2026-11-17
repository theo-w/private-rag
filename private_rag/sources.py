"""sources.yaml 的解析、校验与环境变量插值。"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Annotated, Any, List, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from private_rag.config import DOCS_DIR, ROOT

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(RuntimeError):
    """配置缺失或格式错误。"""


def interpolate_env(text: str, environ: Optional[dict[str, str]] = None) -> str:
    """把 ${VAR} 或 ${VAR:-default} 替换为环境变量值，避免把凭证写进仓库。"""
    env = os.environ if environ is None else environ

    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        if name in env:
            return env[name]
        if default is not None:
            return default
        raise ConfigError(f"环境变量 {name} 未设置（可在 {ROOT}/.env 或 shell 中导出）")

    return _ENV_RE.sub(replace, text)


def _walk(value: Any, environ: Optional[dict[str, str]]) -> Any:
    if isinstance(value, str):
        return interpolate_env(value, environ)
    if isinstance(value, list):
        return [_walk(item, environ) for item in value]
    if isinstance(value, dict):
        return {key: _walk(item, environ) for key, item in value.items()}
    return value


class FileSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["file"] = "file"
    path: str
    tags: List[str] = Field(default_factory=list)
    include: List[str] = Field(default_factory=list, description="可选 glob，如 *.pdf")


class WebSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["web"] = "web"
    urls: List[str]
    timeout: float = 15.0
    tags: List[str] = Field(default_factory=list)

    @field_validator("urls")
    @classmethod
    def _urls_not_empty(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("web source 至少需要一个 url")
        return value


class SqlSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["sql"] = "sql"
    dsn: str
    query: str
    id_column: str
    content_columns: List[str]
    title_column: Optional[str] = None
    updated_at_column: Optional[str] = None
    label: Optional[str] = None
    max_rows: int = 5000
    tags: List[str] = Field(default_factory=list)

    # v7：是否允许 agentic 问答在运行时对本库发起只读查询（默认关闭，需显式开启）
    allow_runtime_query: bool = False
    # 允许运行时查询的表；留空时由连接器从 query 的 FROM 子句推导
    runtime_tables: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_columns(self) -> "SqlSource":
        if not self.content_columns:
            raise ValueError("sql source 需要至少一个 content_columns")
        # sqlite:///data/sql/demo.db 这类相对路径统一按项目根目录解析
        self.dsn = normalize_sqlite_dsn(self.dsn)
        return self


SourceEntry = Annotated[
    Union[FileSource, WebSource, SqlSource], Field(discriminator="type")
]


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: List[SourceEntry] = Field(default_factory=list)


def resolve_path(value: str) -> Path:
    """配置里的相对路径统一按项目根目录解析。"""
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def normalize_sqlite_dsn(dsn: str) -> str:
    """SQLAlchemy 的 sqlite 相对路径依赖当前工作目录，这里统一改为项目绝对路径。"""
    prefix = "sqlite:///"
    if dsn.startswith(prefix) and not dsn.startswith("sqlite:////"):
        raw_path = dsn[len(prefix):]
        if raw_path and not raw_path.startswith(":memory:"):
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                return f"sqlite:///{(ROOT / path).resolve()}"
    return dsn


def source_key_for(source: Union[FileSource, WebSource, SqlSource]) -> str:
    """一个 source 配置项的稳定标识，用于统计与 --prune。"""
    if isinstance(source, FileSource):
        return f"file:{resolve_path(source.path)}"
    if isinstance(source, WebSource):
        digest = hashlib.sha1("\n".join(source.urls).encode("utf-8")).hexdigest()[:12]
        return f"web:{digest}"
    digest = hashlib.sha1(f"{source.dsn}\n{source.query}".encode("utf-8")).hexdigest()[:12]
    return f"sql:{digest}"


def load_sources_config(
    path: Union[str, Path], environ: Optional[dict[str, str]] = None
) -> SourcesConfig:
    config_path = resolve_path(str(path))
    if not config_path.exists():
        raise ConfigError(f"配置文件不存在：{config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML 解析失败：{exc}") from exc

    try:
        payload = _walk(raw, environ)
        config = SourcesConfig(**payload)
    except ValidationError as exc:
        raise ConfigError(f"配置校验失败：{exc}") from exc

    if not config.sources:
        raise ConfigError(f"配置中没有 sources：{config_path}")
    return config


def default_sources_config() -> SourcesConfig:
    """未指定 --config 时保持原有用法：只扫描 data/docs。"""
    return SourcesConfig(sources=[FileSource(path=str(DOCS_DIR))])
