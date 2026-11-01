# -*- coding: utf-8 -*-
"""core/settings.py 纯逻辑测试（零框架依赖）。"""

from __future__ import annotations

from pathlib import Path

from . import loader

MOD = loader.import_core(Path(__file__).resolve().parents[1], "settings")


def test_defaults():
    s = MOD.Settings.from_dict({})
    assert s.enabled is True
    assert s.max_chars == 80
    assert s.timeout == 30
    assert s.format == "mp3"
    assert s.api_base == "https://api.fish.audio"
    assert s.model == "s2.1-pro"
    assert s.speed == 1.0
    assert s.retries == 1


def test_clamps():
    # 越界值被钳制到合法范围
    cfg = {
        "basic": {"enabled": True, "max_chars": 99999, "timeout": -5, "format": "wav"},
        "advanced": {"speed": 99.0, "volume": 100, "temperature": 5, "retries": 99},
    }
    s = MOD.Settings.from_dict(cfg)
    assert s.max_chars == 500
    assert s.timeout == 1
    assert s.speed == 2.0
    assert s.volume == 24
    assert s.temperature == 1.0
    assert s.retries == 3


def test_extra_tags_splitting():
    cfg = {"emotion": {"extra_tags": " 开心 , 生气\n紧张，"}}
    s = MOD.Settings.from_dict(cfg)
    assert "开心" in s.extra_tags
    assert "生气" in s.extra_tags
    assert "紧张" in s.extra_tags


def test_make_fish_config():
    s = MOD.Settings.from_dict(
        {"fish": {"reference_id": "ca3007f96ae7499ab87d27ea3599956a"}, "basic": {"format": "opus"}}
    )
    fc = s.make_fish_config()
    assert fc.reference_id == "ca3007f96ae7499ab87d27ea3599956a"
    assert fc.format == "opus"
    assert fc.model == "s2.1-pro"


def run():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("test_core_settings: 全部通过")


if __name__ == "__main__":
    run()
