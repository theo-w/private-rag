"""本地文件连接器：txt / md / pdf。"""

from __future__ import annotations

import fnmatch
from datetime import datetime
from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader

from private_rag.config import ROOT
from private_rag.models import SourceDocument
from private_rag.sources import FileSource, resolve_path

SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf"}


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _updated_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def iter_files(target: Path, include: list[str] | None = None) -> list[Path]:
    if target.is_file():
        return [target]
    files = sorted(
        path for path in target.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if include:
        files = [
            path for path in files
            if any(fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(str(path), pattern)
                   for pattern in include)
        ]
    return files


def load_file_documents(source: FileSource, source_key: str) -> tuple[list[SourceDocument], list[str]]:
    """把一个文件或目录展开成文档单元；PDF 按页展开。"""
    errors: list[str] = []
    documents: list[SourceDocument] = []
    target = resolve_path(source.path)
    files = iter_files(target, source.include)
    if not files:
        errors.append(f"没有找到可入库文件：{target}")
        return documents, errors

    for path in files:
        relative = _relative(path)
        updated_at = _updated_at(path)
        try:
            if path.suffix.lower() == ".pdf":
                for index, page in enumerate(PyPDFLoader(str(path)).load(), start=1):
                    page_number = int(page.metadata.get("page", index - 1)) + 1
                    text = page.page_content.strip()
                    if not text:
                        continue
                    documents.append(
                        SourceDocument(
                            doc_id=f"file:{relative}#page={page_number}",
                            text=text,
                            title=f"{path.name} 第 {page_number} 页",
                            source_type="file",
                            source_uri=relative,
                            source_key=source_key,
                            updated_at=updated_at,
                            tags=list(source.tags),
                            extra={"page": page_number, "file_name": path.name, "format": "pdf"},
                        )
                    )
            else:
                text = path.read_text(encoding="utf-8").strip()
                if not text:
                    continue
                documents.append(
                    SourceDocument(
                        doc_id=f"file:{relative}",
                        text=text,
                        title=path.name,
                        source_type="file",
                        source_uri=relative,
                        source_key=source_key,
                        updated_at=updated_at,
                        tags=list(source.tags),
                        extra={"file_name": path.name, "format": path.suffix.lower().lstrip(".")},
                    )
                )
        except Exception as exc:  # 单个文件失败不影响其他文件
            errors.append(f"读取失败 {relative}: {exc}")

    return documents, errors
