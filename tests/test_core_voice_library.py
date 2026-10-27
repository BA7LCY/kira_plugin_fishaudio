# -*- coding: utf-8 -*-
"""core/voice_library.py 纯逻辑测试（零框架依赖）。"""

from __future__ import annotations

from pathlib import Path

from . import loader, tmpdir

MOD = loader.import_core(Path(__file__).resolve().parents[1], "voice_library")


def test_parse_voice_id():
    assert MOD.parse_voice_id("ca3007f96ae7499ab87d27ea3599956a") == "ca3007f96ae7499ab87d27ea3599956a"
    # 整页地址
    assert (
        MOD.parse_voice_id("https://fish.audio/m/ca3007f96ae7499ab87d27ea3599956a?x=1")
        == "ca3007f96ae7499ab87d27ea3599956a"
    )
    try:
        MOD.parse_voice_id("太短")
        assert False, "应当抛错"
    except MOD.VoiceLibraryError:
        pass


def test_clean_name():
    assert MOD.clean_name(" 小明 ").strip()  # 允许带空格的名字
    try:
        MOD.clean_name("12345")
        assert False, "纯数字应抛错"
    except MOD.VoiceLibraryError:
        pass


def test_add_and_resolve():
    lib = MOD.VoiceLibrary(None)
    lib.add("小明", "ca3007f96ae7499ab87d27ea3599956a")
    lib.add("小红", "9a9cf47702da476aa4629e2506d4a857")
    assert len(lib) == 2
    # 名称 / casefold / 序号 三种解析（_resolve 返回 Voice 对象）
    assert lib._resolve("小明").id == "ca3007f96ae7499ab87d27ea3599956a"
    assert lib._resolve("小红").id == "9a9cf47702da476aa4629e2506d4a857"
    assert lib._resolve("1").id == "ca3007f96ae7499ab87d27ea3599956a"
    # casefold 覆盖同名
    lib.add("小明", "bf322df2096a46f18c579d0baa36f41d")
    assert len(lib) == 2
    assert lib.get("小明").id == "bf322df2096a46f18c579d0baa36f41d"


def test_active_and_remove():
    lib = MOD.VoiceLibrary(None)
    lib.add("A", "ca3007f96ae7499ab87d27ea3599956a")
    lib.add("B", "9a9cf47702da476aa4629e2506d4a857")
    lib.use("B")
    assert lib.active_id == "9a9cf47702da476aa4629e2506d4a857"
    lib.remove("B")
    assert lib.active_name == ""  # 删除默认音色后清空


def test_save_load_roundtrip():
    d = tmpdir.make_tmpdir("vl")
    try:
        path = d / "voice_library.json"
        lib = MOD.VoiceLibrary(path)
        lib.add("默认音色", "ca3007f96ae7499ab87d27ea3599956a", note="测试")
        lib.use("默认音色")
        lib.save()

        lib2 = MOD.VoiceLibrary(path)
        n = lib2.load()
        assert n == 1
        assert lib2.active_id == "ca3007f96ae7499ab87d27ea3599956a"
        assert lib2.get("默认音色").note == "测试"
    finally:
        tmpdir.rm_tmpdir(d)


def test_load_corrupt_backs_up():
    d = tmpdir.make_tmpdir("vlc")
    try:
        path = d / "voice_library.json"
        path.write_text("{不是合法json", encoding="utf-8")
        lib = MOD.VoiceLibrary(path)
        assert lib.load() == 0  # 损坏文件被挪走，从空白开始
        assert (d / "voice_library.json.bad").exists()
    finally:
        tmpdir.rm_tmpdir(d)


def run():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("test_core_voice_library: 全部通过")


if __name__ == "__main__":
    run()
