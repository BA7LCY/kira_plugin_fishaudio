# -*- coding: utf-8 -*-
"""音色库管理（纯标准库实现，数据存插件自己的数据目录）。

音色库 = 用户收藏的音色（reference_id）集合，用名称做索引，存到插件数据目录的
voice_library.json。支持：列表 / 添加 / 切换 / 删除 / 设默认。

持久化采用先写 .tmp 再原子替换，避免写入一半损坏文件。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 音色库容量上限
MAX_VOICES = 50
# 名称长度上限
_NAME_MAX = 24
# 备注长度上限
_NOTE_MAX = 60
# 音色 ID：Fish 平台生成的是 32 位十六进制（即 16~64 位字母数字/下划线/短横）
_ID_PATTERN = re.compile(r"[A-Za-z0-9_\-]{16,64}")

# 文件格式版本号（未来结构变化时用于迁移）
_FILE_VERSION = 1


class VoiceLibraryError(ValueError):
    """音色库业务错误；message 可以直接展示给用户。"""


@dataclass
class Voice:
    """一条音色记录。"""

    id: str
    note: str = ""


def parse_voice_id(text: str) -> str:
    """从用户输入解析音色 ID：支持裸 ID，或音色页面的完整地址。"""
    t = str(text or "").strip().strip('"\'<>')
    # 去掉网址的查询串/锚点/结尾斜杠，取最后一段
    t = t.split("?")[0].split("#")[0].rstrip("/")
    t = t.rsplit("/", 1)[-1].strip()
    if not _ID_PATTERN.fullmatch(t):
        raise VoiceLibraryError(
            "音色 ID 看起来不对：应该是音色页面地址末尾那串 32 位字母数字，"
            "也可以直接贴整个地址。"
        )
    return t


def clean_name(name: str) -> str:
    """校验音色名称：非空、不超长、不含空白、不能是纯数字。"""
    name = str(name or "").strip()
    if not name:
        raise VoiceLibraryError("音色名称不能为空。")
    if len(name) > _NAME_MAX:
        raise VoiceLibraryError(f"音色名称最长 {_NAME_MAX} 个字符。")
    if any(c.isspace() for c in name):
        raise VoiceLibraryError("音色名称里不能有空格。")
    if name.isdigit():
        raise VoiceLibraryError("名称不能是纯数字，否则会和列表序号混淆。")
    return name


def clean_note(note: str) -> str:
    """校验备注：压缩空白、限长。"""
    note = re.sub(r"\s+", " ", str(note or "")).strip()
    if len(note) > _NOTE_MAX:
        raise VoiceLibraryError(f"备注最长 {_NOTE_MAX} 个字符。")
    return note


class VoiceLibrary:
    """音色库：名称→Voice 的有序映射，外加一个"当前使用的默认音色"。

    名称做键（保留用户输入的大小写，查找时大小写不敏感）；
    序号从 1 开始，对应 WebUI / 列表里的编号。
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else None
        self._voices: Dict[str, Voice] = {}
        self._active: Optional[str] = None
        if self.path is not None and self.path.exists():
            self.load()

    # ---------- 只读 ----------
    @property
    def active_name(self) -> str:
        """当前默认音色的名称；不存在则空字符串。"""
        if self._active and self._active in self._voices:
            return self._active
        return ""

    @property
    def active_id(self) -> str:
        """当前默认音色的 ID；不存在则空字符串。"""
        name = self.active_name
        return self._voices[name].id if name else ""

    def items(self) -> List[Tuple[int, str, Voice]]:
        """[(序号, 名称, Voice)]，保序。"""
        return [(i + 1, name, voice) for i, (name, voice) in enumerate(self._voices.items())]

    def get(self, key: str) -> Optional[Voice]:
        """按名称 / 序号取音色；找不到返回 None。"""
        try:
            return self._resolve(key)
        except VoiceLibraryError:
            return None

    def __len__(self) -> int:
        return len(self._voices)

    def _resolve(self, key: str) -> Voice:
        """把用户输入（名称或 1 起始序号）解析成 Voice。"""
        key = str(key or "").strip()
        if not key:
            raise VoiceLibraryError("请提供音色名称或序号。")
        if key in self._voices:
            return self._voices[key]
        low = key.lower()
        for name, voice in self._voices.items():
            if name.lower() == low:
                return voice
        if key.isdigit():
            idx = int(key)
            if 1 <= idx <= len(self._voices):
                return list(self._voices.values())[idx - 1]
        raise VoiceLibraryError(f"音色库里没有「{key}」；先用列表看看有哪些。")

    # ---------- 写操作（内存） ----------
    def add(self, name: str, voice_id: str, note: str = "") -> Tuple[str, bool]:
        """新增音色；同名（大小写不敏感）则覆盖。返回 (最终名称, 是否覆盖)。"""
        name = clean_name(name)
        voice_id = parse_voice_id(voice_id)
        note = clean_note(note)
        existing = self.get(name)
        if existing is not None:
            self._voices[name] = Voice(voice_id, note if note else existing.note)
            return name, True
        if len(self._voices) >= MAX_VOICES:
            raise VoiceLibraryError(f"音色库最多存 {MAX_VOICES} 个，先删掉不用的再添加。")
        self._voices[name] = Voice(voice_id, note)
        return name, False

    def set_note(self, key: str, note: str) -> None:
        """修改备注。"""
        voice = self._resolve(key)
        voice.note = clean_note(note)

    def use(self, key: str) -> str:
        """把某个音色设为默认。返回名称。"""
        voice = self._resolve(key)
        self._active = [n for n, v in self._voices.items() if v is voice][0]
        return self._active

    def clear_active(self) -> None:
        """清空默认音色。"""
        self._active = None

    def remove(self, key: str) -> bool:
        """删除音色；删的是默认音色则同时清空默认。返回是否删了。"""
        try:
            voice = self._resolve(key)
        except VoiceLibraryError:
            return False
        name = next(n for n, v in self._voices.items() if v is voice)
        del self._voices[name]
        if self._active == name:
            self._active = None
        return True

    # ---------- 持久化 ----------
    def save(self) -> None:
        """写盘：先写 .tmp 再原子替换。"""
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": _FILE_VERSION,
            "active": self._active,
            "voices": {name: {"id": v.id, "note": v.note} for name, v in self._voices.items()},
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def load(self) -> int:
        """读盘；文件损坏时改名 .bad 并从空开始。返回读到的条数。"""
        if self.path is None or not self.path.exists():
            return 0
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            bad = self.path.with_suffix(self.path.suffix + ".bad")
            try:
                os.replace(self.path, bad)
            except OSError:
                pass
            self._voices.clear()
            self._active = None
            return 0
        voices: Dict[str, Voice] = {}
        active: Optional[str] = None
        for name, item in (data.get("voices") or {}).items():
            if not isinstance(item, dict):
                continue
            vid = str(item.get("id") or "").strip()
            if not _ID_PATTERN.fullmatch(vid):
                continue
            voices[str(name)] = Voice(vid, str(item.get("note") or ""))
        active = str(data.get("active") or "")
        if active not in voices:
            active = None
        self._voices = voices
        self._active = active
        return len(voices)
