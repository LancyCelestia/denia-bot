"""Bounded API reads; optional parser cookies authenticate source reads only."""
import asyncio
import hashlib
import json
import re

import httpx

from .model import BiliError, Item, Page, clean, face_url, identifier
from .auth import cookie_header

SPACE = '/x/polymer/web-dynamic/v1/feed/space'
CARD = '/x/web-interface/card'
SEARCH = '/x/web-interface/search/type'
PATHS = {SPACE, CARD, SEARCH}


def mapping(value):
    if not isinstance(value,dict):
        raise BiliError('REMOTE_SCHEMA')
    return value


def normalize_item(raw, uid, depth=0):
    raw = mapping(raw)
    if depth>1:
        raise BiliError('REMOTE_SCHEMA')
    if raw.get('visible') is not True:
        raise BiliError('PUBLIC_UNPROVEN')
    modules = mapping(raw.get('modules'))
    author = mapping(modules.get('module_author'))
    if isinstance(author.get('mid'),bool) or str(author.get('mid')) != uid:
        raise BiliError('REMOTE_SCHEMA')
    dynamic = mapping(modules.get('module_dynamic'))
    major = mapping(dynamic.get('major') or {})
    if major.get('type') in {'MAJOR_TYPE_BLOCKED','MAJOR_TYPE_NONE'}:
        raise BiliError('PUBLIC_UNPROVEN')
    kind = raw.get('type')
    if not isinstance(kind,str):
        raise BiliError('REMOTE_SCHEMA')
    desc = mapping(dynamic.get('desc') or {}).get('text','')
    text = clean(desc)
    labels = {'DYNAMIC_TYPE_WORD':'文字动态','DYNAMIC_TYPE_DRAW':'图文动态',
        'DYNAMIC_TYPE_AV':'视频动态','DYNAMIC_TYPE_ARTICLE':'专栏动态','DYNAMIC_TYPE_FORWARD':'转发动态'}
    label = labels.get(kind,'未知类型动态')
    major_type = major.get('type')
    if major_type in {'MAJOR_TYPE_ARCHIVE','MAJOR_TYPE_ARTICLE','MAJOR_TYPE_OPUS'}:
        key = {'MAJOR_TYPE_ARCHIVE':'archive','MAJOR_TYPE_ARTICLE':'article','MAJOR_TYPE_OPUS':'opus'}[major_type]
        content = mapping(major.get(key))
        title = clean(content.get('title') or '',200)
        summary = mapping(content.get('summary') or {}).get('text','') if key=='opus' else content.get('desc','')
        extra = '\n'.join(p for p in (title,clean(summary)) if p)
        if extra: text = (text+'\n'+extra).strip()
        if key=='opus': label='图文动态'
    elif major_type == 'MAJOR_TYPE_DRAW':
        images = mapping(major.get('draw')).get('items')
        if not isinstance(images,list) or len(images)>100:
            raise BiliError('REMOTE_SCHEMA')
        text += '\n附 '+str(len(images))+' 张图片（打开原动态查看）'
    if kind == 'DYNAMIC_TYPE_FORWARD':
        original = raw.get('orig')
        if original is None:
            text += '\n原文状态未确认'
        elif mapping(original).get('type') == 'DYNAMIC_TYPE_NONE':
            text += '\n原文不可用'
        else:
            original_uid = str(mapping(mapping(original.get('modules')).get('module_author')).get('mid'))
            identifier(original_uid)
            value = normalize_item(original,original_uid,depth+1)
            text += '\n转发 @'+value.name+'：\n'+value.text
    if label=='未知类型动态':
        text = '未知类型，请打开原动态查看。'
    try:
        ts = author.get('pub_ts')
        if isinstance(ts,bool): raise ValueError
        ts = float(ts)
    except (TypeError,ValueError,OverflowError) as exc:
        raise BiliError('REMOTE_SCHEMA') from exc
    return Item(identifier(raw.get('id_str')),uid,clean(author.get('name'),80),ts,label,clean(text),
                face_url(author.get('face')))


