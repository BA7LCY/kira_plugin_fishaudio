# -*- coding: utf-8 -*-
"""Kira 框架 core 包的测试桩。

只实现本插件用到的最小接口：
- core.plugin：BasePlugin（假构造）、logger、register（收集器）、PluginPage、PageMenu
- core.chat.message_elements：Text、Record
- core.tag：BaseTag（<fish> 动态标签的基类）
- fastapi：HTTPException（本插件只在 _http_error 里延迟导入）
"""

from __future__ import annotations

import logging
import sys
import types


def _make_module(name: str, path: bool = False) -> types.ModuleType:
    m = types.ModuleType(name)
    if path:
        m.__path__ = []
    sys.modules[name] = m
    return m


def install() -> None:
    """把框架 core 桩装进 sys.modules（幂等）。"""
    if "core" in sys.modules:
        return

    core = _make_module("core", path=True)

    # ---- core.plugin ----
    plugin = _make_module("core.plugin")
    plugin.logger = logging.getLogger("stub.core.plugin")

    class BasePlugin:
        """假基类：只保存 ctx 与 cfg。"""

        def __init__(self, ctx, cfg: dict):
            self.ctx = ctx
            self.plugin_cfg = cfg or {}

    class PluginPage:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        @classmethod
        def from_folder(cls, path):
            return cls(path)

    class PageMenu:
        def __init__(self, label=None, icon=None, order=100):
            self.label = label
            self.icon = icon
            self.order = order

    class _Register:
        """装饰器收集器：记录注册过的标签/API/页面/事件钩子。"""

        def __init__(self):
            self.tags = []
            self.apis = []
            self.pages = []
            self.hooks = []

        def tag(self, name, description, parent="msg"):
            def deco(func):
                self.tags.append(
                    {"name": name, "description": description, "parent": parent, "func": func}
                )
                return func

            return deco

        def api(self, method, path, auth=True, **kwargs):
            def deco(func):
                self.apis.append(
                    {"method": method, "path": path, "auth": auth, "kwargs": kwargs, "func": func}
                )
                return func

            return deco

        def page(self, route, auth=True, menu=None):
            def deco(func):
                self.pages.append({"route": route, "auth": auth, "menu": menu, "func": func})
                return func

            return deco

    # on.llm_request(priority=...) 钩子装饰器（收集进 hooks，便于测试直接调用）
    class _On:
        def llm_request(self, priority=0):
            def deco(func):
                plugin.register.hooks.append(
                    {"event": "llm_request", "priority": priority, "func": func}
                )
                return func

            return deco

    class _Priority:
        SYS_LOW = -100
        LOW = -50
        MEDIUM = 0
        HIGH = 50
        SYS_HIGH = 100

    plugin.BasePlugin = BasePlugin
    plugin.PluginPage = PluginPage
    plugin.PageMenu = PageMenu
    plugin.register = _Register()
    plugin.on = _On()
    plugin.Priority = _Priority()
    core.plugin = plugin

    # ---- core.chat.message_elements ----
    chat = _make_module("core.chat", path=True)
    me = _make_module("core.chat.message_elements")

    class Text:
        def __init__(self, text):
            self.text = text

        def __repr__(self):
            return f"Text({self.text!r})"

    class Record:
        def __init__(self, record, mime=None, name=None, transcript=None):
            self.record = record
            self.mime = mime
            self.name = name
            self.transcript = transcript

        def __repr__(self):
            return f"Record({self.record!r})"

    me.Text = Text
    me.Record = Record
    chat.message_elements = me
    core.chat = chat

    # ---- core.tag ----
    tag = _make_module("core.tag", path=True)

    class BaseTag:
        """标签基类桩：与框架一致，保存 ctx，name/description/parent 是类属性。"""

        def __init__(self, ctx=None, **kwargs):
            self.ctx = ctx
            self.kwargs = kwargs

        name = None
        description = None
        parent = "msg"

        async def handle(self, value, **kwargs):
            raise NotImplementedError

    tag.BaseTag = BaseTag
    tag.RootTagAction = type("RootTagAction", (), {})
    core.tag = tag


def install_fastapi() -> None:
    """安装 fastapi 桩（HTTPException），让 _http_error 可用。"""
    if "fastapi" in sys.modules:
        return

    fastapi = _make_module("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail=None):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    fastapi.HTTPException = HTTPException


class FakeCtx:
    """插件上下文桩：只提供 get_plugin_data_dir()。"""

    def __init__(self, data_dir):
        self._data_dir = str(data_dir)

    def get_plugin_data_dir(self):
        return self._data_dir
