# -*- coding: utf-8 -*-
"""情绪标签解析与处理（纯标准库实现）。

Fish Audio S2 系列模型支持在文本中用方括号写入情绪标记（如 [happy]），
模型只把它当作语气控制，不会真的念出来。本模块负责：
  - 识别文本中的情绪标签（支持全角/半角方括号，避开代码、链接等误判）
  - 按配置的白名单 / 数量上限做过滤
  - 转语音前：归一化（保留标签、半角化、超限丢弃）
  - 发文字前：剥离标签（可保留代码块内部不动）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import FrozenSet, Iterable, Iterator, Tuple

# 基础情绪（Fish 官方示例清单）
_BASIC = (
    "happy", "sad", "angry", "excited", "calm", "nervous", "confident",
    "surprised", "satisfied", "delighted", "scared", "worried", "upset",
    "frustrated", "depressed", "empathetic", "embarrassed", "disgusted",
    "moved", "proud", "relaxed", "grateful", "curious", "sarcastic",
)

# 进阶情绪
_ADVANCED = (
    "disdainful", "unhappy", "anxious", "hysterical", "indifferent",
    "uncertain", "doubtful", "confused", "disappointed", "regretful",
    "guilty", "ashamed", "jealous", "envious", "hopeful", "optimistic",
    "pessimistic", "nostalgic", "lonely", "bored", "contemptuous",
    "sympathetic", "compassionate", "determined", "resigned",
)

# 音调（语气）
_TONES = (
    "in a hurry tone", "shouting", "screaming", "whispering", "soft tone",
    "emphasis",
)

# 音效（拟声）
_EFFECTS = (
    "laughing", "chuckling", "sobbing", "crying loudly", "sighing",
    "groaning", "panting", "gasping", "yawning", "snoring", "clear throat",
)

# 特殊效果
_SPECIAL = ("audience laughing", "background laughter", "crowd laughing", "break", "long-break")

# 其他常见但未列入官方清单的标签（默认放行）
_SEEN = (
    "friendly", "narrator", "mysterious", "relieved", "enthusiastic",
    "encouraging", "urgent", "furious", "terrified", "ecstatic", "interested",
)

# 强度修饰词（可放在情绪前，如 [slightly sad]）
_MODIFIERS = ("slightly", "very", "extremely", "a bit", "somewhat")

# 全部已知标签
KNOWN_TAGS: FrozenSet[str] = frozenset(
    _BASIC + _ADVANCED + _TONES + _EFFECTS + _SPECIAL + _SEEN
)

# 过滤模式：any = 全部放行；whitelist = 只放行已知标签 + 自定义
MODES: Tuple[str, ...] = ("any", "whitelist")

# 情绪标签正则：排除前面的字母数字/下划线/右括号，避免误伤 arr[i]、f(x)[0]、
# markdown 链接等；内容 1~40 字符且不含方括号与换行；后面不能紧跟左括号
_TAG = re.compile(r"(?<![A-Za-z0-9_\)])[\[［]([^\[\]［］\r\n]{1,40})[\]］](?!\()")

# 是否含字母（判断"标签内容"是否为纯符号）
_HAS_LETTER = re.compile(r"[^\W\d_]")

# 代码块 / 行内代码切分（保留顺序，奇数位是代码）
_CODE_SPLIT = re.compile(r"(```.*?```|`[^`\n]+`)", re.DOTALL)


@dataclass(frozen=True)
class EmotionRules:
    """情绪过滤规则（不可变）。"""

    mode: str = "any"          # any | whitelist
    extra: FrozenSet[str] = frozenset()   # 自定义额外允许的标签
    max_tags: int = 6          # 转语音时最多保留几个标签


def make_rules(mode: str, extra: Iterable[str] = (), max_tags: int = 6) -> EmotionRules:
    """由配置构造规则：归一化额外标签（去空白/去括号/小写/压缩空格）。"""
    mode = (mode or "any").lower()
    if mode not in MODES:
        mode = "any"
    cleaned = set()
    for tag in extra or ():
        t = str(tag).strip()
        t = t.strip("[]［］")
        t = re.sub(r"\s+", " ", t.lower())
        if t:
            cleaned.add(t)
    try:
        max_tags = max(0, int(max_tags))
    except (TypeError, ValueError):
        max_tags = 6
    return EmotionRules(mode=mode, extra=frozenset(cleaned), max_tags=max_tags)


def is_tag(inner: str, rules: EmotionRules) -> bool:
    """判断一段方括号内的文字是否算情绪标签。"""
    inner = inner.strip()
    if not inner or not _HAS_LETTER.search(inner):
        return False
    low = inner.lower()
    # 明显不是情绪：CQ 码、at、链接
    if low.startswith("cq:") or low.startswith("at:") or "://" in low or low.startswith("www."):
        return False
    if rules.mode != "whitelist":
        return True
    known = KNOWN_TAGS | rules.extra
    if low in known:
        return True
    # 支持修饰词前缀，如 "very happy"、"a bit sad"
    for mod in _MODIFIERS:
        if low.startswith(mod + " ") and low[len(mod) + 1:] in known:
            return True
    return False


def _tags(text: str, rules: EmotionRules) -> Iterator[Tuple[int, int, str]]:
    """产出所有有效标签的 (起始, 结束, 内容)。"""
    prev_end = -1
    for m in _TAG.finditer(text):
        if not is_tag(m.group(1), rules):
            continue
        # 排除紧跟在已认标签后的嵌套方括号（如 [very [sad]] 的后半）
        if prev_end >= 0 and m.start() < prev_end and text[m.start() - 1] == "]":
            continue
        prev_end = m.end()
        yield m.start(), m.end(), m.group(1)


def count(text: str, rules: EmotionRules | None = None) -> int:
    """统计文本里有效情绪标签个数。"""
    rules = rules or EmotionRules()
    return sum(1 for _ in _tags(text, rules))


def normalize(text: str, rules: EmotionRules | None = None) -> str:
    """转语音前归一化：全角括号转半角、丢弃超限标签。"""
    rules = rules or EmotionRules()
    text = text.replace("［", "[").replace("］", "]")
    keep = []
    for start, end, _inner in _tags(text, rules):
        keep.append((start, end))
    if len(keep) <= rules.max_tags:
        return text
    drop = set(keep[rules.max_tags:])
    parts = []
    last = 0
    for start, end in keep:
        if (start, end) in drop:
            parts.append(text[last:start])
            last = end
    parts.append(text[last:])
    return "".join(parts)


def strip(text: str, rules: EmotionRules | None = None) -> str:
    """去掉所有有效情绪标签；行首标签连同其后的空格一起去掉。"""
    rules = rules or EmotionRules()
    tags = list(_tags(text, rules))
    if not tags:
        return text
    parts = []
    last = 0
    for start, end, _inner in tags:
        parts.append(text[last:start])
        # 若该标签位于（当前已拼接内容的）行首，且后面跟着空格，则一并去掉
        seg = "".join(parts)
        at_line_start = (seg == "" or seg.endswith("\n"))
        if at_line_start and end < len(text) and text[end] == " ":
            end += 1
        last = end
    parts.append(text[last:])
    return "".join(parts)


def strip_outside_code(text: str, rules: EmotionRules | None = None) -> str:
    """去掉代码块/行内代码之外的标签；代码内部原样保留。"""
    rules = rules or EmotionRules()
    pieces = _CODE_SPLIT.split(text)
    out = []
    for i, piece in enumerate(pieces):
        if i % 2 == 1:
            out.append(piece)  # 代码段，不处理
        else:
            out.append(strip(piece, rules))
    return "".join(out)
