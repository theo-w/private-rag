"""树索引：按标题层级把文档组织成一棵树，命中以后还原成「带层级路径的章节上下文」。

动机（v8.4 之后）：父块扩展用的是固定字符窗口（默认 2000 字符、不重叠），
窗口边界与文档结构无关，所以会出现「父块正好断在『这里有五个关键角色：』、
角色列表掉进下一个父块」这类截断（v5 报告的 q07 就是这条）。树索引改用标题边界：
一个命中块所属的章节，就是送给模型的上下文，并且把「文档 › 章 › 节」这条路径
一起写进上下文，让模型知道这段材料在文档里的位置。

存放方式：与 BM25 词法索引同一套做法——离线从已入库的 Qdrant payload 构建，
落盘成独立索引文件，不改动现有块的写入格式，也不新建 Qdrant 集合。

构建依据：块元数据里并没有标题层级字段，标题只存在于块文本中。因此先用
`parent_index` 把同一文档的父块按顺序拼回「结构文本」，再在结构文本里扫描
Markdown 标题行切出章节边界，最后按偏移把每个父块（以及它的子块）归到最深一级
章节节点上。
"""

from __future__ import annotations

import os
import pickle
import tempfile
import uuid
from bisect import bisect_right
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional, Sequence

from qdrant_client import QdrantClient

from private_rag.config import TREE_INDEX_PATH, TREE_SECTION_CHARS
from private_rag.retrieval.hybrid import RetrievedChunk
from private_rag.store import scroll_all

# 上下文里层级路径那一行的前缀；评估与调试都按这个前缀识别树路径
PATH_PREFIX = "章节路径: "


def parse_heading(line: str) -> Optional[tuple[int, str]]:
    """把一行解析成 (层级, 标题)。

    判定规则与 `chunking._split_by_markdown_structure` 保持一致（以 `#` 开头即算
    标题边界），否则章节边界与父块边界会错位，偏移归属就不可靠。
    """
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None
    level = 0
    while level < len(stripped) and stripped[level] == "#":
        level += 1
    if level > 6:
        # 七个以上的 # 不是标题（Markdown 只认 1–6 级）
        return None
    title = stripped[level:].strip()
    # ATX 关闭式写法「## 标题 ##」：去掉尾部多余的 #
    while title.endswith("#"):
        title = title[:-1].rstrip()
    if not title:
        # 光一个 # 没有标题文字：切分器会把这行当边界，但这里不值得为它建节点
        return None
    return (level, title)


@dataclass(frozen=True)
class TreeNode:
    """树上的一个节点：文档根节点，或某一级标题对应的章节。"""

    node_id: str
    doc_id: str
    parent_node_id: Optional[str]
    level: int
    title: str
    # 从文档标题到本节点的标题链，长度 = level + 1
    path: tuple[str, ...]
    # 本节点「自己那一节」的正文：含标题行，不含子章节的文字
    text: str
    # 在结构文本里的范围，用于把块归到节点上
    start: int
    end: int
    chunk_ids: list[str] = field(default_factory=list)
    page: Optional[int] = None
    table: Optional[str] = None

    @property
    def is_root(self) -> bool:
        return self.level == 0


@dataclass
class TreeIndex:
    """一份可持久化的文档树；内容与 Qdrant payload 保持一致。"""

    VERSION = 1

    nodes: dict[str, TreeNode]
    # 命中块 -> 所属节点，查询侧只查这一张表
    chunk_node: dict[str, str]

    def __len__(self) -> int:
        return len(self.nodes)

    @property
    def chunk_count(self) -> int:
        return len(self.chunk_node)

    def node(self, node_id: str) -> Optional[TreeNode]:
        return self.nodes.get(node_id)

    def node_of_chunk(self, point_id: str) -> Optional[TreeNode]:
        node_id = self.chunk_node.get(str(point_id))
        return self.nodes.get(node_id) if node_id else None

    def doc_ids(self) -> list[str]:
        return sorted({node.doc_id for node in self.nodes.values() if node.is_root})

    def path_label(self, node: TreeNode) -> str:
        """「文档 › 章 › 节」形式的路径文本；层级越深，路径越长。"""
        return " › ".join(str(part) for part in node.path if str(part).strip())

    def stats(self) -> dict:
        roots = [node for node in self.nodes.values() if node.is_root]
        return {
            "docs": len(roots),
            "nodes": len(self.nodes),
            "sections": len(self.nodes) - len(roots),
            "chunks": self.chunk_count,
        }

    def save(self, path: Path = TREE_INDEX_PATH) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "nodes": self.nodes,
            "chunk_node": self.chunk_node,
        }
        # 原子替换，避免进程中断留下半个索引（与 lexical.py 同一处理）
        with tempfile.NamedTemporaryFile("wb", dir=str(path.parent), delete=False) as handle:
            pickle.dump(payload, handle, protocol=4)
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
        return path

    @classmethod
    def load(cls, path: Path = TREE_INDEX_PATH) -> Optional["TreeIndex"]:
        if not path.exists():
            return None
        try:
            with path.open("rb") as handle:
                payload = pickle.load(handle)
        except Exception:
            return None
        if payload.get("version") != cls.VERSION:
            return None
        nodes = payload.get("nodes") or {}
        if not isinstance(nodes, dict) or not all(
            isinstance(node, TreeNode) for node in nodes.values()
        ):
            return None
        return cls(nodes=nodes, chunk_node=payload.get("chunk_node") or {})


