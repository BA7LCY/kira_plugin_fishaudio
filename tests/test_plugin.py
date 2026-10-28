# -*- coding: utf-8 -*-
"""main.py 集成测试（框架 core 桩 + 假 TTS）。

覆盖：
  - <fish> 标签：开关 / 上限降级 / 情绪归一化 / 音色解析 / 失败降级
  - 音色库 API：列表 / 保存 / 设默认 / 删除 / 清空默认
  - 试听 API：正常返回 base64 / 未配 Key / 超上限
"""

from __future__ import annotations

from pathlib import Path

from . import loader, stub_core, tmpdir

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# 先装框架桩再导入 main
stub_core.install()
stub_core.install_fastapi()
MAIN = loader.import_main(PLUGIN_ROOT)

FishAudioPlugin = MAIN.FishAudioPlugin
FishError = MAIN.FishError
HTTPException = stub_core.install_fastapi().__class__ if False else None


class FakeTTS:
    """记录调用、返回假音频文件的 TTS。"""

    def __init__(self, out_dir: Path, fail: bool = False):
        self.out_dir = Path(out_dir)
        self.calls: list = []
        self.fail = fail

    async def synthesize(self, text: str, cfg):
        self.calls.append((text, cfg))
        if self.fail:
            raise FishError("模拟合成失败", status=500, retryable=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        p = self.out_dir / f"fake_{len(self.calls)}.mp3"
        p.write_bytes(b"FAKEAUDIO")
        return p


def _get_fastapi():
    import sys
    return sys.modules["fastapi"]


def _make_plugin(data_dir: Path, cfg: dict, *, with_tts: bool = True, tts_fail: bool = False):
    """构造并初始化插件实例，默认注入假 TTS。"""
    ctx = stub_core.FakeCtx(data_dir)
    plugin = FishAudioPlugin(ctx, cfg)
    return plugin


async def _init(plugin, out_dir=None, tts_fail=False):
    await plugin.initialize()
    if out_dir is not None:
        plugin.tts = FakeTTS(out_dir, fail=tts_fail)
    return plugin


def _base_cfg(**overrides):
    cfg = {
        "basic": {"enabled": True, "max_chars": 80, "timeout": 30, "format": "mp3"},
        "fish": {"api_key": "test-key", "api_base": "https://api.fish.audio", "model": "s2.1-pro", "reference_id": ""},
        "emotion": {"enabled": True, "tag_mode": "any", "extra_tags": "", "max_tags": 6, "strip_in_text": True},
        "advanced": {"speed": 1.0, "volume": 0.0, "temperature": 0.0, "top_p": 0.0, "latency": "normal", "normalize": True, "retries": 1, "extra_body": {}},
    }
    # 逐层覆盖
    for k, v in overrides.items():
        if isinstance(v, dict) and k in cfg:
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


async def test_fish_tag_returns_record():
    d = tmpdir.make_tmpdir("pl")
    try:
        plugin = _make_plugin(d, _base_cfg())
        await _init(plugin, out_dir=d / "cache")
        res = await plugin.fish("你好呀")
        assert len(res) == 1 and res[0].__class__.__name__ == "Record"
        text, cfg = plugin.tts.calls[0]
        assert text == "你好呀"
        assert cfg.api_key == "test-key"
    finally:
        tmpdir.rm_tmpdir(d)


async def test_fish_tag_disabled_returns_text():
    d = tmpdir.make_tmpdir("pl")
    try:
        plugin = _make_plugin(d, _base_cfg(**{"basic": {"enabled": False}}))
        await _init(plugin, out_dir=d / "cache")
        res = await plugin.fish("你好")
        assert res[0].__class__.__name__ == "Text"
        assert res[0].text == "你好"
    finally:
        tmpdir.rm_tmpdir(d)


async def test_fish_tag_over_max_chars_degrades_to_text():
    d = tmpdir.make_tmpdir("pl")
    try:
        plugin = _make_plugin(d, _base_cfg(**{"basic": {"max_chars": 5}}))
        await _init(plugin, out_dir=d / "cache")
        res = await plugin.fish("这段文字超过五个字了吧")
        assert res[0].__class__.__name__ == "Text"
        assert plugin.tts.calls == []  # 没调合成
    finally:
        tmpdir.rm_tmpdir(d)


async def test_fish_tag_emotion_normalized_for_tts():
    d = tmpdir.make_tmpdir("pl")
    try:
        plugin = _make_plugin(d, _base_cfg())
        await _init(plugin, out_dir=d / "cache")
        res = await plugin.fish("[happy]哈哈")
        assert res[0].__class__.__name__ == "Record"
        text, _ = plugin.tts.calls[0]
        # 情绪标记保留给 S2（只控制语气、不念出）
        assert text == "[happy]哈哈"
    finally:
        tmpdir.rm_tmpdir(d)


async def test_fish_tag_voice_attr_priority():
    d = tmpdir.make_tmpdir("pl")
    try:
        plugin = _make_plugin(d, _base_cfg(**{"fish": {"reference_id": "ca3007f96ae7499ab87d27ea3599956a"}}))
        await _init(plugin, out_dir=d / "cache")
        # 库里设了默认音色
        plugin.voice_lib.add("默认", "9a9cf47702da476aa4629e2506d4a857")
        plugin.voice_lib.use("默认")
        # 1) voice 属性是合法 ID → 优先用它
        await plugin.fish("hi", voice="bf322df2096a46f18c579d0baa36f41d")
        assert plugin.tts.calls[-1][1].reference_id == "bf322df2096a46f18c579d0baa36f41d"
        # 2) voice 属性是库内名称 → 解析成它的 ID
        await plugin.fish("hi", voice="默认")
        assert plugin.tts.calls[-1][1].reference_id == "9a9cf47702da476aa4629e2506d4a857"
        # 3) 没给 voice → 库默认
        await plugin.fish("hi")
        assert plugin.tts.calls[-1][1].reference_id == "9a9cf47702da476aa4629e2506d4a857"
        # 4) voice 属性无法识别 → 兜底回库默认
        await plugin.fish("hi", voice="不存在的东西")
        assert plugin.tts.calls[-1][1].reference_id == "9a9cf47702da476aa4629e2506d4a857"
        # 5) 清掉库默认 → 用配置兜底
        plugin.voice_lib.clear_active()
        await plugin.fish("hi")
        assert plugin.tts.calls[-1][1].reference_id == "ca3007f96ae7499ab87d27ea3599956a"
    finally:
        tmpdir.rm_tmpdir(d)


async def test_fish_tag_failure_degrades_to_text():
    d = tmpdir.make_tmpdir("pl")
    try:
        plugin = _make_plugin(d, _base_cfg())
        await _init(plugin, out_dir=d / "cache", tts_fail=True)
        res = await plugin.fish("你好")
        assert res[0].__class__.__name__ == "Text"
    finally:
        tmpdir.rm_tmpdir(d)


async def test_voice_api_roundtrip():
    d = tmpdir.make_tmpdir("pl")
    try:
        plugin = _make_plugin(d, _base_cfg())
        await _init(plugin, out_dir=d / "cache")

        # 列表初始为空
        payload = await plugin.api_list_voices()
        assert payload["count"] == 0 and payload["active"] == ""

        # 保存一个音色
        body = MAIN.VoiceSaveBody(name="小明", id="ca3007f96ae7499ab87d27ea3599956a", note="测试音色")
        payload = await plugin.api_save_voice(body)
        assert payload["count"] == 1
        assert payload["voices"][0]["name"] == "小明"

        # 重复保存（覆盖）
        body2 = MAIN.VoiceSaveBody(name="小明", id="9a9cf47702da476aa4629e2506d4a857")
        payload = await plugin.api_save_voice(body2)
        assert payload["count"] == 1

        # 设为默认
        payload = await plugin.api_use_voice(MAIN.VoiceKeyBody(key="小明"))
        assert payload["active"] == "小明"

        # 非法名称 → HTTPException 400
        fastapi = _get_fastapi()
        try:
            await plugin.api_save_voice(MAIN.VoiceSaveBody(name="bad name!", id="ca3007f96ae7499ab87d27ea3599956a"))
            assert False, "应当抛 HTTPException"
        except fastapi.HTTPException as e:
            assert e.status_code == 400

        # 不存在的 key → 400
        try:
            await plugin.api_use_voice(MAIN.VoiceKeyBody(key="不存在的"))
            assert False
        except fastapi.HTTPException as e:
            assert e.status_code == 400

        # 删除
        payload = await plugin.api_remove_voice(MAIN.VoiceKeyBody(key="小明"))
        assert payload["count"] == 0 and payload["active"] == ""

        # 清空默认（没有默认时也不报错）
        payload = await plugin.api_clear_voice()
        assert payload["active"] == ""
    finally:
        tmpdir.rm_tmpdir(d)


async def test_tts_test_api():
    d = tmpdir.make_tmpdir("pl")
    try:
        plugin = _make_plugin(d, _base_cfg())
        await _init(plugin, out_dir=d / "cache")

        # 正常试听
        body = MAIN.TtsTestBody(text="你好", reference_id="ca3007f96ae7499ab87d27ea3599956a")
        result = await plugin.api_tts_test(body)
        assert result["audio"]
        assert result["mime"] == "audio/mpeg"
        assert result["length"] > 0
        import base64
        assert base64.b64decode(result["audio"]) == b"FAKEAUDIO"

        # 未配 Key → 400
        plugin2 = _make_plugin(d / "other", _base_cfg(**{"fish": {"api_key": ""}}))
        await _init(plugin2, out_dir=d / "cache2")
        fastapi = _get_fastapi()
        try:
            await plugin2.api_tts_test(MAIN.TtsTestBody())
            assert False
        except fastapi.HTTPException as e:
            assert e.status_code == 400
            assert "API Key" in e.detail

        # 超上限 → 400
        try:
            await plugin.api_tts_test(MAIN.TtsTestBody(text="这" * 200))
            assert False
        except fastapi.HTTPException as e:
            assert e.status_code == 400
            assert "上限" in e.detail
    finally:
        tmpdir.rm_tmpdir(d)


class FakeTagSet:
    """记录 unregister / register 调用（register 只收实例）。"""

    def __init__(self):
        self.unregistered = []
        self.registered = []

    def unregister(self, *names):
        self.unregistered.extend(names)

    def register(self, *tags):
        self.registered.extend(tags)

    def fish_tag(self):
        for tag in self.registered:
            if getattr(tag, "name", "") == "fish":
                return tag
        return None


class FakeEvent:
    """只带平台信息的事件桩。"""

    def __init__(self, supported_elements=("record",), platform="Telegram", name="tg", adapter_id="tg"):
        self.supported_elements = list(supported_elements)
        self.adapter = type(
            "FakeAdapterInfo",
            (),
            {"platform": platform, "name": name, "adapter_id": adapter_id},
        )()


async def test_manage_builtin_tags_hook():
    """接管钩子：启用+开开关+配了 Key 才屏蔽 <record>，其余情况不屏蔽。"""
    import sys
    pmod = sys.modules["core.plugin"]
    hooks = [h for h in pmod.register.hooks if h["event"] == "llm_request"]
    hook = None
    for h in hooks:
        if h["func"].__name__ == "manage_builtin_tags":
            hook = h["func"]
    assert hook is not None, "应该收集到 manage_builtin_tags 钩子"
    # 优先级 MEDIUM(0)，低于内置 SYS_HIGH(100)，保证在内置注册之后执行
    assert h["priority"] == 0

    d = tmpdir.make_tmpdir("pl")
    try:
        # 1) 默认：启用 + 开开关 + 有 Key → 屏蔽
        plugin = _make_plugin(d, _base_cfg())
        await _init(plugin, out_dir=d / "cache")
        ts = FakeTagSet()
        await hook(plugin, None, None, ts)
        assert ts.unregistered == ["record"]

        # 2) 开关关闭 → 不屏蔽
        plugin2 = _make_plugin(d / "p2", _base_cfg(**{"basic": {"disable_builtin_record": False}}))
        await _init(plugin2, out_dir=d / "cache2")
        ts2 = FakeTagSet()
        await hook(plugin2, None, None, ts2)
        assert ts2.unregistered == []

        # 3) 没配 Key → 不屏蔽（保留框架自带作兜底）
        plugin3 = _make_plugin(d / "p3", _base_cfg(**{"fish": {"api_key": ""}}))
        await _init(plugin3, out_dir=d / "cache3")
        ts3 = FakeTagSet()
        await hook(plugin3, None, None, ts3)
        assert ts3.unregistered == []

        # 4) 插件禁用 → 不屏蔽
        plugin4 = _make_plugin(d / "p4", _base_cfg(**{"basic": {"enabled": False}}))
        await _init(plugin4, out_dir=d / "cache4")
        ts4 = FakeTagSet()
        await hook(plugin4, None, None, ts4)
        assert ts4.unregistered == []
    finally:
        tmpdir.rm_tmpdir(d)


async def test_hook_registers_fish_tag_by_platform():
    """钩子按平台能力注册 <fish>：能发语音→语音说明，发不了→文字说明。"""
    import sys
    pmod = sys.modules["core.plugin"]
    hook = next(
        h["func"] for h in pmod.register.hooks
        if h["event"] == "llm_request" and h["func"].__name__ == "manage_builtin_tags"
    )

    d = tmpdir.make_tmpdir("pl")
    try:
        # 1) 普通平台（Telegram，支持 record）→ 注册语音版标签
        plugin = _make_plugin(d, _base_cfg())
        await _init(plugin, out_dir=d / "cache")
        ts = FakeTagSet()
        await hook(plugin, FakeEvent(platform="Telegram"), None, ts)
        tag = ts.fish_tag()
        assert tag is not None, "应该注册 <fish> 标签"
        assert tag._allow_voice is True
        assert "转成语音" in tag.description
        # handle 真的会合成语音
        res = await tag.handle("你好")
        assert res[0].__class__.__name__ == "Record"

        # 2) QQ 官方机器人（自称支持 record，实际发不出去）→ 文字版标签
        plugin2 = _make_plugin(d / "p2", _base_cfg())
        await _init(plugin2, out_dir=d / "cache2")
        ts2 = FakeTagSet()
        await hook(plugin2, FakeEvent(platform="QQ Official", name="QQ官鸡"), None, ts2)
        tag2 = ts2.fish_tag()
        assert tag2 is not None
        assert tag2._allow_voice is False
        assert "发不了语音" in tag2.description
        res2 = await tag2.handle("[happy]你好")
        assert res2[0].__class__.__name__ == "Text"
        assert plugin2.tts.calls == []  # 不合成，也不浪费额度

        # 3) supported_elements 没有 record → 文字版
        plugin3 = _make_plugin(d / "p3", _base_cfg())
        await _init(plugin3, out_dir=d / "cache3")
        ts3 = FakeTagSet()
        await hook(plugin3, FakeEvent(supported_elements=("text",), platform="Webchat"), None, ts3)
        assert ts3.fish_tag()._allow_voice is False

        # 4) 用户自己在配置里追加"发不了语音"的平台 → 文字版
        plugin4 = _make_plugin(
            d / "p4", _base_cfg(**{"basic": {"text_only_platforms": "discord"}})
        )
        await _init(plugin4, out_dir=d / "cache4")
        ts4 = FakeTagSet()
        await hook(plugin4, FakeEvent(platform="Discord"), None, ts4)
        assert ts4.fish_tag()._allow_voice is False

        # 5) 插件禁用 → 仍然注册（避免 <fish> 被静默吞掉），但只发文字
        plugin5 = _make_plugin(d / "p5", _base_cfg(**{"basic": {"enabled": False}}))
        await _init(plugin5, out_dir=d / "cache5")
        ts5 = FakeTagSet()
        await hook(plugin5, FakeEvent(platform="Telegram"), None, ts5)
        assert ts5.fish_tag()._allow_voice is False
    finally:
        tmpdir.rm_tmpdir(d)


async def test_fish_allow_voice_false_returns_text():
    """直接调用 fish(allow_voice=False) 时只发文字、不合成。"""
    d = tmpdir.make_tmpdir("pl")
    try:
        plugin = _make_plugin(d, _base_cfg())
        await _init(plugin, out_dir=d / "cache")
        res = await plugin.fish("[happy]你好", allow_voice=False)
        assert res[0].__class__.__name__ == "Text"
        assert plugin.tts.calls == []
    finally:
        tmpdir.rm_tmpdir(d)


async def test_log_tag_mode_only_on_change():
    """语音标签模式日志只在变化时打一遍，避免每轮对话刷屏。"""
    import logging
    import sys

    pmod = sys.modules["core.plugin"]
    hook = next(
        h["func"] for h in pmod.register.hooks
        if h["event"] == "llm_request" and h["func"].__name__ == "manage_builtin_tags"
    )

    collected = []

    class _Capture(logging.Handler):
        def emit(self, record):
            collected.append(record.getMessage())

    target = pmod.logger
    target.setLevel(logging.DEBUG)
    handler = _Capture()
    target.addHandler(handler)

    d = tmpdir.make_tmpdir("pl")
    try:
        plugin = _make_plugin(d, _base_cfg())
        await _init(plugin, out_dir=d / "cache")
        qq = FakeEvent(platform="QQ Official", name="QQ官鸡")

        await hook(plugin, qq, None, FakeTagSet())
        mode_logs = [m for m in collected if m.startswith("语音标签模式")]
        assert len(mode_logs) == 1, mode_logs
        assert "发文字" in mode_logs[0]
        assert "QQ Official" in mode_logs[0]
        assert "已屏蔽" in mode_logs[0]

        # 同一平台再问一次 → 不重复打印
        await hook(plugin, qq, None, FakeTagSet())
        mode_logs = [m for m in collected if m.startswith("语音标签模式")]
        assert len(mode_logs) == 1, mode_logs

        # 换到能发语音的平台 → 打一次新状态
        await hook(plugin, FakeEvent(platform="Telegram"), None, FakeTagSet())
        mode_logs = [m for m in collected if m.startswith("语音标签模式")]
        assert len(mode_logs) == 2, mode_logs
        assert "发语音" in mode_logs[1]
    finally:
        target.removeHandler(handler)
        tmpdir.rm_tmpdir(d)


def run():
    import asyncio
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            asyncio.run(fn())
    print("test_plugin: 全部通过")


if __name__ == "__main__":
    run()
