# -*- coding: utf-8 -*-
"""测试临时目录工具。

测试临时文件统一放在本插件的 _tmp_tt/ 下（已 gitignore）。
沙箱禁止写系统临时目录（tempfile 默认目录），所以测试临时目录统一放到
插件目录 _tmp_tt/ 下。
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

# 工作区根：本文件位于 <工作区>/kira_plugin_fishaudio/tests/tmpdir.py
WORKSPACE = Path(__file__).resolve().parents[2]
# 临时文件根（对测试运行而言是"外部"目录，符合用户约定）
TMP_ROOT = WORKSPACE / "_tmp_tt"


def make_tmpdir(tag: str) -> Path:
    """在工作区临时目录下建一个带前缀的独立目录。

    不用 tempfile.mkdtemp：沙箱对 mkdtemp 创建的目录不允许写入，
    用 Path.mkdir 创建则正常。
    """
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    d = TMP_ROOT / f"{tag}_{os.getpid()}_{time.time_ns()}"
    d.mkdir()
    return d


def rm_tmpdir(path: Path) -> None:
    """删除测试临时目录（失败忽略）。"""
    shutil.rmtree(str(path), ignore_errors=True)
