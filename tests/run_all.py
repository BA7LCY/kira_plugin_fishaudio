# -*- coding: utf-8 -*-
"""一键运行全部测试。

用法：
    python tests/run_all.py
（装了 pytest 的话也可以直接 pytest tests/ 运行，测试写法兼容 pytest。）
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

MODULES = [
    "test_core_emotion",
    "test_core_voice_library",
    "test_core_settings",
    "test_core_tts_client",
    "test_plugin",
]

if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here.parent))  # 让 tests 可作为包导入
    for mod in MODULES:
        m = importlib.import_module(f"tests.{mod}")
        m.run()
    print("\n全部测试通过 ✓")
