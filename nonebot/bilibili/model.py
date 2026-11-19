from dataclasses import dataclass
import html
import math
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit


class BiliError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def identifier(value):
    if not isinstance(value, str) or re.fullmatch(r'[1-9][0-9]{0,19}', value) is None:
        raise BiliError('INVALID_ARGUMENT')
    return value


def clean(value, limit=1600):
    if not isinstance(value, str):
        raise BiliError('REMOTE_SCHEMA')
    value = html.unescape(re.sub(r'<[^>]*>', '', value))
    return ''.join(c for c in value if c == '\n' or not unicodedata.category(c).startswith('C'))[:limit]


def face_url(value):
    """Only pinned avatar hosts become a fetchable URL; anything else means no avatar."""
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if not value or len(value) > 512:
        return ''
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
    except ValueError:
        return ''
    if (parsed.scheme != 'https' or not host.endswith('.hdslb.com')
            or parsed.username or parsed.password or parsed.port is not None
            or parsed.query or parsed.fragment):
        return ''
    return value


def image_url(value):
    """Pinned Bilibili image CDN only; normalize legacy HTTP covers to HTTPS."""
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if not value or len(value) > 1024:
        return ''
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
    except ValueError:
        return ''
    if (parsed.scheme not in {'http', 'https'} or not host.endswith('.hdslb.com')
            or parsed.username or parsed.password or parsed.port is not None
            or parsed.fragment):
        return ''
    return urlunsplit(('https', host, parsed.path, parsed.query, ''))


@dataclass(frozen=True)
class Scope:
    bot: str
    kind: str
    target: str

    def __post_init__(self):
        identifier(self.bot)
        identifier(self.target)
        if self.kind not in {'group', 'private'}:
            raise BiliError('INVALID_ARGUMENT')

    @property
    def key(self):
        return self.bot, self.kind, self.target


@dataclass(frozen=True)
class Item:
    id: str
    uid: str
    name: str
    ts: float
    kind: str
    text: str
    face: str = ''
    title: str = ''
    cover: str = ''

    def __post_init__(self):
        identifier(self.id)
        identifier(self.uid)
        if isinstance(self.ts, bool) or not isinstance(self.ts, (int, float)) or not math.isfinite(self.ts) or self.ts <= 0:
            raise BiliError('REMOTE_SCHEMA')
        for value, limit in ((self.name,80),(self.kind,40),(self.text,1600),(self.title,200)):
            if not isinstance(value,str) or len(value)>limit:
                raise BiliError('REMOTE_SCHEMA')
        # A malformed avatar or cover URL must never block the text card or the delivery.
        object.__setattr__(self, 'face', face_url(self.face))
        object.__setattr__(self, 'cover', image_url(self.cover))

    @property
    def url(self):
        return 'https://t.bilibili.com/' + self.id

    def plain(self):
        return self.name + ' 发布了' + self.kind + '\n' + self.text + '\n' + self.url


@dataclass(frozen=True)
class Page:
    items: tuple[Item, ...]
    cursor: str | None


@dataclass(frozen=True)
class Receipt:
    kind: str
    message_id: str | None = None
    note: str = ''


HEADS = {'b站订阅':'subscribe','b站退订':'unsubscribe','b站订阅列表':'list','b站搜索':'search','b站选':'select'}
HELP = [('b站订阅 <UID>','仅主人；本会话需在接收目标里，首次不推历史'),
        ('b站退订 <UID>','仅主人；删除当前会话订阅和待发动态'),
        ('b站订阅列表','查看当前会话订阅与检查/发送状态'),
        ('b站搜索 <关键词>','最多5条，120秒内仅本人可选；不下载'),
        ('b站选 <序号>','查看本会话本人的搜索详情与观看链接')]


def parse(text):
    if not isinstance(text,str) or not text.startswith('.'):
        return None
    parts = text[1:].split(maxsplit=1)
    if not parts or parts[0] not in HEADS:
        return None
    action = HEADS[parts[0]]
    arg = parts[1].strip() if len(parts)>1 else ''
    valid = (bool(re.fullmatch(r'[1-9][0-9]{0,19}',arg)) if action in {'subscribe','unsubscribe'} else
             1<=len(arg)<=100 if action=='search' else
             bool(re.fullmatch(r'[1-5]',arg)) if action=='select' else not arg)
    return action, arg, valid


ERRORS = {
    'INVALID_ARGUMENT':'参数无效，请查看 .help 中的完整 B站命令。',
    'NOT_OWNER':'仅机器人主人可增删当前会话订阅。',
    'DISABLED':'B站动态订阅尚未启用。',
    'TARGET_DISABLED':'当前 bot/会话未在接收目标配置中。',
    'BUSY':'正在检查或处理其它请求，请稍后再试。',
    'REMOTE_RESTRICTED':'B站访问受限，来源已暂停；请主人检查后按文档恢复。',
    'REMOTE_AUTH_REQUIRED':'B站登录态缺失、无效或要求验证；请主人检查解析插件的 .blogin 登录，订阅不会自动回退匿名。',
    'REMOTE_HTTP':'B站请求失败，请稍后重试。',
    'REMOTE_SCHEMA':'B站返回格式异常，请主人检查。',
    'PUBLIC_UNPROVEN':'动态公开性未确认，本轮不推送。',
    'DEADLINE':'请求超时，请稍后再试。',
    'STORAGE_FAULT':'本地存储异常，已停止新投递；请主人检查，勿删除去重库。',
    'CAPACITY':'订阅或待发容量已满，未推进检查位置；请主人检查。',
    'SCAN_GAP':'动态检查覆盖不完整，可能遗漏，请主人检查。',
    'EXPIRED':'没有本会话本人的有效结果，请重新搜索。',
}


def error_text(code):
    return ERRORS.get(code, '操作失败，请主人查看插件日志。')
