# -*- coding: utf-8 -*-
"""core/emotion.py 纯逻辑测试（零框架依赖）。"""

from __future__ import annotations

from pathlib import Path

from . import loader

MOD = loader.import_core(Path(__file__).resolve().parents[1], "emotion")


def test_known_tag_count():
    # 官方文档基础+进阶+语气+音效+特效+修饰符，至少 80 个
    assert len(MOD.KNOWN_TAGS) >= 80, len(MOD.KNOWN_TAGS)


def test_make_rules_normalizes_extra():
    rules = MOD.make_rules("whitelist", ["  [Happy] ", "screaming", "x"], max_tags=3)
    assert "happy" in rules.extra and "screaming" in rules.extra and "x" in rules.extra


def test_is_tag():
    rules = MOD.make_rules("any")
    assert MOD.is_tag("happy", rules)
    assert not MOD.is_tag("at:", rules)
    assert not MOD.is_tag("http://x", rules)
    # whitelist 模式：未知标签不算
    wl = MOD.make_rules("whitelist", frozenset(), max_tags=6)
    assert MOD.is_tag("happy", wl)
    assert not MOD.is_tag("foobarbaz", wl)


def test_normalize_keeps_tags_and_clamps():
    rules = MOD.make_rules("any", max_tags=6)
    text = "［happy］你好"
    out = MOD.normalize(text, rules)
    assert out == "[happy]你好"  # 全角转半角、标签保留


def test_normalize_drops_excess_tags():
    # 超过 max_tags 的标签被丢掉
    rules = MOD.make_rules("any", max_tags=1)
    out = MOD.normalize("[happy][sad][angry]你好", rules)
    assert out.count("[") == 1


def test_strip_removes_tags():
    rules = MOD.make_rules("any")
    assert MOD.strip("[happy]你好", rules) == "你好"
    assert MOD.strip("今天天气真好[whispering]", rules) == "今天天气真好"


def test_strip_outside_code_preserves_code():
    rules = MOD.make_rules("any")
    text = "[happy]你好\n\u0060\u0060\u0060python\n[happy]\n\u0060\u0060\u0060"
    out = MOD.strip_outside_code(text, rules)
    assert "你好" in out
    assert "\u0060\u0060\u0060" in out  # 代码块原样保留


def test_count():
    rules = MOD.make_rules("any")
    assert MOD.count("[happy][sad]hi", rules) == 2


def run():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("test_core_emotion: 全部通过")


if __name__ == "__main__":
    run()
