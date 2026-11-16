"""只读演示服务的接口测试（不依赖 Ollama/Qdrant/网络）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from private_rag.api.readonly import (
    SnapshotError,
    create_readonly_app,
    load_snapshot,
)

DOCUMENTS = [
    {
        "doc_id": "file:data/docs:演示-扫描件模拟.pdf#page=1",
        "text": "产品 SKU-2026-001 单价 459.00 元，库存 12 件，安全库存 25 件。",
        "title": "演示-扫描件模拟.pdf 第 1 页",
        "source_type": "file",
        "source_uri": "data/docs/演示-扫描件模拟.pdf",
        "source_key": "file:data/docs",
        "updated_at": "2026-11-16",
        "tags": ["local", "demo"],
        "content_hash": "a" * 64,
        "page": 1,
        "table": None,
        "file_name": "演示-扫描件模拟.pdf",
        "scanned": True,
        "char_count": 40,
    },
    {
        "doc_id": "file:data/docs:演示-商品库存表.pdf#page=1#table=1",
        "text": "SKU,单价,库存,安全库存\nSKU-2026-001,459.00,12,25",
        "title": "演示-商品库存表.pdf 第 1 页 表格 1",
        "source_type": "file",
        "source_uri": "data/docs/演示-商品库存表.pdf",
        "source_key": "file:data/docs",
        "updated_at": "2026-11-16",
        "tags": ["local", "demo"],
        "content_hash": "b" * 64,
        "page": 1,
        "table": "演示-商品库存表_table_1",
        "file_name": "演示-商品库存表.pdf",
        "scanned": False,
        "char_count": 42,
    },
    {
        "doc_id": "sql:x#row=1",
        "text": "橙子富含维生素 C，适合日常补充维 C。",
        "title": "橙子",
        "source_type": "sql",
        "source_uri": "sqlite:///data/sql/demo.db",
        "source_key": "sql:x",
        "updated_at": "2026-01-10",
        "tags": ["business", "demo"],
        "content_hash": "c" * 64,
        "page": None,
        "table": "demo.products",
        "file_name": None,
        "scanned": False,
        "char_count": 20,
    },
]


def write_snapshot(tmp_path: Path) -> Path:
    path = tmp_path / "demo_snapshot.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "app_version": "0.6.0",
                "source_config": "config/sources.demo.yaml",
                "mode": "readonly",
                "retrieval": "bm25",
                "stats": {
                    "documents": len(DOCUMENTS),
                    "scanned_documents": 1,
                    "table_documents": 2,
                    "total_chars": 102,
                },
                "documents": DOCUMENTS,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def client(tmp_path) -> TestClient:
    app = create_readonly_app(snapshot_path=write_snapshot(Path(tmp_path)))
    return TestClient(app)


def test_ready_reports_snapshot_stats(client):
    response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["mode"] == "readonly"
    assert body["checks"]["documents"] == 3
    # v6 的两类产物要在就绪状态里可见
    assert body["checks"]["scanned_documents"] == 1
    assert body["checks"]["table_documents"] == 2
    # 模型三项必须显式为 False，避免误认为云端可问答
    assert body["checks"]["embedding_model"] is False
    assert body["checks"]["reranker"] is False
    assert body["checks"]["llm"] is False


def test_ready_503_when_snapshot_missing(tmp_path):
    app = create_readonly_app(snapshot_path=Path(tmp_path) / "missing.json")
    client = TestClient(app)
    response = client.get("/ready")
    assert response.status_code == 503
    assert "build_demo_snapshot" in response.json()["reasons"][0]


def test_load_snapshot_missing_raises(tmp_path):
    with pytest.raises(SnapshotError):
        load_snapshot(Path(tmp_path) / "nope.json")


def test_search_citation_carries_page_and_table(client):
    response = client.post("/api/v1/search", json={"query": "SKU-2026-001 库存", "top_k": 5})
    assert response.status_code == 200
    citations = response.json()["citations"]
    assert citations, "命中不应为空"
    pages = {citation["page"] for citation in citations}
    tables = {citation["table"] for citation in citations}
    assert 1 in pages
    assert "演示-商品库存表_table_1" in tables
    assert citations[0]["index"] == 1
    assert response.json()["meta"]["answer"] == "disabled"


def test_search_zero_overlap_returns_empty(client):
    response = client.post("/api/v1/search", json={"query": "zzzqqq 无意义词"})
    assert response.status_code == 200
    assert response.json()["citations"] == []
    assert response.json()["retrieved"] == 0


def test_search_filters_by_source_type(client):
    response = client.post(
        "/api/v1/search", json={"query": "维生素 C", "source_type": "sql"}
    )
    citations = response.json()["citations"]
    assert citations and all(item["source_type"] == "sql" for item in citations)


def test_documents_list_and_detail(client):
    listed = client.get("/api/v1/documents?limit=2").json()
    assert listed["total"] == 3
    assert len(listed["documents"]) == 2

    # doc_id 含 ":" 与 "#"，只能走查询参数，否则会被 URL 当 fragment 截断
    doc_id = DOCUMENTS[0]["doc_id"]
    detail = client.get("/api/v1/document", params={"doc_id": doc_id})
    assert detail.status_code == 200
    assert detail.json()["scanned"] is True

    missing = client.get("/api/v1/document", params={"doc_id": "file:nope"})
    assert missing.status_code == 404


@pytest.mark.parametrize("path", ["/api/v1/chat", "/api/v1/chat/stream"])
def test_generation_is_disabled(client, path):
    response = client.post(path, json={"question": "库存多少"})
    assert response.status_code == 409
    assert response.json()["error"] == "generation_disabled"


def test_demo_page_served(client):
    response = client.get("/demo")
    assert response.status_code == 200
    assert "只读演示" in response.text


def test_token_required_when_configured(tmp_path):
    app = create_readonly_app(
        snapshot_path=write_snapshot(Path(tmp_path)), token="s3cret"
    )
    client = TestClient(app)
    assert client.get("/ready").status_code == 200
    assert client.get("/api/v1/documents").status_code == 401
    ok = client.get("/api/v1/documents", headers={"X-Demo-Token": "s3cret"})
    assert ok.status_code == 200
    assert ok.json()["total"] == 3


def test_readonly_index_ranks_like_lexical_index(tmp_path):
    """只读索引与线上词法索引同算法：同一批文档给出同样的排序。"""

    from private_rag.api.readonly import ReadonlyIndex, load_snapshot
    from private_rag.retrieval.lexical import LexicalIndex

    snapshot = load_snapshot(write_snapshot(Path(tmp_path)))
    query = "SKU-2026-001 库存 单价"

    readonly_titles = [
        citation["title"] for citation in ReadonlyIndex(snapshot).search(query, top_k=3)[0]
    ]
    lexical = LexicalIndex(
        [
            {
                "point_id": document["doc_id"],
                "text": document["text"],
                "metadata": {},
            }
            for document in DOCUMENTS
        ]
    )
    titles = {document["doc_id"]: document["title"] for document in DOCUMENTS}
    lexical_titles = [
        titles[hit.point_id]
        for hit in sorted(lexical.search(query, top_k=3), key=lambda hit: -hit.score)
    ]

    assert readonly_titles == lexical_titles
    assert readonly_titles, "对齐用的排序不该为空"


def test_root_serves_demo_page(client):
    """分享出去的链接通常就是根地址，所以根路径要直接是演示页而不是 JSON。"""
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "只读演示" in response.text


def test_service_info_available_at_info(client):
    """服务信息挪到 /info，仍能被外部脚本读到。"""
    response = client.get("/info")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "readonly"
    assert body["models"] == "off"
    assert body["demo"] == "/demo"
