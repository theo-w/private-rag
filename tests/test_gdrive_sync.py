"""Google Drive 语料接入测试：命令拼装、目录安全边界、下载目录清点与假文件冒烟入库。

全部不联网、不依赖真实 Google 账号：下载命令只断言拼装结果，真实执行层打桩，
入库走 tmp_path 里的 Qdrant 与确定性假向量。
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest
from langchain_core.embeddings import Embeddings
from qdrant_client import QdrantClient

from private_rag.config import GDRIVE_SYNC_DIR, ROOT
from private_rag.ingestion import gdrive
from private_rag.ingestion.pipeline import run_ingest
from private_rag.retrieval.lexical import LexicalIndex
from private_rag.sources import FileSource, source_key_for


class FakeEmbeddings(Embeddings):
    """确定性向量，保证测试不依赖 Ollama。"""

    def _vector(self, text: str) -> list:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [byte / 255.0 + 0.01 for byte in digest[:8]]

    def embed_documents(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)


def _load_script():
    """按路径加载同步 CLI，scripts/ 不是包。"""
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "sync_gdrive", root / "scripts" / "sync_gdrive.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stub_download(monkeypatch, *, returncode=0, stdout="", stderr="", side_effect=None):
    """替换下载执行层，并记下调用参数。"""
    calls = []

    def fake(argv, cwd):
        calls.append({"argv": list(argv), "cwd": Path(cwd)})
        if side_effect:
            side_effect(Path(cwd))
        return gdrive.subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(gdrive, "_run_download", fake)
    return calls


# —— 下载命令拼装 ——


def test_rclone_command_pins_export_formats_to_pdf(tmp_path):
    dest = tmp_path / "gdrive"
    plan = gdrive.build_sync_plan("rclone", "gdrive:Notes", dest, binary="rclone")
    assert plan.argv[0] == "rclone"
    assert plan.argv[1] == "copy"
    assert plan.argv[2] == "gdrive:Notes"
    assert plan.argv[3] == str(dest)
    # 入库链路只认 txt/md/pdf，Google 原生文档必须显式导出成 PDF
    assert plan.argv[4:6] == ["--drive-export-formats", "pdf"]
    assert plan.cwd == ROOT


def test_rclone_sync_mode_filters_and_dry_run(tmp_path):
    plan = gdrive.build_sync_plan(
        "rclone",
        "gdrive:Notes",
        tmp_path / "gdrive",
        mode="sync",
        include=["*.md", "*.pdf"],
        extra_args=["--drive-skip-gdocs"],
        dry_run=True,
        binary="rclone",
    )
    assert plan.argv[1] == "sync"
    assert plan.argv.count("--include") == 2
    assert "--dry-run" in plan.argv
    # 用户透传的参数排在最后，不会被脚本覆盖
    assert plan.argv[-1] == "--drive-skip-gdocs"


def test_export_formats_can_be_turned_off(tmp_path):
    plan = gdrive.build_sync_plan(
        "rclone", "gdrive:Notes", tmp_path / "gdrive", export_formats="", binary="rclone"
    )
    assert "--drive-export-formats" not in plan.argv


def test_rclone_target_must_look_like_remote_path(tmp_path):
    with pytest.raises(gdrive.SyncError, match="remote:path"):
        gdrive.build_sync_plan("rclone", "Notes", tmp_path / "gdrive", binary="rclone")


def test_missing_download_cli_gives_install_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(gdrive.shutil, "which", lambda name: None)
    with pytest.raises(gdrive.SyncError, match="brew install rclone"):
        gdrive.build_sync_plan("rclone", "gdrive:Notes", tmp_path / "gdrive")


def test_unknown_tool_is_rejected(tmp_path):
    with pytest.raises(gdrive.SyncError, match="不支持的下载工具"):
        gdrive.build_sync_plan("gsutil", "bucket/x", tmp_path / "gdrive")


def test_gdrive_download_runs_inside_dest(tmp_path, monkeypatch):
    dest = tmp_path / "gdrive"
    calls = _stub_download(monkeypatch)
    plan = gdrive.build_sync_plan("gdrive", "1AbcFolderId", dest, binary="gdrive")
    gdrive.run_sync(plan, logger=lambda _: None)
    assert calls[0]["argv"][:3] == ["gdrive", "download", "1AbcFolderId"]
    assert "--recursive" in calls[0]["argv"]
    # gdrive 各发行版的输出目录参数不一致，靠把工作目录切成目标目录来落文件
    assert calls[0]["cwd"] == dest

    single = gdrive.build_sync_plan(
        "gdrive", "1AbcFileId", dest, single_file=True, binary="gdrive"
    )
    assert "--recursive" not in single.argv


def test_gdrive_dry_run_is_refused(tmp_path):
    with pytest.raises(gdrive.SyncError, match="dry-run"):
        gdrive.build_sync_plan(
            "gdrive", "1AbcFolderId", tmp_path / "gdrive", dry_run=True, binary="gdrive"
        )


def test_download_failure_is_reported_with_exit_code(tmp_path, monkeypatch):
    calls = _stub_download(monkeypatch, returncode=2, stderr="authorization rejected")
    plan = gdrive.build_sync_plan("rclone", "gdrive:Notes", tmp_path / "gdrive", binary="rclone")
    with pytest.raises(gdrive.SyncError, match="退出码 2") as exc:
        gdrive.run_sync(plan, logger=lambda _: None)
    assert "authorization rejected" in str(exc.value)
    assert calls[0]["cwd"] == ROOT


# —— 下载目录的安全边界（ADR 0002：真实语料正文不进公开仓库）——


def test_dest_inside_repo_without_gitignore_is_rejected(monkeypatch):
    monkeypatch.setattr(gdrive, "_path_is_ignored", lambda path: False)
    dest = ROOT / "data" / "gdrive"
    with pytest.raises(gdrive.SyncError, match="gitignore"):
        gdrive.assert_dest_is_private(dest)
    # 显式确认过才放行
    gdrive.assert_dest_is_private(dest, allow=True)


def test_dest_outside_repo_is_not_checked(monkeypatch):
    def _boom(path):
        raise AssertionError("仓库外目录不该查 gitignore")

    monkeypatch.setattr(gdrive, "_path_is_ignored", _boom)
    gdrive.assert_dest_is_private(Path("/tmp/somewhere-else"))


def test_unverifiable_ignore_state_only_warns(monkeypatch, tmp_path):
    monkeypatch.setattr(gdrive, "_path_is_ignored", lambda path: None)
    notes = []
    gdrive.assert_dest_is_private(ROOT / "data" / "gdrive", logger=notes.append)
    assert notes and "无法用 git 判断" in notes[0]


def test_default_sync_dir_is_gitignored():
    """默认下载目录必须真的被 gitignore，否则真实语料会跟着提交。"""
    if gdrive._path_is_ignored(GDRIVE_SYNC_DIR) is None:
        pytest.skip("本机 git 不可用，无法判定忽略状态")
    assert gdrive._path_is_ignored(GDRIVE_SYNC_DIR) is True


# —— 下载目录就是普通 file 源 ——


def test_ingest_config_is_a_plain_file_source_tagged_gdrive(tmp_path):
    config = gdrive.build_ingest_config(tmp_path / "gdrive")
    assert len(config.sources) == 1
    source = config.sources[0]
    assert isinstance(source, FileSource)
    assert source.tags == ["gdrive"]
    assert source_key_for(source) == f"file:{tmp_path / 'gdrive'}"


def test_inventory_separates_unsupported_suffixes(tmp_path):
    dest = tmp_path / "gdrive"
    (dest / "子目录").mkdir(parents=True)
    (dest / "笔记.md").write_text("橙子富含维生素 C。", encoding="utf-8")
    (dest / "记录.txt").write_text("香蕉不要放冰箱。", encoding="utf-8")
    (dest / "子目录" / "扫描.pdf").write_bytes(b"%PDF-1.4")
    (dest / "表格.xlsx").write_bytes(b"xx")
    (dest / "文档.docx").write_bytes(b"xx")

    report = gdrive.inventory(dest)
    assert report.total == 5
    assert report.ingestable == 3
    assert report.unsupported == ["文档.docx", "表格.xlsx"]
    assert "后缀不被入库链路支持" in report.summary()


def test_inventory_of_missing_directory(tmp_path):
    report = gdrive.inventory(tmp_path / "not-downloaded-yet")
    assert report.total == 0
    assert report.ingestable == 0
    assert "没有文件" in report.summary()


def test_downloaded_files_ingest_through_existing_pipeline(tmp_path):
    """假下载的产物走现有 pipeline：分块与嵌入逻辑一处没动。"""
    dest = tmp_path / "gdrive"
    dest.mkdir()
    (dest / "橙子.md").write_text("橙子富含维生素 C，适合日常补充。", encoding="utf-8")
    (dest / "香蕉.md").write_text("香蕉不适合放冰箱，会发黑。", encoding="utf-8")
    (dest / "跳过.docx").write_bytes(b"xx")

    config = gdrive.build_ingest_config(dest)
    lexical_path = tmp_path / "lexical" / "bm25.pkl"
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    try:
        stats = run_ingest(
            config,
            client=client,
            embedding=FakeEmbeddings(),
            lexical_path=lexical_path,
            logger=lambda _: None,
        )
        assert stats.added == 2
        assert stats.chunks >= 2
        assert LexicalIndex.load(lexical_path) is not None

        # 再跑一次：内容没变就整批跳过，不重复调用 embedding
        second = run_ingest(
            config,
            client=client,
            embedding=FakeEmbeddings(),
            lexical_path=lexical_path,
            logger=lambda _: None,
        )
        assert second.added == 0
        assert second.skipped == 2
    finally:
        client.close()


def test_downloaded_files_keep_file_metadata(tmp_path):
    """引用元数据沿用 file 源的形态：source_type=file，source_uri 沿用既有路径约定。"""
    from private_rag.ingestion.files import _relative, load_file_documents

    dest = tmp_path / "gdrive"
    dest.mkdir()
    (dest / "橙子.md").write_text("橙子富含维生素 C。", encoding="utf-8")

    config = gdrive.build_ingest_config(dest)
    source = config.sources[0]
    documents, errors = load_file_documents(source, source_key_for(source))
    assert errors == []
    assert len(documents) == 1
    document = documents[0]
    assert document.source_type == "file"
    assert document.tags == ["gdrive"]
    assert document.source_uri.endswith("橙子.md")
    # 仓库内的下载目录，source_uri 与 data/docs 一样写成相对仓库根的路径
    assert _relative(ROOT / "data" / "gdrive" / "橙子.md") == "data/gdrive/橙子.md"


# —— CLI 接线 ——


def test_main_downloads_then_ingests_without_real_cli(tmp_path, monkeypatch):
    module = _load_script()
    dest = tmp_path / "gdrive"

    def fake_download(argv, cwd):
        # 模拟 rclone 把文件落到目标目录（rclone 自己按 remote:path 定位，与工作目录无关）
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "橙子.md").write_text("橙子富含维生素 C。", encoding="utf-8")
        return gdrive.subprocess.CompletedProcess(argv, 0, "Transferred: 1", "")

    monkeypatch.setattr(gdrive, "_run_download", fake_download)
    ingested = []

    def fake_ingest(target, args):
        ingested.append((target, args.prune, args.tags))
        return 0

    monkeypatch.setattr(module, "ingest", fake_ingest)

    assert module.main(
        ["--remote", "gdrive:Notes", "--dest", str(dest), "--binary", "rclone", "--ingest"]
    ) == 0
    assert ingested == [(dest, False, "gdrive")]
    assert (dest / "橙子.md").exists()


def test_main_without_download_target_exits_before_touching_files(tmp_path, monkeypatch):
    module = _load_script()
    monkeypatch.setattr(module, "GDRIVE_REMOTE", "")
    calls = _stub_download(monkeypatch)

    assert module.main(["--dest", str(tmp_path / "gdrive")]) == 1
    assert calls == []


def test_main_refuses_to_ingest_after_dry_run(tmp_path):
    module = _load_script()
    with pytest.raises(SystemExit):
        module.parse_args(["--remote", "gdrive:Notes", "--dry-run", "--ingest"])


def test_main_skip_sync_uses_files_already_downloaded(tmp_path, monkeypatch):
    module = _load_script()
    dest = tmp_path / "gdrive"
    dest.mkdir()
    (dest / "已有.md").write_text("已经下载过了。", encoding="utf-8")
    calls = _stub_download(monkeypatch)
    ingested = []
    monkeypatch.setattr(module, "ingest", lambda target, args: ingested.append(target) or 0)

    assert module.main(["--skip-sync", "--dest", str(dest), "--ingest"]) == 0
    assert calls == []
    assert ingested == [dest]


def test_main_passes_extra_args_through_to_the_download_tool(tmp_path, monkeypatch):
    module = _load_script()
    calls = _stub_download(monkeypatch)
    monkeypatch.setattr(module, "ingest", lambda target, args: 0)

    assert module.main(
        [
            "--remote", "gdrive:Notes", "--dest", str(tmp_path / "gdrive"),
            "--binary", "rclone", "--", "--drive-skip-gdocs",
        ]
    ) == 0
    assert calls[0]["argv"][-1] == "--drive-skip-gdocs"


def test_list_remotes_shows_configured_remotes(tmp_path, monkeypatch, capsys):
    module = _load_script()
    calls = _stub_download(monkeypatch, stdout="gdrive:\nbackup:\n")

    assert module.main(["--list-remotes", "--binary", "rclone"]) == 0
    assert calls[0]["argv"][:2] == ["rclone", "listremotes"]
    assert "gdrive:" in capsys.readouterr().out


def test_list_remotes_without_any_remote_is_an_error(monkeypatch, capsys):
    module = _load_script()
    _stub_download(monkeypatch, stdout="")

    assert module.main(["--list-remotes", "--binary", "rclone"]) == 1
    assert "rclone config" in capsys.readouterr().out


def test_main_reports_unsupported_files_and_refuses_empty_ingest(tmp_path, monkeypatch):
    module = _load_script()
    dest = tmp_path / "gdrive"
    dest.mkdir()
    (dest / "文档.docx").write_bytes(b"xx")
    monkeypatch.setattr(module, "ingest", lambda target, args: pytest.fail("不该入库"))

    assert module.main(["--skip-sync", "--dest", str(dest), "--ingest"]) == 1
