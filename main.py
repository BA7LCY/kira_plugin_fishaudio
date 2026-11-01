# -*- coding: utf-8 -*-
"""kira_plugin_fishaudio 插件主入口。

功能：
- <fish> 标签：让大模型在回复里用 <fish>文本</fish> 把内容转成语音发出。
- 音色库 WebUI：管理 Fish Audio 音色、试听、设置全局默认音色。

设计约定：
- 核心逻辑全部放在 core/ 纯 Python 模块里（emotion / voice_library / tts_client / settings），
  不依赖 Kira 框架，便于单元测试与迁移到其他框架。
- 依赖注入：FishTTS 支持注入 httpx 客户端（见 core/tts_client.py）；本类实例的
  self.tts / self.voice_lib 也可在测试中整体替换。
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel

from core.plugin import BasePlugin, logger, on, Priority, register, PluginPage, PageMenu
from core.chat.message_elements import Record, Text
from core.tag import BaseTag

from .core.emotion import EmotionRules, make_rules, normalize, strip_outside_code
from .core.platform import PlatformInfo, supports_voice
from .core.settings import Settings
from .core.tts_client import FishError, FishTTS
from .core.voice_library import (
    MAX_VOICES,
    VoiceLibrary,
    VoiceLibraryError,
    clean_name,
    clean_note,
    parse_voice_id,
)

# 试听默认文案（用户可改）
TEST_TEXT_DEFAULT = "你好，这是一条试听语音。"

# <fish> 标签的两种说明：平台能发语音时教它发语音，发不了就让它当普通文字
VOICE_TAG_DESCRIPTION = (
    "<fish>要转成语音的文字</fish>  # 用插件预设的默认音色把文字转成语音发出去。"
    "文字里可以带方括号情绪标记（例如 [happy]、[whispering]、[laughing]），"
    "这些标记只影响语气、不会被念出来。"
    "也可以临时指定音色：<fish voice=\"音色ID\">文字</fish>。"
    "不要和其他 msg 内标签混用。"
)
TEXT_TAG_DESCRIPTION = (
    "<fish>要发给用户的文字</fish>  # 当前聊天平台发不了语音，"
    "标签里的内容会按普通文字发出去，不影响聊天内容。"
)


class FishTag(BaseTag):
    """<fish> 标签的运行时实例。

    每次请求在 on.llm_request 钩子里按"这个平台能不能发语音"新建一个实例，
    并注册进本次请求的标签集，所以不同平台/会话互不影响。
    """

    name = "fish"
    parent = "msg"

    def __init__(self, plugin: "FishAudioPlugin", *, allow_voice: bool):
        super().__init__(ctx=getattr(plugin, "ctx", None))
        self._plugin = plugin
        self._allow_voice = bool(allow_voice)
        self.description = VOICE_TAG_DESCRIPTION if self._allow_voice else TEXT_TAG_DESCRIPTION

    async def handle(self, value: str, **kwargs) -> list:
        """把 <fish> 的内容交给插件：能发语音就合成语音，否则发文字。"""
        return await self._plugin.fish(value, allow_voice=self._allow_voice, **kwargs)

# 一次只允许一个试听请求：Fish 免费额度有限，避免并发把额度打光
_test_lock = asyncio.Lock()


def _http_error(status: int, message: str):
    """抛出一个带中文提示的 HTTP 错误（延迟导入 fastapi，便于无框架测试）。"""
    from fastapi import HTTPException

    raise HTTPException(status_code=status, detail=message)


class VoiceSaveBody(BaseModel):
    """新增/覆盖一个音色。"""

    name: str
    id: str
    note: str = ""


class VoiceKeyBody(BaseModel):
    """按名称/序号/ID 指定一个音色。"""

    key: str


class TtsTestBody(BaseModel):
    """试听请求体。"""

    text: str = TEST_TEXT_DEFAULT
    reference_id: str = ""


class FishAudioPlugin(BasePlugin):
    """Fish Audio 语音插件。"""

    def __init__(self, ctx, cfg: dict):
        super().__init__(ctx, cfg)
        # 插件数据目录（data/plugin_data/kira_plugin_fishaudio/）
        self.data_dir: Path = Path(ctx.get_plugin_data_dir())
        # 语音缓存目录（合成出的音频临时放在这里）
        self.cache_dir: Path = self.data_dir / "tts_cache"
        # 音色库文件
        self.library_path: Path = self.data_dir / "voice_library.json"

        # 依赖：initialize 时创建，测试里可以整体替换
        self.voice_lib: Optional[VoiceLibrary] = None
        self.tts: Optional[FishTTS] = None
        # 上一次的语音标签模式，用来避免每轮对话都重复打印同一行日志
        self._tag_state: Optional[tuple] = None

    # ---------- 生命周期 ----------

    async def initialize(self) -> None:
        """加载音色库、准备 TTS 客户端。"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.voice_lib = VoiceLibrary(self.library_path)
        self.voice_lib.load()
        self.tts = FishTTS(self.cache_dir)
        logger.info("FishAudio 插件初始化完成，音色库 %d 个音色", len(self.voice_lib))

    async def terminate(self) -> None:
        """卸载时保存音色库（幂等，可重复调用）。"""
        if self.voice_lib is not None:
            try:
                self.voice_lib.save()
            except OSError as e:
                logger.warning("保存音色库失败: %s", e)

    # ---------- <fish> 标签 ----------

    async def fish(self, content: str, allow_voice: bool = True, **attrs) -> list:
        """把标签内容合成语音；不能发语音或任何异常都降级为普通文字，不打扰用户。

        allow_voice 由 FishTag 按本次请求的平台能力传入（见 manage_builtin_tags）。
        """
        settings = self._settings()
        if not settings.enabled or not allow_voice:
            return [Text(self._plain_text(content, settings))]
        try:
            voice_attr = attrs.get("voice") or ""
            return await self._speak(content, settings, voice_attr)
        except (FishError, VoiceLibraryError, OSError) as e:
            logger.warning("语音合成降级为文字: %s", e)
            return [Text(self._plain_text(content, settings))]

    async def _speak(self, content: str, settings: Settings, voice_attr: str) -> list:
        """合成语音；超过文字上限或合成失败时返回文字。"""
        if self.tts is None:
            return [Text(self._plain_text(content, settings))]
        speech_text = self._speech_text(content, settings)
        # 转语音文字上限：超过则发文字
        if len(speech_text) > settings.max_chars:
            logger.info(
                "语音文本 %d 字超过上限 %d，降级为文字", len(speech_text), settings.max_chars
            )
            return [Text(self._plain_text(content, settings))]
        reference_id = self._resolve_reference_id(voice_attr, settings)
        audio_path = await self.tts.synthesize(
            speech_text, settings.make_fish_config(reference_id=reference_id)
        )
        logger.info("已合成语音: %s (%d 字节)", audio_path.name, audio_path.stat().st_size)
        return [Record(str(audio_path))]

    def _speech_text(self, content: str, settings: Settings) -> str:
        """真正交给 TTS 的文本：情绪开启时保留方括号标记（S2 只控制语气、不会念出）。"""
        if settings.emotion_enabled:
            return normalize(content, self._emotion_rules(settings))
        return content

    def _plain_text(self, content: str, settings: Settings) -> str:
        """给用户看的文字版：情绪开启且选择剥离时去掉方括号标记。"""
        if settings.emotion_enabled and settings.strip_in_text:
            return strip_outside_code(content, self._emotion_rules(settings))
        return content

    def _emotion_rules(self, settings: Settings) -> EmotionRules:
        """按配置构造情绪规则。"""
        return make_rules(
            mode=settings.tag_mode,
            extra=settings.extra_tags,
            max_tags=settings.max_tags,
        )

    def _resolve_reference_id(self, voice_attr: str, settings: Settings) -> str:
        """决定用哪个音色：voice 属性 > 音色库默认 > 配置兜底。"""
        if voice_attr:
            # 先尝试当成音色 ID 或音色页面地址
            try:
                return parse_voice_id(voice_attr)
            except VoiceLibraryError:
                pass
            # 再尝试当成音色库里的名称
            if self.voice_lib is not None:
                hit = self.voice_lib.get(voice_attr)
                if hit is not None:
                    return hit.id
            logger.warning("voice 属性「%s」无法识别，改用默认音色", voice_attr)
        # 音色库里设置的默认音色优先
        if self.voice_lib is not None and self.voice_lib.active_id:
            return self.voice_lib.active_id
        # 配置里的兜底音色
        return settings.reference_id

    # ---------- 接管框架自带的 <record> 语音标签 ----------

    @on.llm_request(priority=Priority.MEDIUM)
    async def manage_builtin_tags(self, event, req, tag_set, *_):
        """准备本次请求的语音标签。

        做两件事：
        1. 屏蔽框架自带的 <record> 标签：框架在 SYS_HIGH（先执行）里按会话能力
           注册它，本钩子用 MEDIUM（后执行）把它移除，避免大模型在两条语音方案里选错。
           只有插件启用、配了 API Key 且开关打开时才接管，没配 Key 时保留框架自带的兜底。
        2. 按当前平台能力注册 <fish> 标签：能发语音就合成语音，发不了就发文字
           （例如 QQ 官方机器人适配器会把 record 变成一句 "[Unsupported message element]"）。
        """
        settings = self._settings()
        blocked_record = bool(
            settings.enabled and settings.disable_builtin_record and settings.api_key
        )
        if blocked_record:
            tag_set.unregister("record")

        allow_voice = settings.enabled and self._platform_supports_voice(event, settings)
        tag_set.register(FishTag(self, allow_voice=allow_voice))
        self._log_tag_mode(settings, allow_voice, blocked_record, event)

    def _platform_info(self, event) -> PlatformInfo:
        """从事件里收集平台信息（平台名、适配器名、适配器实现模块）。"""
        info = getattr(event, "adapter", None)
        platform = str(getattr(info, "platform", "") or "")
        adapter_name = str(getattr(info, "name", "") or "")
        adapter_id = str(getattr(info, "adapter_id", "") or "")

        module = ""
        manager = getattr(self.ctx, "adapter_mgr", None)
        if manager is not None and adapter_name:
            try:
                adapter = manager.get_adapter(adapter_name)
                if adapter is not None:
                    module = type(adapter).__module__ or ""
            except Exception:  # 适配器管理器接口变动时不影响聊天
                module = ""

        elements = getattr(event, "supported_elements", None) or ()
        return PlatformInfo(
            platform=platform,
            adapter_name=adapter_name,
            adapter_id=adapter_id,
            adapter_module=module,
            supported_elements=tuple(str(item) for item in elements),
        )

    def _platform_supports_voice(self, event, settings: Settings) -> bool:
        """当前平台能不能真的发语音（不能则 <fish> 降级成文字）。"""
        info = self._platform_info(event)
        return supports_voice(info, settings.text_only_platforms)

    def _log_tag_mode(
        self, settings: Settings, allow_voice: bool, blocked_record: bool, event=None
    ) -> None:
        """只在语音标签模式变化时打一行日志，同一模式下每轮对话不重复刷屏。

        模式由「插件开关、有没有 Key、能不能发语音、平台、框架 record 是否屏蔽」决定；
        任何一项变了（比如换了平台、改了配置）才会再打一次。
        """
        platform = ""
        if event is not None:
            platform = self._platform_info(event).platform
        state = (settings.enabled, bool(settings.api_key), allow_voice, platform, blocked_record)
        if state == self._tag_state:
            return
        self._tag_state = state

        parts = []
        if not settings.enabled:
            parts.append("插件已关闭")
        if not settings.api_key:
            parts.append("还没填 API Key")
        if allow_voice:
            parts.append("<fish> 发语音")
        else:
            where = f"（平台：{platform}）" if platform else ""
            parts.append(f"<fish> 发文字{where}")
        parts.append("框架自带 <record> 已屏蔽" if blocked_record else "框架自带 <record> 保留")
        logger.debug("语音标签模式：%s", "；".join(parts))

    # ---------- 配置 ----------

    def _settings(self) -> Settings:
        """从插件配置构造设置对象（每次调用重新解析，保证拿到最新配置）。"""
        return Settings.from_dict(self.plugin_cfg)

    # ---------- 音色库 WebUI API ----------

    @register.api("GET", "/voices", auth=True)
    async def api_list_voices(self) -> dict:
        """音色列表 + 当前默认音色。"""
        return self._voices_payload()

    @register.api("POST", "/voices/save", auth=True)
    async def api_save_voice(self, body: VoiceSaveBody) -> dict:
        """新增/覆盖一个音色。"""
        try:
            name = clean_name(body.name)
            note = clean_note(body.note)
            voice_id = parse_voice_id(body.id)
        except VoiceLibraryError as e:
            _http_error(400, str(e))
        self.voice_lib.add(name, voice_id, note=note)
        self._save_library()
        return self._voices_payload()

    @register.api("POST", "/voices/use", auth=True)
    async def api_use_voice(self, body: VoiceKeyBody) -> dict:
        """把某个音色设为全局默认。"""
        try:
            self.voice_lib.use(body.key)
        except VoiceLibraryError as e:
            _http_error(400, str(e))
        self._save_library()
        return self._voices_payload()

    @register.api("POST", "/voices/remove", auth=True)
    async def api_remove_voice(self, body: VoiceKeyBody) -> dict:
        """删除一个音色。"""
        try:
            self.voice_lib.remove(body.key)
        except VoiceLibraryError as e:
            _http_error(400, str(e))
        self._save_library()
        return self._voices_payload()

    @register.api("POST", "/voices/clear", auth=True)
    async def api_clear_voice(self) -> dict:
        """取消默认音色（回到配置里的兜底音色）。"""
        self.voice_lib.clear_active()
        self._save_library()
        return self._voices_payload()

    @register.api("POST", "/tts/test", auth=True)
    async def api_tts_test(self, body: TtsTestBody) -> dict:
        """合成一条试听语音，返回 base64 音频。"""
        if self.tts is None:
            _http_error(503, "插件还没准备好，请稍后再试。")
        settings = self._settings()
        if not settings.api_key:
            _http_error(400, "还没有配置 Fish API Key，请先在插件设置里填写。")
        text = (body.text or TEST_TEXT_DEFAULT).strip()
        if not text:
            _http_error(400, "试听文本不能为空。")
        if len(text) > settings.max_chars:
            _http_error(400, f"试听文本超过 {settings.max_chars} 字的转语音上限。")
        reference_id = body.reference_id or self._resolve_reference_id("", settings)
        cfg = settings.make_fish_config(reference_id=reference_id, format="mp3")
        async with _test_lock:
            try:
                audio_path = await self.tts.synthesize(text, cfg)
            except FishError as e:
                _http_error(502, f"语音合成失败：{e}")
        mime = mimetypes.guess_type(str(audio_path))[0] or "audio/mpeg"
        data = audio_path.read_bytes()
        return {
            "audio": base64.b64encode(data).decode("ascii"),
            "mime": mime,
            "length": len(data),
        }

    # ---------- 页面 ----------

    @register.page(
        route="/voices",
        auth=True,
        menu=PageMenu(label={"zh": "Fish 音色库", "en": "Fish Voices"}, icon="Microphone", order=110),
    )
    def page_voices(self) -> PluginPage:
        """音色库管理页面。"""
        return PluginPage.from_folder("./web")

    # ---------- 内部工具 ----------

    def _voices_payload(self) -> dict:
        """WebUI 用的音色库数据。"""
        voices = []
        for idx, name, voice in self.voice_lib.items():
            voices.append({"idx": idx, "name": name, "id": voice.id, "note": voice.note})
        return {
            "voices": voices,
            "count": len(self.voice_lib),
            "max_voices": MAX_VOICES,
            "active": self.voice_lib.active_name or "",
            "config_default": self._settings().reference_id,
        }

    def _save_library(self) -> None:
        """保存音色库，失败只告警、不阻塞接口。"""
        try:
            self.voice_lib.save()
        except OSError as e:
            logger.warning("保存音色库失败: %s", e)