# --------------------------------------------------------------------------- #
# 构建
# --------------------------------------------------------------------------- #


def _node_id(doc_id: str, level: int, start: int, title: str) -> str:
    """节点 ID 只由文档身份与章节位置决定，重建以后保持稳定。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:{level}:{start}:{title}"))


def _as_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class _Unit:
    """结构文本里的一段：一个父块，没有父子块索引时退化成一个子块。"""

    key: str
    order: int
    text: str
    start: int = 0
    end: int = 0


@dataclass
class _Slice:
    """一个章节切出来的连续片段：从它的标题行到下一个任意级标题行。"""

    level: int
    title: str
    start: int
    end: int


def _scan_sections(structure: str) -> list[_Slice]:
    """扫描结构文本里的标题行，切出章节片段。

    片段按出现顺序连续排列、互不重叠：某一级标题下面还有更小的标题时，
    父章节的片段只到第一个子标题为止，子章节另成节点。这样每个命中块拿到的
    是「离它最近的那一级标题」之间的正文，正是父块固定窗口缺的那条边界。
    代码块围栏内部不算标题（入库时正文里的代码块已被切分器丢弃，这里再防一道）。
    """
    sections: list[_Slice] = []
    offset = 0
    in_code = False
    for line in structure.split("\n"):
        line_len = len(line) + 1  # 含换行符
        if line.strip().startswith("```"):
            in_code = not in_code
            offset += line_len
            continue
        if not in_code:
            heading = parse_heading(line)
            if heading is not None:
                level, title = heading
                if sections:
                    sections[-1].end = offset
                sections.append(
                    _Slice(level=level, title=title, start=offset, end=len(structure))
                )
        offset += line_len
    return sections


def _doc_units(doc_records: Sequence[dict]) -> tuple[list[_Unit], dict[str, str]]:
    """一个文档的结构单元，以及子块 -> 父块的映射。

    父块之间不重叠，按 `parent_index` 拼起来就能还原正文结构；没有父块信息
    （老数据或非父子切分）时退化成按 `chunk_index` 排的子块，子块之间可能有重叠，
    此时章节正文会带上重复的开头，属于已记录的降级。
    """
    parents: dict[str, _Unit] = {}
    child_parent: dict[str, str] = {}
    orphans: list[_Unit] = []
    for record in doc_records:
        point_id = str(record.get("point_id"))
        parent = record.get("parent")
        if isinstance(parent, dict) and parent.get("id"):
            parent_id = str(parent["id"])
            child_parent[point_id] = parent_id
            parents.setdefault(
                parent_id,
                _Unit(
                    key=parent_id,
                    order=_as_int(parent.get("index")) or 0,
                    text=str(parent.get("text") or ""),
                ),
            )
            continue
        metadata = record.get("metadata") or {}
        orphans.append(
            _Unit(
                key=point_id,
                order=_as_int(metadata.get("chunk_index")) or 0,
                text=str(record.get("text") or ""),
            )
        )
    if parents:
        return sorted(parents.values(), key=lambda unit: unit.order), child_parent
    return sorted(orphans, key=lambda unit: unit.order), child_parent


def build_index(records: Sequence[dict]) -> TreeIndex:
    """从已入库的块记录构建树索引（纯函数，不打开 Qdrant）。

    每条记录的形状与 `lexical.build_from_store` 一致：
    `{"point_id", "text", "metadata", "parent"}`。
    """
    by_doc: dict[str, list[dict]] = {}
    for record in records:
        metadata = record.get("metadata") or {}
        doc_id = str(metadata.get("doc_id") or record.get("point_id") or "")
        if not doc_id:
            continue
        by_doc.setdefault(doc_id, []).append(record)

    nodes: dict[str, TreeNode] = {}
    chunk_node: dict[str, str] = {}

    for doc_id, doc_records in sorted(by_doc.items()):
        first_meta = doc_records[0].get("metadata") or {}
        doc_title = str(
            first_meta.get("title") or first_meta.get("file_name") or doc_id
        )
        units, child_parent = _doc_units(doc_records)
        if not units:
            continue

        # 1) 按顺序拼结构文本，同时记下每个父块的范围
        cursor = 0
        for unit in units:
            unit.start = cursor
            unit.end = cursor + len(unit.text)
            cursor = unit.end + 1  # +1 是连接用的换行符
        structure = "\n".join(unit.text for unit in units)
        total = len(structure)

        # 2) 标题扫描出章节片段；标题栈决定路径与父子关系
        slices = _scan_sections(structure)
        root_id = _node_id(doc_id, 0, 0, doc_title)
        pending: dict[str, dict] = {
            root_id: {
                "doc_id": doc_id,
                "parent_node_id": None,
                "level": 0,
                "title": doc_title,
                "path": (doc_title,),
                "text": structure[: slices[0].start].strip() if slices else structure.strip(),
                "start": 0,
                "end": total,
                "chunk_ids": [],
                "page": None,
                "table": None,
            }
        }

        stack: list[tuple[_Slice, str]] = []
        slice_nodes: list[str] = []
        unit_owner: dict[str, str] = {}
        for section in slices:
            while stack and stack[-1][0].level >= section.level:
                stack.pop()
            parent_id = stack[-1][1] if stack else root_id
            node_id = _node_id(doc_id, section.level, section.start, section.title)
            pending[node_id] = {
                "doc_id": doc_id,
                "parent_node_id": parent_id,
                "level": section.level,
                "title": section.title,
                "path": pending[parent_id]["path"] + (section.title,),
                "text": structure[section.start:section.end].strip(),
                "start": section.start,
                "end": section.end,
                "chunk_ids": [],
                "page": None,
                "table": None,
            }
            slice_nodes.append(node_id)
            stack.append((section, node_id))

        # 片段互不重叠，所以「起点落在哪个片段」就是每个结构单元的章节
        starts = [section.start for section in slices]

        def slice_at(position: int) -> Optional[str]:
            found = bisect_right(starts, position) - 1
            if found >= 0 and position < slices[found].end:
                return slice_nodes[found]
            return None

        unit_by_key = {unit.key: unit for unit in units}
        for unit in units:
            owner = slice_at(unit.start)
            if owner is not None:
                unit_owner[unit.key] = owner

        # 3) 每个块优先归到自己正文所在的最深一级章节，定位不到就退回父块的章节，
        #    父块也归属不到（跨片段、重叠）才落回文档根节点。归属前先验一次
        #    「能不能在章节正文里找到这个块」，与查询时的判定同一口径，
        #    免得归到一个还原不出上下文的节点上。
        for record in doc_records:
            point_id = str(record.get("point_id"))
            metadata = record.get("metadata") or {}
            chunk_text = str(record.get("text") or "")
            unit_key = child_parent.get(point_id, point_id)
            unit = unit_by_key.get(unit_key)
            node_id = unit_owner.get(unit_key, root_id)
            if unit is not None:
                hit = locate_chunk(chunk_text, unit.text)
                if hit is not None:
                    deeper = slice_at(unit.start + hit[0])
                    if deeper is not None and locate_chunk(
                        chunk_text, pending[deeper]["text"]
                    ) is not None:
                        node_id = deeper
            pending[node_id]["chunk_ids"].append(point_id)
            chunk_node[point_id] = node_id
            fields = pending[node_id]
            if fields["page"] is None:
                fields["page"] = _as_int(metadata.get("page"))
            if fields["table"] is None:
                table = metadata.get("table")
                fields["table"] = str(table) if table is not None else None

        for node_id, fields in pending.items():
            nodes[node_id] = TreeNode(
                node_id=node_id,
                doc_id=fields["doc_id"],
                parent_node_id=fields["parent_node_id"],
                level=fields["level"],
                title=fields["title"],
                path=tuple(fields["path"]),
                text=fields["text"],
                start=fields["start"],
                end=fields["end"],
                chunk_ids=list(fields["chunk_ids"]),
                page=fields["page"],
                table=fields["table"],
            )

    return TreeIndex(nodes=nodes, chunk_node=chunk_node)


def build_from_store(
    client: QdrantClient,
    path: Path = TREE_INDEX_PATH,
    logger=None,
) -> int:
    """从 Qdrant 全量 payload 重建树索引，返回节点数。"""
    records = []
    for record in scroll_all(client, with_payload=True):
        payload = record.payload or {}
        documents = {
            "point_id": str(record.id),
            "text": payload.get("page_content", ""),
            "metadata": payload.get("metadata", {}),
            "parent": payload.get("parent"),
        }
        records.append(documents)
    index = build_index(records)
    index.save(path)
    if logger:
        stats = index.stats()
        logger(
            f"树索引写入 {path}（{stats['docs']} 个文档 / {stats['nodes']} 个节点 / "
            f"{stats['chunks']} 个块）"
        )
    return len(index)


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #


def _normalize(text: str) -> str:
    """去掉所有空白，用于在章节正文里定位命中块。"""
    return "".join(str(text).split())


def _offset_map(text: str) -> tuple[str, list[int]]:
    """返回（去空白文本, 每个去空白字符在原串里的位置）。"""
    chars: list[str] = []
    positions: list[int] = []
    for position, char in enumerate(text):
        if not char.isspace():
            chars.append(char)
            positions.append(position)
    return "".join(chars), positions


def locate_chunk(chunk_text: str, section_text: str) -> Optional[tuple[int, int]]:
    """在章节正文里找到命中块的位置（原串偏移）。定位不到时返回 None。"""
    needle = _normalize(chunk_text)
    if not needle:
        return None
    normalized, positions = _offset_map(section_text)
    found = normalized.find(needle)
    if found < 0:
        return None
    return positions[found], positions[found + len(needle) - 1] + 1


@dataclass(frozen=True)
class TreeContext:
    """一次还原的结果：某章节围绕命中块截出的一段上下文，带层级路径。"""

    node_id: str
    label: str
    text: str


def window_around_hit(
    section_text: str,
    hit: tuple[int, int],
    *,
    max_chars: int,
) -> str:
    """以命中块为中心在章节正文里开窗，保证命中块整段落在窗口内。

    窗口不够装整段章节时，先向命中块前面借、再向后面借：前面通常是章节的
    开头与铺垫，比后面的下一段落更有用。
    """
    if max_chars <= 0:
        return ""
    start, end = hit
    span = end - start
    if span >= max_chars:
        return section_text[start:start + max_chars]
    lead = (max_chars - span) // 2
    tail = max_chars - span - lead
    window_start = max(0, start - lead)
    window_end = min(len(section_text), end + tail)
    # 一侧被文档边界挡住时，把剩余额度让给另一侧
    if window_start == 0:
        window_end = min(len(section_text), window_end + (lead - (start - window_start)))
    elif window_end == len(section_text):
        window_start = max(0, window_start - (tail - (window_end - end)))
    return section_text[window_start:window_end].strip()


def tree_context_for_chunk(
    chunk: RetrievedChunk,
    index: TreeIndex,
    *,
    max_chars: int,
) -> Optional[TreeContext]:
    """把一个命中块还原成它所属章节的上下文。没有节点、或章节正文里定位不到该块时返回 None。"""
    node = index.node_of_chunk(chunk.point_id)
    if node is None or not node.text or max_chars <= 0:
        return None
    hit = locate_chunk(chunk.text, node.text)
    if hit is None:
        return None
    text = window_around_hit(node.text, hit, max_chars=max_chars)
    if not text:
        return None
    return TreeContext(node_id=node.node_id, label=index.path_label(node), text=text)


def attach_tree_context(
    chunks: Sequence[RetrievedChunk],
    index: TreeIndex,
    *,
    max_chars: int,
    section_max_chars: int = TREE_SECTION_CHARS,
) -> list[RetrievedChunk]:
    """第二路上下文：把命中块的文本换成「章节路径 + 该章节围绕命中块的一段」。

    - 与 `expand_parents` 共用同一个总字符预算，因此两路是并列关系，不会把上下文撑大；
    - 章节正文放不下（或定位不到命中块）时，退回原来的父块/子块文本；
    - 同一章节被多个块命中时只还原一次，后面的块若内容已被这段覆盖就丢掉，
      没被覆盖的仍保留；
    - 引用摘要仍用实际命中的子块（`snippet_text`），与父块扩展同一口径。
    """
    expanded: list[RetrievedChunk] = []
    used = 0
    covered: dict[str, str] = {}

    for chunk in chunks:
        remaining = max_chars - used
        if remaining <= 0:
            break

        context = tree_context_for_chunk(chunk, index, max_chars=section_max_chars)
        if context is not None and context.node_id in covered:
            if _normalize(chunk.text) in covered[context.node_id]:
                # 这段内容已经在前面那节里送过了，再送一遍只是占预算
                continue
            context = None

        snippet = chunk.snippet_text or chunk.text
        if context is not None:
            body = f"{PATH_PREFIX}{context.label}\n{context.text}"
            if len(body) <= remaining:
                expanded.append(
                    replace(
                        chunk,
                        text=body,
                        snippet_text=snippet,
                        tree_node_id=context.node_id,
                    )
                )
                used += len(body)
                covered[context.node_id] = _normalize(context.text)
                continue

        # 退回父块扩展同一口径：装得下父块就送父块，否则送命中的子块，必要时截断
        parent_text = chunk.parent_text
        if parent_text and len(parent_text) <= remaining:
            text = parent_text
        elif len(chunk.text) <= remaining:
            text = chunk.text
        else:
            text = chunk.text[:remaining]
        if not text:
            continue
        expanded.append(replace(chunk, text=text, snippet_text=snippet))
        used += len(text)

    return expanded
