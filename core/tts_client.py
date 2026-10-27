# -*- coding: utf-8 -*-
"""Fish Audio TTS 客户端（httpx 异步实现）。

职责：
  - 构造请求（endpoint / build_payload / build_headers，纯函数方便单测）
  - 调用 /v1/tts 接口合成音频（带重试与错误识别）
  - 落盘到输出目录并定期清理旧文件

依赖注入：FishTTS 接受外部传入的 httpx.AsyncClient（或 client_factory），
测试时可注入带 MockTransport 的假客户端，无需真实网络。

请求要点（Fish 官方 API）：
  - 模型通过 HTTP 头 model 选择，不是请求体字段
  - 情绪标记直接写在 text 里（如 [happy]），S2 系列支持任意自然语言描述
  - 认证：Authorization: Bearer <API_KEY>
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

try:
    import httpx
except ImportError:  # 纯逻辑单测环境可能没有 httpx，允许延迟报错
    httpx = None  # type: ignore

# 默认服务地址
DEFAULT_BASE = "https://api.fish.audio"

# 请求头里的 UA
_USER_AGENT = "kira_plugin_fishaudio/0.1"

# 支持的音频格式
_FORMATS = ("mp3", "wav", "opus")

# 支持的延迟档位
_LATENCIES = ("normal", "balanced", "low")

# 落盘文件前缀
_FILE_PREFIX = "fishaudio_"

# 缓存文件保留时长与清理间隔（秒）
_KEEP_SECONDS = 3600.0
_CLEAN_EVERY = 600.0

# 错误响应体预览长度
_BODY_PREVIEW = 300

# timeout=0 时使用的兜底总等待（秒）
_MAX_WAIT = 120.0


class FishError(Exception):
    """Fish Audio 请求错误。

    - message：可直接展示给用户的中文描述
    - status：HTTP 状态码（网络层错误为 0）
    - retryable：是否值得重试（网络抖动 / 5xx / 空音频）
    """

    def __init__(self, message: str, status: int = 0, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retryable = retryable


@dataclass(frozen=True)
class FishConfig:
    """一次 TTS 请求的全部参数（不可变）。0 值字段不会出现在请求体里。"""

    api_key: str = ""
    api_base: str = DEFAULT_BASE
    reference_id: str = ""      # 音色 ID；空则不传（用模型默认音色）
    model: str = "s2.1-pro"
    format: str = "mp3"          # mp3 | wav | opus
    temperature: float = 0.0     # 0 不传
    top_p: float = 0.0           # 0 不传
    speed: float = 1.0           # 1.0 不传；范围 0.5~2.0
    volume: float = 0.0          # 分贝；0 不传
    latency: str = "normal"      # normal | balanced | low；normal 不传
    normalize: bool = True       # 数字/日期口语化；True 不传
    extra_body: dict = field(default_factory=dict)  # 额外字段；不能覆盖 text
    retries: int = 1             # 失败后的额外重试次数
    timeout: float = 30.0        # 单次请求超时（秒）；0 = 不限时


def endpoint(api_base: str) -> str:
    """把配置里的服务地址规整成 /v1/tts 接口地址。"""
    base = (api_base or DEFAULT_BASE).rstrip("/")
    if base.endswith("/v1/tts"):
        return base
    if base.endswith("/v1"):
        return base + "/tts"
    return base + "/v1/tts"


def build_payload(text: str, cfg: FishConfig) -> dict:
    """构造请求体：只发必要字段，0 值 / 默认值一律不发。"""
    payload: dict = {"text": text, "format": cfg.format}
    if cfg.reference_id:
        payload["reference_id"] = cfg.reference_id
    if cfg.temperature > 0:
        payload["temperature"] = min(cfg.temperature, 1.0)
    if cfg.top_p > 0:
        payload["top_p"] = min(cfg.top_p, 1.0)
    prosody: dict = {}
    if abs(cfg.speed - 1.0) > 1e-9:
        prosody["speed"] = min(max(cfg.speed, 0.5), 2.0)
    if abs(cfg.volume) > 1e-6:
        prosody["volume"] = cfg.volume
    if prosody:
        payload["prosody"] = prosody
    if cfg.latency in _LATENCIES and cfg.latency != "normal":
        payload["latency"] = cfg.latency
    if not cfg.normalize:
        payload["normalize"] = False
    for key, value in (cfg.extra_body or {}).items():
        if key != "text":
            payload[key] = value
    return payload


def build_headers(cfg: FishConfig) -> dict:
    """构造请求头：注意 model 是 HTTP 头，不是请求体字段。"""
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
        "model": cfg.model,
    }
    return headers


def _extract_error(resp) -> str:
    """从错误响应里尽量取出可读的报错信息。"""
    try:
        data = resp.json()
        return str(data.get("detail") or data.get("message") or data)[:_BODY_PREVIEW]
    except Exception:
        return (resp.text or "")[:_BODY_PREVIEW]


class FishTTS:
    """Fish Audio TTS 合成器。

    依赖注入方式（二选一）：
      - client：直接给一个已配置好的 httpx.AsyncClient（如带 MockTransport 的测试客户端），
        调用方负责关闭；
      - client_factory：一个返回 httpx.AsyncClient 的工厂函数（收 timeout 关键字），
        由本类按次创建并关闭。
    都不给时，用默认 httpx.AsyncClient 按配置超时创建。
    """

    def __init__(
        self,
        out_dir: Optional[Path] = None,
        client: Optional["httpx.AsyncClient"] = None,
        client_factory: Optional[Callable[..., "httpx.AsyncClient"]] = None,
    ):
        self.out_dir = Path(out_dir) if out_dir is not None else None
        self._client = client
        self._client_factory = client_factory
        self._last_cleanup = 0.0

    def _acquire_client(self, timeout: float) -> "httpx.AsyncClient":
        """按注入方式获取客户端；外部注入的客户端由调用方负责生命周期。"""
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            return self._client_factory(timeout=timeout)
        if httpx is None:
            raise FishError("缺少 httpx 依赖，请先安装 requirements.txt。")
        return httpx.AsyncClient(timeout=timeout)

    async def synthesize(self, text: str, cfg: FishConfig) -> Path:
        """合成一段语音，返回音频文件路径。"""
        if not cfg.api_key:
            raise FishError("没有配置 Fish API Key，请在插件设置里填写。")
        if self.out_dir is None:
            raise FishError("没有设置输出目录。")
        data = await self._request(text, cfg)
        return self._save(data, cfg.format)

    async def _request(self, text: str, cfg: FishConfig) -> bytes:
        """带重试地请求 TTS 接口，返回音频字节。"""
        timeout = cfg.timeout if cfg.timeout > 0 else _MAX_WAIT
        client = self._acquire_client(timeout)
        owns = client is not self._client
        try:
            attempts = 1 + max(0, cfg.retries)
            for attempt in range(1, attempts + 1):
                try:
                    return await self._once(client, text, cfg)
                except FishError as e:
                    if not e.retryable or attempt >= attempts:
                        raise
                    await asyncio.sleep(0.5)
            raise FishError("合成失败：多次重试后仍然出错。")
        finally:
            if owns:
                await client.aclose()

    async def _once(self, client, text: str, cfg: FishConfig) -> bytes:
        """单次请求：校验状态码 / 内容类型 / 音频非空。"""
        url = endpoint(cfg.api_base)
        payload = build_payload(text, cfg)
        headers = build_headers(cfg)
        try:
            resp = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as e:
            raise FishError(f"请求 Fish Audio 失败：{e}", retryable=True)
        if resp.status_code != 200:
            raise FishError(
                f"Fish Audio 返回错误（{resp.status_code}）：{_extract_error(resp)}",
                status=resp.status_code,
                retryable=resp.status_code >= 500,
            )
        content_type = resp.headers.get("content-type", "")
        # 反代错误页通常返回 json 或 text，正常音频是 audio/* 二进制
        if content_type.startswith("application/json") or content_type.startswith("text/"):
            raise FishError(
                f"Fish Audio 返回了异常内容（{content_type}）：{_extract_error(resp)}",
                status=resp.status_code,
                retryable=True,
            )
        if not resp.content:
            raise FishError("Fish Audio 返回了空音频。", retryable=True)
        return resp.content

    def _save(self, data: bytes, fmt: str) -> Path:
        """音频落盘并顺手清理过期文件。"""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        name = f"{_FILE_PREFIX}{int(time.time() * 1000)}_{uuid.uuid4().hex[:12]}.{fmt}"
        path = self.out_dir / name
        path.write_bytes(data)
        self._cleanup()
        return path

    def _cleanup(self) -> None:
        """定期删除超过保留时长的缓存音频。"""
        now = time.monotonic()
        if now - self._last_cleanup < _CLEAN_EVERY:
            return
        self._last_cleanup = now
        cutoff = time.time() - _KEEP_SECONDS
        try:
            for f in self.out_dir.glob(_FILE_PREFIX + "*"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                except OSError:
                    pass
        except OSError:
            pass
