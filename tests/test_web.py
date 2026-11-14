"""网页连接器测试：用本地 HTTP server 验证抓取、正文抽取与元数据。"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

from private_rag.ingestion.web import load_web_documents
from private_rag.sources import WebSource, source_key_for

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "web"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass


@pytest.fixture()
def fixture_url():
    handler = functools.partial(_QuietHandler, directory=str(FIXTURES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/demo.html"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_load_web_document(fixture_url):
    source = WebSource(urls=[fixture_url], timeout=5, tags=["web"])
    documents, errors = load_web_documents(source, source_key_for(source))

    assert errors == []
    assert len(documents) == 1
    document = documents[0]
    assert document.source_type == "web"
    assert document.source_uri == fixture_url
    assert document.source_key == source_key_for(source)
    assert document.tags == ["web"]
    assert "本地知识库" in document.text
    assert "BM25" in document.text
    assert "示例网页" in document.title
    assert document.extra["url"] == fixture_url


def test_bad_url_is_reported_without_blocking(tmp_path):
    source = WebSource(urls=["http://127.0.0.1:1/missing.html"], timeout=0.5, tags=["web"])
    documents, errors = load_web_documents(source, source_key_for(source))
    assert documents == []
    assert len(errors) == 1
    assert "抓取失败" in errors[0]
