# -*- coding: utf-8 -*-
"""把插件根目录按 Kira 的加载方式载入测试。

Kira 框架把插件 main.py 作为 plugins.<插件目录名>.main 加载，且给
plugins.<插件目录名> 包设置 __path__=[插件根]，因此 main.py 里
"from .core.xxx import ..."（相对导入）指向插件自带的 core/ 子包，
"from core.plugin import ..."（绝对导入）指向框架的 core 包。

测试里我们用同样方式构造包结构，这样既测了真实加载路径，又不会和
stub 出来的框架 core 冲突。
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


def ensure_plugin_package(plugin_root: Path) -> str:
    """创建 plugins 与 plugins.<目录名> 包结构，返回包名。"""
    plugin_root = Path(plugin_root).resolve()
    pkg_name = f"plugins.{plugin_root.name}"

    if "plugins" not in sys.modules:
        top = types.ModuleType("plugins")
        top.__path__ = []
        sys.modules["plugins"] = top

    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(plugin_root)]
        sys.modules[pkg_name] = pkg

    return pkg_name


def import_core(plugin_root: Path, modname: str):
    """导入插件自带的 core 子模块（不加载 main.py，无需框架桩）。"""
    pkg = ensure_plugin_package(plugin_root)
    return importlib.import_module(f"{pkg}.core.{modname}")


def import_main(plugin_root: Path):
    """导入插件 main.py（需要先安装框架 core 桩，见 stub_core）。"""
    pkg = ensure_plugin_package(plugin_root)
    return importlib.import_module(f"{pkg}.main")
