"""Lazy reuse of the existing parser login, never a second credential store."""
from .model import BiliError


COOKIE_NAMES = ('SESSDATA', 'bili_jct', 'DedeUserID')


def cookie_header(snapshot):
    """Limit names and cookie-octets before constructing a request header."""
    if not isinstance(snapshot, dict) or not snapshot.get('SESSDATA'):
        raise BiliError('REMOTE_AUTH_REQUIRED')
    fields = []
    for name in COOKIE_NAMES:
        value = snapshot.get(name)
        if value is None or value == '':
            continue
        if (not isinstance(value, str) or len(value) > 4096 or
                any(not 0x21 <= ord(c) <= 0x7e or c in ';,"\\' for c in value)):
            raise BiliError('REMOTE_AUTH_REQUIRED')
        if name == 'DedeUserID' and (not value.isascii() or not value.isdigit() or len(value) > 20):
            raise BiliError('REMOTE_AUTH_REQUIRED')
        fields.append(name + '=' + value)
    header = '; '.join(fields)
    if len(header) > 8192:
        raise BiliError('REMOTE_AUTH_REQUIRED')
    return header


def parser_login_cookies():
    # Resolve on every read, so startup order and a later .blogin work without
    # importing/initializing the parser (or changing its global HTTP backend).
    from nonebot import get_plugin
    try:
        plugin = get_plugin('media_parser')
        runtime = plugin.module.runtime if plugin is not None else None
        parser = runtime.bilibili() if runtime is not None else None
        if parser is None:
            raise BiliError('REMOTE_AUTH_REQUIRED')
        return parser.login.subscription_cookies()
    except Exception:
        raise BiliError('REMOTE_AUTH_REQUIRED') from None
