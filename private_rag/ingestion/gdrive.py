"""Google Drive 语料接入：先用本机 CLI 把 Drive 文件落到本地目录，再交给现有 file 源入库。

边界（见 docs/adr/0003）：
- 只有这一步联网。入库与检索链路不碰 Google API，仓库也不引入 Google SDK 依赖。
- 凭据只留在本机（rclone 的 remote 配置在 ~/.config/rclone/rclone.conf），不进命令行、不进日志。
- 下载目录默认 data/gdrive，已 gitignore：真实语料正文不进公开仓库（docs/adr/0002）。
- 入库复用 private_rag/ingestion/pipeline.py：不重写分块与嵌入，source_type 仍是 file，
  靠 tags=gdrive 区分来源。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from private_rag.config import GDRIVE_SYNC_DIR, ROOT
from private_rag.ingestion.files import SUPPORTED_SUFFIXES
from private_rag.sources import FileSource, SourcesConfig, resolve_path

SYNC_TOOLS = ("rclone", "gdrive")

# 入库链路只认 txt/md/pdf（SUPPORTED_SUFFIXES），所以 Google 原生文档默认导出成 PDF；
# rclone 不指定时按自己的默认格式导出（docx/xlsx/pptx 那类），那些后缀本仓库读不了。
DEFAULT_EXPORT_FORMATS = "pdf"
DEFAULT_TAGS = ("gdrive",)

# 每个工具在本机的安装方式；缺 CLI 时把这句话直接打给用户，不让他们去猜
INSTALL_HINTS = {
    "rclone": "macOS: brew install rclone；其他系统见 https://rclone.org/install/",
    "gdrive": "macOS: brew install gdrive，或取发行版 https://github.com/glutensystem/gdrive",
}


class SyncError(RuntimeError):
    """同步的前置条件不满足（CLI 没装、remote 形态不对、下载目录会进仓库）。"""


@dataclass
class SyncPlan:
    """一次下载要执行的命令与执行位置，构造过程不碰网络，方便单测断言。"""

    tool: str
    argv: List[str]
    cwd: Path
    dest: Path


@dataclass
class Inventory:
    """下载目录的清点结果：多少文件能入库，哪些后缀不被支持。"""

    total: int = 0
    ingestable: int = 0
    unsupported: List[str] = field(default_factory=list)

    def summary(self, show: int = 5) -> str:
        if self.total == 0:
            return "目录里没有文件"
        text = f"{self.total} 个文件，其中 {self.ingestable} 个可入库（txt/md/pdf）"
        if self.unsupported:
            listed = ", ".join(self.unsupported[:show])
            more = f" 等 {len(self.unsupported)} 个" if len(self.unsupported) > show else ""
            text += f"；{len(self.unsupported)} 个后缀不被入库链路支持：{listed}{more}"
        return text


def default_dest() -> Path:
    return GDRIVE_SYNC_DIR


def resolve_dest(value: Optional[str] = None) -> Path:
    """相对路径按项目根目录解析，与 sources.yaml 的其它路径同一套规则。"""
    return resolve_path(value) if value else default_dest()


def require_binary(tool: str, binary: Optional[str] = None) -> str:
    """确认下载 CLI 在本机可用；不可用时给出安装位置，不静默失败。"""
    if tool not in SYNC_TOOLS:
        raise SyncError(f"不支持的下载工具：{tool}（可选 {'/'.join(SYNC_TOOLS)}）")
    if binary:
        return binary  # 显式指定的路径交给调用方负责（自定义安装位置、测试替身）
    found = shutil.which(tool)
    if found is None:
        raise SyncError(f"本机没有 {tool}。{INSTALL_HINTS[tool]}")
    return found


def build_sync_plan(
    tool: str,
    target: str,
    dest: Path,
    *,
    mode: str = "copy",
    export_formats: str = DEFAULT_EXPORT_FORMATS,
    include: Iterable[str] = (),
    extra_args: Iterable[str] = (),
    dry_run: bool = False,
    single_file: bool = False,
    binary: Optional[str] = None,
) -> SyncPlan:
    """拼出下载命令。凭据不出现在参数里：rclone 用本机 remote，gdrive 用本机凭据文件。"""
    binary_path = require_binary(tool, binary)
    if not target.strip():
        raise SyncError("下载目标为空：rclone 用 gdrive:某个文件夹，gdrive 用文件夹或文件 ID")

    if tool == "rclone":
        if ":" not in target:
            raise SyncError(
                f"rclone 目标要写成 remote:path（例如 gdrive:Notes），现在是 {target}"
            )
        argv = [binary_path, "sync" if mode == "sync" else "copy", target, str(dest)]
        if export_formats:
            argv += ["--drive-export-formats", export_formats]
        for pattern in include:
            argv += ["--include", pattern]
        if dry_run:
            argv.append("--dry-run")
        argv += list(extra_args)
        # rclone 自己按 remote:path 定位，工作目录用项目根即可
        return SyncPlan(tool=tool, argv=argv, cwd=ROOT, dest=dest)

    # gdrive 各发行版的输出目录参数不一致（有的只有 --output 单文件），
    # 直接把工作目录切成目标目录，文件就落在那里。
    argv = [binary_path, "download", target]
    if not single_file:
        argv.append("--recursive")
    if dry_run:
        raise SyncError("gdrive CLI 没有 dry-run；要预演请改用 --tool rclone --dry-run")
    argv += list(extra_args)
    return SyncPlan(tool=tool, argv=argv, cwd=dest, dest=dest)


def _run_download(argv: List[str], cwd: Path) -> subprocess.CompletedProcess:
    """执行下载命令。单测打桩这一层即可，不需要真实 CLI。"""
    return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True)


def run_sync(plan: SyncPlan, *, logger: Callable[[str], None] = print) -> None:
    plan.dest.mkdir(parents=True, exist_ok=True)
    logger(f"🔽 下载：{' '.join(plan.argv)}")
    result = _run_download(plan.argv, plan.cwd)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:400]
        raise SyncError(f"{plan.tool} 同步失败（退出码 {result.returncode}）：{detail}")
    tail = [line for line in (result.stdout or "").splitlines() if line.strip()][-3:]
    for line in tail:
        logger(f"   {line}")


def _path_is_ignored(path: Path) -> Optional[bool]:
    """用 git 判断目录是否被忽略；True 忽略，False 未忽略，None 表示判不了。

    探测的是目录里的一个假文件名：git 对 `data/gdrive/` 这类目录型规则，只有在能判定
    「这是个目录」时才匹配，而目录在首次下载前还不存在。用一个文件名探进去，规则照样命中。
    """
    probe = path / "private-rag-sync-probe.tmp"
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(probe)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
    except (OSError, ValueError):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def assert_dest_is_private(
    dest: Path, *, allow: bool = False, logger: Callable[[str], None] = print
) -> None:
    """仓库内的下载目录必须被 gitignore，否则真实语料正文会跟着提交（ADR 0002）。"""
    if allow:
        return
    try:
        relative = dest.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return  # 仓库之外的目录，本来就不会被提交
    ignored = _path_is_ignored(dest)
    if ignored:
        return
    if ignored is None:
        logger(f"⚠️ 无法用 git 判断 {relative} 是否被忽略，请先确认它不会进仓库")
        return
    raise SyncError(
        f"下载目录 {relative} 在仓库里但没有被 gitignore，真实语料正文会被提交进公开仓库。"
        "把它加进 .gitignore，或改用仓库外目录（--dest ~/...），"
        "确认无误再显式加 --allow-tracked-dest"
    )


def inventory(dest: Path) -> Inventory:
    """清点下载目录：入库链路只认 SUPPORTED_SUFFIXES，其余后缀单独列出来。"""
    if not dest.exists():
        return Inventory()
    files = [path for path in dest.rglob("*") if path.is_file()]
    unsupported = sorted(
        str(path.relative_to(dest)) for path in files if path.suffix.lower() not in SUPPORTED_SUFFIXES
    )
    return Inventory(
        total=len(files),
        ingestable=len(files) - len(unsupported),
        unsupported=unsupported,
    )


def build_ingest_config(
    dest: Path,
    *,
    tags: Iterable[str] = DEFAULT_TAGS,
    include: Iterable[str] = (),
) -> SourcesConfig:
    """下载目录就是一个普通 file 源：不新增 source 类型，不动分块与嵌入。"""
    return SourcesConfig(
        sources=[FileSource(path=str(dest), tags=list(tags), include=list(include))]
    )
