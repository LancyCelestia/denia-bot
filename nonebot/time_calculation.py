"""Conservative arithmetic for explicit relative-time questions."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

_NUM = r'(?:\d{1,6}|[零〇一二两三四五六七八九十百千]{1,8}|半)'
_UNIT = r'(?:小时|钟头|分钟|秒钟|天|日|时|分|秒)'
_PART = rf'{_NUM}(?:个)?{_UNIT}'
_DURATION = rf'{_PART}(?:(?:零|又|再|和|加上)?{_PART})*'
_BOUNDARY = r'(?<![\d零〇一二两三四五六七八九十百千半个])'
_RELATIVE = re.compile(rf'{_BOUNDARY}(?P<duration>{_DURATION})\s*(?P<direction>之后|以后|之前|以前|过后|后|前)')
_OPERATION = re.compile(rf'(?P<operation>加上|加|减去|减|往前推|向前推|往后推|向后推|再过)\s*(?P<duration>{_DURATION})')
_PIECE = re.compile(rf'(?P<number>{_NUM})(?:个)?(?P<unit>{_UNIT})')
_ABSOLUTE = re.compile(r'昨天|前天|明天|后天|今天|今晚|明早|昨晚|上周|下周|星期|礼拜|周[一二三四五六日天]|\d{1,2}[:：]\d{2}|\d{1,2}点|[一二三四五六七八九十两]{1,3}点|\d{1,2}月\d{1,2}[日号]')
_QUESTION = re.compile(r'[?？]|什么|啥时候|啥时|几[点时号日]|哪[天日]|哪一|什么时候|什么时间|什么日子|现在的')
_UNSUPPORTED = re.compile(rf'{_NUM}(?:个|整)?(?:周|星期|月|年|刻钟|刻)|{_NUM}整(?:天|日|小时)|\d+[.．]\d+')
_ANAPHORA = re.compile(r'那|这|然后|接着|从|开始|起')
_NOW = re.compile(r'现在|此刻|当前|此时|这时')
_SAFE_PREFIX = re.compile(r'(?:\s|[，,。！!？?~～…]|达妮娅|小娅|娅娅|娅|诶呀|哎呀|唔|请问|请|帮我|帮忙|算一算|算算|算下|问一下|告诉我|我想知道|如果|假如|那|从|以|是|的|时间|开始|起|算|往|未来|这|差不多|大概|大约|现在|此刻|当前|此时|这时)*')
_DIRECT_QUESTION = re.compile(r'\s*(?:大约|大概|大致|将|会|应该|那时|是|算是|到)*\s*(?:什么时候|什么时间|什么日子|什么日期|啥时候|啥时|几点(?:钟)?|几时|几号|几月几号|几日|哪天|哪一天|哪日|[?？])(?:了|呢|啦|啊|呀|吖|吗|哦|嘛|的|[?？！!。,.，\s])*$')
_DIGITS = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
_UNITS = {'十': 10, '百': 100, '千': 1000}
_SECONDS = {'天': 86400, '日': 86400, '小时': 3600, '钟头': 3600, '时': 3600,
            '分钟': 60, '分': 60, '秒钟': 1, '秒': 1}


def _number(value):
    if value == '半':
        return 0.5
    if value.isascii() and value.isdigit():
        return int(value)
    if all(c in _DIGITS for c in value):
        return int(''.join(str(_DIGITS[c]) for c in value))
    total, digit, last_unit = 0, None, 10000
    for char in value:
        if char in _DIGITS:
            if digit is not None and digit != 0:
                raise ValueError('invalid Chinese number')
            digit = _DIGITS[char]
        elif char in _UNITS:
            unit = _UNITS[char]
            if unit >= last_unit:
                raise ValueError('invalid Chinese number')
            total += (digit if digit is not None else 1) * unit
            digit, last_unit = None, unit
        else:
            raise ValueError('invalid Chinese number')
    if digit and last_unit >= 100 and len(value) > 1 and value[-2] not in '零〇':
        raise ValueError('ambiguous abbreviated Chinese number')
    return total + (digit or 0)


def calculate(text: str, now: datetime):
    if not isinstance(text, str) or not text or len(text) > 1000 or not _QUESTION.search(text) or _ABSOLUTE.search(text) or _UNSUPPORTED.search(text):
        return None
    matches = list(_RELATIVE.finditer(text))
    if len(matches) > 1:
        return None
    match = matches[0] if matches else _OPERATION.search(text)
    if not match:
        return None
    prefix, suffix = text[:match.start()], text[match.end():]
    if not _SAFE_PREFIX.fullmatch(prefix) or (_ANAPHORA.search(prefix) and not _NOW.search(prefix)):
        return None
    if not _DIRECT_QUESTION.fullmatch(suffix):
        if not (_NOW.search(prefix) and re.fullmatch(r'[呢啦呀啊吖哦嘛\s。！？!?]*', suffix)):
            return None
    duration = match.group('duration')
    start, end = match.span('duration')
    # Refuse mixed or incomplete expressions rather than silently dropping a unit.
    if _PIECE.search(text[:start]) or _PIECE.search(text[end:]):
        return None
    if start and text[start - 1] in '0123456789零〇一二两三四五六七八九十百千半个':
        return None
    seconds = 0.0
    try:
        for part in _PIECE.finditer(duration):
            seconds += _number(part.group('number')) * _SECONDS[part.group('unit')]
        if not 0 < seconds <= 36600 * 86400:
            return None
        direction = match.groupdict().get('direction') or ''
        operation = match.groupdict().get('operation') or ''
        negative = direction.endswith('前') or operation in ('减去', '减', '往前推', '向前推')
        return now + timedelta(seconds=-seconds if negative else seconds)
    except (ValueError, OverflowError):
        return None


def render(result: datetime, now: datetime):
    days = (result.date() - now.date()).days
    relative = {-2: '前天', -1: '昨天', 0: '今天', 1: '明天', 2: '后天', 3: '大后天'}.get(days)
    weekday = '一二三四五六日'[result.weekday()]
    detail = f'{result.year}年{result.month}月{result.day}日（周{weekday}'
    if relative:
        detail += f'，{relative}'
    return detail + f'）{result:%H:%M:%S}'
