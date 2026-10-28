# -*- coding: utf-8 -*-
"""平台语音能力判断（core/platform.py）的单元测试：纯逻辑，不依赖框架。"""

from __future__ import annotations

from pathlib import Path

from . import loader

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
platform = loader.import_core(PLUGIN_ROOT, "platform")


def _info(**kwargs):
    return platform.PlatformInfo(**kwargs)


def test_record_element_required():
    # 没有 record 元素 → 一律不能发语音
    assert platform.supports_voice(_info(supported_elements=("text", "img"))) is False
    # 有 record + 普通平台 → 能发
    assert platform.supports_voice(_info(supported_elements=("text", "record"), platform="Telegram")) is True


def test_qq_official_is_text_only():
    # QQ 官方机器人自称支持 record，实际发不出去
    cases = [
        _info(supported_elements=("record",), platform="QQ Official"),
        _info(supported_elements=("record",), adapter_module="core.adapter.src.qq_official.im"),
        _info(supported_elements=("record",), adapter_id="qq_official"),
        _info(supported_elements=("record",), adapter_name="QQOfficialBot"),
    ]
    for info in cases:
        assert platform.supports_voice(info) is False, info


def test_extra_blocked_platforms():
    info = _info(supported_elements=("record",), platform="Discord")
    assert platform.supports_voice(info) is True
    assert platform.supports_voice(info, ("discord",)) is False
    # 标记按子串、忽略大小写匹配
    assert platform.supports_voice(info, ("DIS",)) is False


def test_split_platforms():
    assert platform.split_platforms("") == ()
    assert platform.split_platforms("a, b") == ("a", "b")
    assert platform.split_platforms("a，b\nc") == ("a", "b", "c")
    # 中文逗号、换行都能当分隔符，空项被丢掉
    assert platform.split_platforms(" , ") == ()


def test_supported_elements_none():
    # 框架没给 supported_elements（None）时当作不支持，走文字降级
    info = platform.PlatformInfo()
    assert platform.supports_voice(info) is False


def run():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("test_core_platform: 全部通过")


if __name__ == "__main__":
    run()
