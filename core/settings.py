# -*- coding: utf-8 -*-
"""插件配置的集中解析（纯标准库，无框架依赖）。

把框架给的原始 dict 配置一次性转成带类型、带默认值、带边界钳制的 Settings，
后续代码只读 Settings，避免到处散落 cfg.get(...) 和防御式强转。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

from .tts_client import FishConfig

# 插件唯一 ID（与目录名、manifest.json 保持一致）
PLUGIN_ID = "kira_plugin_fishaudio"

# 各配置项的默认值（与 schema.json 的 default 保持一致，两处需要同步修改）
DEFAULTS = {
    # 基础
    "enabled": True,
    "max_chars": 80,       # 转语音文字上限（字符）
    "timeout": 30.0,       # 单次请求超时（秒）
    "format": "mp3",
    # Fish 服务
    "api_key": "",
    "api_base": "https://api.fish.audio",
    "model": "s2.1-pro",
    "reference_id": "",    # 兜底音色（音色库默认音色优先于它）
    # 情绪
    "emotion_enabled": True,
    "tag_mode": "any",     # any | whitelist
    "extra_tags": "",      # 白名单额外标签，逗号分隔
    "max_tags": 6,         # 转语音时最多保留几个情绪标签
    "strip_in_text": False,  # 发普通文字时是否剥离情绪标签
    # 高级
    "speed": 1.0,          # 语速 0.5~2.0
    "volume": 0.0,         # 音量（分贝）
    "temperature": 0.0,    # 0~1
    "top_p": 0.0,          # 0~1
    "latency": "normal",   # normal | balanced | low
    "normalize": True,     # 数字/日期口语化
    "retries": 1,          # 失败重试次数 0~3
    "extra_body": {},      # 附加请求字段（JSON）
}

_MODELS = ("s2.1-pro", "s2-pro", "s2.1-pro-free", "drama-3")
_FORMATS = ("mp3", "wav", "opus")
_LATENCIES = ("normal", "balanced", "low")


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off"):
            return False
    return default


def _as_float(value: Any, default: float, lo: float = -float("inf"), hi: float = float("inf")) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(v, lo), hi)


def _as_int(value: Any, default: int, lo: int = -(10**9), hi: int = 10**9) -> int:
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return default
    return min(max(v, lo), hi)


def _as_str(value: Any, default: str) -> str:
    if value is None:
        return default
    return str(value)


def _as_choice(value: Any, default: str, choices: Tuple[str, ...]) -> str:
    v = _as_str(value, default)
    return v if v in choices else default


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


@dataclass(frozen=True)
class Settings:
    """解析后的插件配置（不可变）。"""

    # 基础
    enabled: bool = True
    max_chars: int = 80
    timeout: float = 30.0
    format: str = "mp3"
    # Fish 服务
    api_key: str = ""
    api_base: str = "https://api.fish.audio"
    model: str = "s2.1-pro"
    reference_id: str = ""
    # 情绪
    emotion_enabled: bool = True
    tag_mode: str = "any"
    extra_tags: Tuple[str, ...] = ()
    max_tags: int = 6
    strip_in_text: bool = False
    # 高级
    speed: float = 1.0
    volume: float = 0.0
    temperature: float = 0.0
    top_p: float = 0.0
    latency: str = "normal"
    normalize: bool = True
    retries: int = 1
    extra_body: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, cfg: Dict[str, Any]) -> "Settings":
        """把框架原始配置转成 Settings（顶层缺键用默认值，非法值回退默认值）。"""
        basic = cfg.get("basic", {}) if isinstance(cfg, dict) else {}
        fish = cfg.get("fish", {}) if isinstance(cfg, dict) else {}
        emotion = cfg.get("emotion", {}) if isinstance(cfg, dict) else {}
        advanced = cfg.get("advanced", {}) if isinstance(cfg, dict) else {}

        # 自定义白名单标签：逗号 / 换行分隔
        extra_raw = _as_str(emotion.get("extra_tags"), "")
        extra_tags = tuple(
            t.strip() for t in extra_raw.replace("\n", ",").replace("，", ",").split(",") if t.strip()
        )

        return cls(
            enabled=_as_bool(basic.get("enabled"), DEFAULTS["enabled"]),
            max_chars=_as_int(basic.get("max_chars"), DEFAULTS["max_chars"], 1, 500),
            timeout=_as_float(basic.get("timeout"), DEFAULTS["timeout"], 1.0, 300.0),
            format=_as_choice(basic.get("format"), DEFAULTS["format"], _FORMATS),
            api_key=_as_str(fish.get("api_key"), DEFAULTS["api_key"]),
            api_base=_as_str(fish.get("api_base"), DEFAULTS["api_base"]),
            model=_as_choice(fish.get("model"), DEFAULTS["model"], _MODELS),
            reference_id=_as_str(fish.get("reference_id"), DEFAULTS["reference_id"]),
            emotion_enabled=_as_bool(emotion.get("enabled"), DEFAULTS["emotion_enabled"]),
            tag_mode=_as_choice(emotion.get("tag_mode"), DEFAULTS["tag_mode"], ("any", "whitelist")),
            extra_tags=extra_tags,
            max_tags=_as_int(emotion.get("max_tags"), DEFAULTS["max_tags"], 0, 20),
            strip_in_text=_as_bool(emotion.get("strip_in_text"), DEFAULTS["strip_in_text"]),
            speed=_as_float(advanced.get("speed"), DEFAULTS["speed"], 0.5, 2.0),
            volume=_as_float(advanced.get("volume"), DEFAULTS["volume"], -24.0, 24.0),
            temperature=_as_float(advanced.get("temperature"), DEFAULTS["temperature"], 0.0, 1.0),
            top_p=_as_float(advanced.get("top_p"), DEFAULTS["top_p"], 0.0, 1.0),
            latency=_as_choice(advanced.get("latency"), DEFAULTS["latency"], _LATENCIES),
            normalize=_as_bool(advanced.get("normalize"), DEFAULTS["normalize"]),
            retries=_as_int(advanced.get("retries"), DEFAULTS["retries"], 0, 3),
            extra_body=_as_dict(advanced.get("extra_body")),
        )

    def make_fish_config(self, reference_id: str = "", format: str = "") -> FishConfig:
        """转成 FishConfig；reference_id / format 传参时优先于配置。"""
        return FishConfig(
            api_key=self.api_key,
            api_base=self.api_base,
            reference_id=reference_id or self.reference_id,
            model=self.model,
            format=format or self.format,
            temperature=self.temperature,
            top_p=self.top_p,
            speed=self.speed,
            volume=self.volume,
            latency=self.latency,
            normalize=self.normalize,
            extra_body=self.extra_body,
            retries=self.retries,
            timeout=self.timeout,
        )
