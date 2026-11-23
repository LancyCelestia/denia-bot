"""识别并去掉模型未加标签的「自述计划」文本。

MuiceBot 的 ``thought_processor`` 只认 `` thinking...</think>``；有些 OpenAI 兼容上游
（本机是 Antigravity2API 的 ``gemini-3.8-flash-high``）会把计划直接塞进 ``delta.content``，
没有标签，于是被当成正文发出去。这里只认**明确的自我计划语态**，不按语言或长度乱杀，
正常的中文/英文回复原样放行。

判断分三层，越往后越保守：

1. 计划开场白（``Initial response``）在段首；
2. 计划专属短语（``Finalizing`` / ``aligning with the character`` / ``the character's response``）
   且英文占比高、至少两个角色扮演词；
3. 没命中短语时的窄英文兜底：至少 12 个词、英文占比高、至少三个元词和两个角色词。

正常英文里的 ``The response is ...``、``Let me think ...``、``seems best`` 单独出现不算；
它们只有在和计划专属短语/兜底词同时出现时才可能命中。
"""

from __future__ import annotations

import re

#: 计划语态的开场白。出现在段首才够强。
_START_MARKERS = (
    "initial response",
)
#: 计划专属短语。还需要英文占比高 + 至少两个角色词，避免正常英文句子误杀。
_STRONG_MARKERS = (
    "finalizing",
    "aligning with the character",
    "the character's response",
)
#: 兼容旧名字；现在的判定只用开场白和强短语，弱短语不再单独触发。
PLANNING_MARKERS = _START_MARKERS + _STRONG_MARKERS

_START_RE = re.compile("|".join(re.escape(m) for m in _START_MARKERS), re.IGNORECASE)
_STRONG_RE = re.compile("|".join(re.escape(m) for m in _STRONG_MARKERS), re.IGNORECASE)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_WORD_RE = re.compile(r"[a-z]+")
#: 至少命中两个「角色扮演」词，避免把 HTTP response / character encoding 这类技术英文误杀。
_ROLE_WORDS = frozenset({"character", "persona", "tone", "brevity", "roleplay", "playful"})
#: 元词 = 角色词 + response/reply；单靠英文不算。
_META_WORDS = _ROLE_WORDS | frozenset({"response", "reply"})
#: 计划标记的最大长度，用于跨 chunk 的“半个标记”缓冲。
_MARKER_MAX = max(len(m) for m in PLANNING_MARKERS)


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
    if _START_RE.match(s):
        return True

    words = _WORD_RE.findall(s.lower())
    meta = len(set(words) & _META_WORDS)
    role = len(set(words) & _ROLE_WORDS)
    if _STRONG_RE.search(s) and english_heavy(s) and role >= 2:
        return True
    return (
        len(words) >= 12
        and english_heavy(s)
        and meta >= 3
        and role >= 2
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


def _partial_marker_suffix_len(text: str) -> int:
    """返回末尾“可能是某个计划标记前半截”的长度，用来跨 chunk 扣住标记。"""
    low = text.lower()
    limit = min(len(low), _MARKER_MAX - 1)
    for n in range(limit, 0, -1):
        suffix = low[-n:]
        if any(marker.startswith(suffix) for marker in PLANNING_MARKERS):
            return n
    return 0


class StreamGuard:
    """流式守卫：先扣住一小段，判定不是计划再放行。

    正常中文回复含汉字、没有计划短语，第一段就放行；计划文本一旦确认，后续整条流都丢掉，
    这样 ``model_pref`` 的救援判据会看到「没产出」，自动换次选。

    已放行之后再出现计划（包括标记跨 chunk）时，只扣住标记前后的那一段并停止后续输出；
    ``aborted`` 表示整条流一个字都没放出去，供调用方决定是否记“整条丢弃”日志。
    """

    def __init__(self, decide_after: int = 80):
        self.decide_after = decide_after
        self.dropped = False
        self.aborted = False
        self._buf = ""
        self._hold = ""
        self._decided = False
        self._drop = False

    def feed(self, text: str) -> str:
        if not text:
            return ""
        if self._decided:
            return self._feed_decided(text)
        self._buf += text
        if not self._ready():
            return ""
        return self._decide()

    def flush(self) -> str:
        if not self._decided:
            return self._decide()
        if self._drop:
            return ""
        return self._release_hold()

    def _ready(self) -> bool:
        b = self._buf
        if is_planning(b):
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
        if self._drop:
            self.aborted = True
        return "" if self._drop else clean

    def _feed_decided(self, text: str) -> str:
        if self._drop:
            return ""
        self._hold += text
        if is_planning(self._hold):
            self.dropped = True
            self._drop = True
            self._hold = ""
            return ""

        keep = _partial_marker_suffix_len(self._hold)
        cut = len(self._hold) - keep
        if cut <= 0 or not self._hold[:cut].strip():
            return ""
        out, self._hold = self._hold[:cut], self._hold[cut:]
        return out

    def _release_hold(self) -> str:
        hold, self._hold = self._hold, ""
        if not hold:
            return ""
        if is_planning(hold):
            self.dropped = True
            self._drop = True
            return ""
        return hold
