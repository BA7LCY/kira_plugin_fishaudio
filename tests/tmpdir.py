# -*- coding: utf-8 -*-
"""测试临时目录工具。

测试产生的临时文件统一放在本插件的 _tmp_tt/ 下（已在 .gitignore 忽略，不会进仓库）。
沙箱禁止写系统临时目录（tempfile 默认目录），所以不用 tempfile.mkdtemp。
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

# 插件根：本文件位于 <插件根>/tests/tmpdir.py
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
# 临时文件根（插件内，已 gitignore）
TMP_ROOT = PLUGIN_ROOT / "_tmp_tt"


def make_tmpdir(tag: str) -> Path:
    """在插件临时目录下建一个带前缀的独立目录。

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
