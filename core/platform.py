# -*- coding: utf-8 -*-
"""判断当前聊天平台能不能真的把语音发出去（纯标准库，无框架依赖）。

背景：Kira 框架的适配器会用 supported_elements 声明自己支持哪些消息元素，
但有的适配器"嘴上说支持 record，实际发不出去"——例如 QQ 官方机器人适配器，
它把 record 元素转成一句字面量 "[Unsupported message element]" 发给用户。
所以不能只看 supported_elements，还要看适配器实现和平台名。

因此这里做两件事：
1. 支持 record 元素才可能是语音平台；
2. 用一份"已知发不出语音"的标记表（按平台名 / 适配器名 / 模块路径做子串匹配），
   命中就把语音降级成文字；用户还可以在配置里追加自己的标记。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Tuple

# 已知「声明支持语音、实际发不出去」的平台标记（小写子串匹配）。
# qq_official 适配器的 send 实现只认 Text / At / Emoji / Reply / File / Image。
KNOWN_TEXT_ONLY_MARKERS: Tuple[str, ...] = (
    "qq_official",
    "qq official",
    "qqofficial",
)


@dataclass(frozen=True)
class PlatformInfo:
    """一次请求里能拿到的平台信息。"""

    platform: str = ""
    adapter_name: str = ""
    adapter_id: str = ""
    adapter_module: str = ""
    supported_elements: Tuple[str, ...] = ()

    def markers(self) -> Tuple[str, ...]:
        """用来做子串匹配的候选文本（小写、去空）。"""
        parts = (self.platform, self.adapter_name, self.adapter_id, self.adapter_module)
        return tuple(part.strip().lower() for part in parts if part and part.strip())


def split_platforms(raw: str) -> Tuple[str, ...]:
    """把配置里的"平台名单"拆开：英文逗号 / 中文逗号 / 换行都可以。"""
    text = (raw or "").replace("\n", ",").replace("，", ",")
    return tuple(item.strip() for item in text.split(",") if item.strip())


def supports_voice(info: PlatformInfo, extra_blocked: Iterable[str] = ()) -> bool:
    """当前平台能不能发语音。

    先看 supported_elements 里有没有 record；
    再拿内置标记表和用户追加的标记表，去平台名 / 适配器名 / 模块路径里做子串匹配。
    """
    elements = {str(item).strip().lower() for item in info.supported_elements}
    if "record" not in elements:
        return False

    blocked = list(KNOWN_TEXT_ONLY_MARKERS)
    blocked.extend(str(item).strip().lower() for item in extra_blocked if str(item).strip())

    markers = info.markers()
    for marker in blocked:
        if any(marker in value for value in markers):
            return False
    return True
