"""识别并去掉模型未加标签的「自述计划」文本。

MuiceBot 的 ``thought_processor`` 只认 `` thinking...</think>``；有些 OpenAI 兼容上游
（本机是 Antigravity2API 的 ``gemini-3.8-flash-high``）会把计划直接塞进 ``delta.content``，
没有标签，于是被当成正文发出去。这里只认**明确的自我计划短语**，不按语言或长度乱杀，
正常的中文回复（哪怕夹着英文术语）原样放行。
"""

from __future__ import annotations

import re

#: 模型在自言自语时才会出现的短语。宁可漏掉一次，也不要把正常回复误判掉。
PLANNING_MARKERS = (
    "initial response",
    "finalizing",
    "the response is",
    "after careful consideration",
    "aligning with the character",
    "the character's response",
    "seems best",
    "let me think",
    "i should respond",
    "the user is asking",
    "rover's playful",
    "shorekeeper's response",
    "tone and brevity",
)

_MARKER_RE = re.compile("|".join(re.escape(m) for m in PLANNING_MARKERS), re.IGNORECASE)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_WORD_RE = re.compile(r"[a-z']+")
#: 英文计划文本的辅助判据：英文占比高且同时出现这些词。单靠英文不算。
_META_WORDS = frozenset(
    {"character", "persona", "tone", "brevity", "roleplay", "response", "reply", "playful"}
)
#: 至少命中两个「角色扮演」词，避免把 HTTP response / character encoding 这类技术英文误杀。
_ROLE_WORDS = frozenset({"character", "persona", "tone", "brevity", "roleplay", "playful"})


def has_marker(text: str) -> bool:
    return bool(_MARKER_RE.search(text or ""))


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def english_heavy(text: str) -> bool:
    latin = len(_LATIN_RE.findall(text or ""))
    cjk = len(_CJK_RE.findall(text or ""))
    total = latin + cjk
    return total > 0 and latin / total >= 0.6


def is_planning(text: str) -> bool:
    """这段文字是不是模型在讲「我打算怎么回」。"""
    s = (text or "").strip()
    if not s:
        return False
    if has_marker(s):
        return True
    words = _WORD_RE.findall(s.lower())
    return (
        len(words) >= 12
        and english_heavy(s)
        and len(set(words) & _META_WORDS) >= 3
        and len(set(words) & _ROLE_WORDS) >= 2
    )


def sanitize(text: str) -> tuple[str, bool]:
    """按段去掉计划文本，返回 ``(干净正文, 是否丢过东西)``。"""
    s = (text or "").strip()
    if not s:
        return "", False
    paragraphs = [p.strip() for p in re.split(r"\n+", s) if p.strip()]
    kept = [p for p in paragraphs if not is_planning(p)]
    clean = "\n".join(kept).strip()
    return clean, len(kept) != len(paragraphs)


class StreamGuard:
    """流式守卫：先扣住一小段，判定不是计划再放行。

    正常中文回复含汉字、没有计划短语，第一段就放行；计划文本一旦确认，后续整条流都丢掉，
    这样 ``model_pref`` 的救援判据会看到「没产出」，自动换次选。
    """

    def __init__(self, decide_after: int = 80):
        self.decide_after = decide_after
        self.dropped = False
        self._buf = ""
        self._decided = False
        self._drop = False

    def feed(self, text: str) -> str:
        if not text:
            return ""
        if self._decided:
            if self._drop:
                return ""
            if has_marker(text):
                self.dropped = True
                return ""
            return text
        self._buf += text
        if not self._ready():
            return ""
        return self._decide()

    def flush(self) -> str:
        if self._decided:
            return ""
        return self._decide()

    def _ready(self) -> bool:
        b = self._buf
        if has_marker(b):
            return True
        if has_cjk(b) and not english_heavy(b):
            return True
        return len(b) >= self.decide_after or "\n" in b

    def _decide(self) -> str:
        clean, dropped = sanitize(self._buf)
        self._buf = ""
        self._decided = True
        self.dropped = self.dropped or dropped
        self._drop = dropped and not clean
        return "" if self._drop else clean