class BiliClient:
    def __init__(self, *, transport=None, cookie_provider=None):
        self.http = httpx.AsyncClient(base_url='https://api.bilibili.com',
            headers={'User-Agent':'denia-bot-bilibili/0.3','Referer':'https://space.bilibili.com/',
                'Accept-Encoding':'identity'},
            timeout=httpx.Timeout(10), limits=httpx.Limits(max_connections=2,max_keepalive_connections=2),
            follow_redirects=False, trust_env=False, transport=transport)
        self.restricted = False
        self.cookie_provider = cookie_provider
        self.authentication_failed = False
        self._auth_identity = None
        self._auth_rejected = None

    def _login_headers(self):
        try:
            header = cookie_header(self.cookie_provider())
        except Exception:
            self.authentication_failed = True
            raise BiliError('REMOTE_AUTH_REQUIRED') from None
        identity = hashlib.sha256(header.encode('ascii')).digest()
        if identity == self._auth_rejected:
            self.authentication_failed = True
            raise BiliError('REMOTE_AUTH_REQUIRED')
        self._auth_identity = identity
        return {'Cookie': header}

    def require_delivery(self):
        # Cookie presence is not a delivery veto. Keep failed/missing login
        # from releasing pending items until a source read succeeds again.
        if self.restricted:
            raise BiliError('REMOTE_RESTRICTED')
        if self.cookie_provider is not None:
            self._login_headers()
            if self.authentication_failed:
                raise BiliError('REMOTE_AUTH_REQUIRED')

    async def __aenter__(self): return self
    async def __aexit__(self,*args): await self.close()
    async def close(self): await self.http.aclose()

    async def _read(self,path,params):
        if path not in PATHS:
            raise BiliError('INVALID_ARGUMENT')
        if self.restricted:
            raise BiliError('REMOTE_RESTRICTED')
        # Search deliberately retains its existing anonymous-public contract;
        # it is never an authentication fallback for UID/feed requests.
        authenticated = self.cookie_provider is not None and path != SEARCH
        self.http.cookies.clear()
        try:
            headers = self._login_headers() if authenticated else {}
            async with self.http.stream('GET',path,params=params,headers=headers) as response:
                if response.status_code in {403,412,429}:
                    self.restricted = True
                    raise BiliError('REMOTE_RESTRICTED')
                if response.status_code == 401 and authenticated:
                    self.authentication_failed = True
                    self._auth_rejected = self._auth_identity
                    raise BiliError('REMOTE_AUTH_REQUIRED')
                if response.status_code != 200:
                    raise BiliError('REMOTE_HTTP')
                if response.headers.get('content-encoding','identity').lower() not in {'','identity'}:
                    raise BiliError('REMOTE_SCHEMA')
                content = bytearray()
                # Identity encoding was checked above. Iterate the raw stream:
                # aiter_bytes() closes at EOF before we can retain refusal state.
                # Only a complete, bounded body may mark either refusal.
                async for part in response.stream:
                    content.extend(part)
                    if len(content)>2*1024*1024:
                        raise BiliError('REMOTE_SCHEMA')
                body = mapping(json.loads(content))
                code = body.get('code')
                if type(code) is not int: raise BiliError('REMOTE_SCHEMA')
                if code in {-352,-412,-509}:
                    self.restricted=True
                if authenticated and code in {-101,-111}:
                    self.authentication_failed = True
                    self._auth_rejected = self._auth_identity
            if code in {-352,-412,-509}:
                raise BiliError('REMOTE_RESTRICTED')
            if code in {-101,-111}:
                raise BiliError('REMOTE_AUTH_REQUIRED')
            if code != 0: raise BiliError('REMOTE_HTTP')
            if authenticated:
                self.authentication_failed = False
            return mapping(body.get('data'))
        except httpx.TimeoutException as exc:
            raise BiliError('DEADLINE') from exc
        except httpx.HTTPError as exc:
            raise BiliError('REMOTE_HTTP') from exc
        except (json.JSONDecodeError,UnicodeError,ValueError,TypeError) as exc:
            raise BiliError('REMOTE_SCHEMA') from exc
        finally:
            # Server Set-Cookie never becomes an alternate credential source,
            # nor leaks into a subsequent anonymous search or another account.
            self.http.cookies.clear()

    async def _get(self,path,params):
        try:
            return await asyncio.wait_for(self._read(path,params),15)
        except asyncio.TimeoutError as exc:
            raise BiliError('DEADLINE') from exc

    async def avatar(self,item):
        """One bounded read from the pinned avatar CDN; no credential, no redirects."""
        try:
            return await asyncio.wait_for(self._avatar(item),5)
        except asyncio.TimeoutError as exc:
            raise BiliError('DEADLINE') from exc

    async def _avatar(self,item):
        url = item.face
        if not url:
            return None
        # The CDN never sees the login cookie; the host allowlist lives in face_url.
        self.http.cookies.clear()
        try:
            async with self.http.stream('GET',url) as response:
                if response.status_code != 200:
                    raise BiliError('REMOTE_HTTP')
                if (response.headers.get('content-encoding','identity').lower() not in {'','identity'}):
                    raise BiliError('REMOTE_SCHEMA')
                kind = (response.headers.get('content-type') or '').split(';')[0].strip().lower()
                if kind not in {'image/jpeg','image/png','image/webp','image/gif'}:
                    raise BiliError('REMOTE_SCHEMA')
                content = bytearray()
                async for part in response.stream:
                    content.extend(part)
                    if len(content) > 2*1024*1024:
                        raise BiliError('REMOTE_SCHEMA')
            return bytes(content) or None
        except httpx.HTTPError as exc:
            raise BiliError('REMOTE_HTTP') from exc
        finally:
            self.http.cookies.clear()

    async def validate_user(self,uid):
        identifier(uid)
        data = await self._get(CARD,{'mid':uid})
        card = mapping(data.get('card'))
        if isinstance(card.get('mid'),bool) or str(card.get('mid'))!=uid:
            raise BiliError('REMOTE_SCHEMA')
        return clean(card.get('name'),80)

    async def _page_data(self,uid,cursor=None):
        identifier(uid)
        if cursor is not None and (not isinstance(cursor,str) or not cursor or len(cursor)>1024):
            raise BiliError('REMOTE_SCHEMA')
        params = {'host_mid':uid,'features':'itemOpusStyle'}
        if cursor is not None: params['offset']=cursor
        data = await self._get(SPACE,params)
        items = data.get('items')
        if not isinstance(items,list) or len(items)>50:
            raise BiliError('REMOTE_SCHEMA')
        more = data.get('has_more')
        if type(more) is not bool:
            raise BiliError('REMOTE_SCHEMA')
        cursor = data.get('offset') if more else None
        if more and (not isinstance(cursor,str) or not cursor or len(cursor)>1024):
            raise BiliError('REMOTE_SCHEMA')
        return items,cursor,more

    async def diagnose_page(self,uid):
        """Exactly one head-page read; only non-content metadata may be reported."""
        items,cursor,more = await self._page_data(uid)
        return {'login_cookie_sent':self.cookie_provider is not None,'item_count':len(items),
            'has_more':more,'public_verified':False,'delivery_allowed':False}

    async def fetch_page(self,uid,cursor=None):
        items,cursor,more = await self._page_data(uid,cursor)
        return Page(tuple(normalize_item(value,uid) for value in items),cursor)

    async def search(self,keyword):
        if not isinstance(keyword,str) or not 1<=len(keyword)<=100:
            raise BiliError('INVALID_ARGUMENT')
        data = await self._get(SEARCH,{'search_type':'video','keyword':keyword,'page':1,'page_size':5})
        values = data.get('result')
        if not isinstance(values,list) or len(values)>100:
            raise BiliError('REMOTE_SCHEMA')
        result = []
        for raw in values[:5]:
            value = mapping(raw)
            bvid = value.get('bvid')
            if not isinstance(bvid,str) or not re.fullmatch(r'BV[0-9A-Za-z]{10}',bvid):
                raise BiliError('REMOTE_SCHEMA')
            # Anonymous video search is public; do not reuse authenticated results.
            if value.get('is_pay') or value.get('is_charge'):
                continue
            result.append(clean(value.get('title'),200)+'\nUP：'+clean(value.get('author'),80)+
                '\n'+clean(value.get('description',''),500)+'\nhttps://www.bilibili.com/video/'+bvid)
        return tuple(result)
