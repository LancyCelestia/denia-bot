"""Existing parser presentation, now HTML rendered by the local Playwright container.

Media downloads, preview selection, cache paths and sending remain independent.
Pillow in card_document is only used to resize/encode already downloaded assets.
"""
from __future__ import annotations
import asyncio
import base64
from pathlib import Path
import uuid
from nonebot import logger
from card_document import document, text, paragraphs, image_uri, chunks
from render_client import render_document_async
from .data import ParseResult
from .perf import mark, phase

STYLE='''
.sheet{padding:28px;background:#fbfaf7;color:#292a2c;font-size:23px}.panel{background:#fff;border-color:#e8e5df}
.muted{color:#6b645a}
.parser-header{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:24px;align-items:start}
.author{grid-column:1;grid-row:1;display:flex;gap:18px;align-items:center;min-width:0}.author-info{min-width:0;flex:1}
.avatar{width:64px;height:64px;border-radius:50%;object-fit:cover;background:#ede9e2;flex-shrink:0}
.author strong{display:block;font-size:26px;font-weight:400}
.platform{grid-column:2;grid-row:1;justify-self:end;display:flex;align-items:center;max-width:180px;font-size:16px;color:#6b645a}
.platform span{min-width:0;overflow-wrap:anywhere}.logo{display:block;height:40px;width:auto;max-width:160px;object-fit:contain;flex-shrink:0}
.grid{display:grid;gap:8px}.grid img{display:block;width:100%;aspect-ratio:1;object-fit:cover;border-radius:12px}
.cover img{display:block;max-height:700px;width:100%;object-fit:contain;border-radius:12px}
.caption{font-size:18px;color:#6b645a;text-align:center}h1{font-size:32px}.more{color:#6b645a;font-size:18px}
/* 转发：每个内容块各自是分页单元，但相邻块之间不留缝，拼成一个连续的引用框；
   嵌套层数用左侧竖条数量表示（box-shadow 画条），不再一层套一层边框。 */
.sheet>.repost{margin-bottom:0;padding-top:8px;padding-bottom:8px;padding-right:20px;background:#f1eee9}
.sheet>.repost.first{padding-top:18px;border-top-left-radius:12px;border-top-right-radius:12px}
.sheet>.repost.last{padding-bottom:18px;margin-bottom:16px;border-bottom-left-radius:12px;border-bottom-right-radius:12px}
.sheet>.repost:last-child{margin-bottom:0}
.repost-label{font-size:18px;color:#6b645a;margin:0 0 10px}
'''

class Renderer:
    DEFAULT_CARD_WIDTH=800
    MAX_IMAGES_DISPLAY=9
    AVATAR_WAIT_TIMEOUT=2.0
    RESOURCES_DIR=Path(__file__).resolve().parent/'resources'

    def __init__(self,config): self.cfg=config

    @classmethod
    def load_resources(cls):
        # Fonts and Emoji are loaded by Chromium inside the renderer container.
        return None

    async def _header(self,result):
        # Embed the original PNG so transparent logos stay sharp and have no white matte.
        try:
            raw=await asyncio.to_thread((self.RESOURCES_DIR/'logos'/f'{result.platform.name}.png').read_bytes)
            logo='data:image/png;base64,'+base64.b64encode(raw).decode('ascii')
        except OSError:
            logo=''
        badge=(f'<img class="logo" src="{logo}" alt="{text(result.platform.display_name)}">'
               if logo else '<span>'+text(result.platform.display_name)+'</span>')
        platform='<p class="platform">'+badge+'</p>'
        if result.author is None: return '<header class="parser-header">'+platform+'</header>'
        avatar_task=result.author.avatar
        available=True
        if isinstance(avatar_task,asyncio.Task) and not avatar_task.done():
            done,_=await asyncio.wait([avatar_task],timeout=self.AVATAR_WAIT_TIMEOUT)
            available=bool(done)
            if not available:
                mark("degraded")
                avatar_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        path=None
        if available:
            try: path=await result.author.get_avatar_path()
            except Exception: mark("degraded")
        uri=await asyncio.to_thread(image_uri,path,width=96,height=96)
        avatar=f'<img class="avatar" src="{uri}">' if uri else '<div class="avatar"></div>'
        return '<header class="parser-header">'+platform+'<div class="author">'+avatar+'<div class="author-info">' \
            +f'<strong class="text">{text(result.author.name)}</strong><p class="muted">{text(result.formatted_datetime() or "")}</p></div></div></header>'

    CONTENT_WIDTH=744   # 800 - 左右各 28px 内边距
    GRID_GAP=8
    MAX_DEPTH=4

    @classmethod
    def _inner_width(cls,depth):
        """该层内容实际可用宽度：每深一层左侧多一条 16px 竖条，引用框右侧再留 20px。"""
        return cls.CONTENT_WIDTH if depth==0 else cls.CONTENT_WIDTH-(16*depth+6)-20

    @staticmethod
    def _repost_style(depth):
        # 竖条依次叠在 x=0,16,32…；先列的 shadow 在上层，只露出 [上一层宽度, 本层宽度) 这一段。
        shadows=[]
        for level in range(depth):
            shadows.append(f'inset {16*level+4}px 0 0 #d7cec3')
            if level<depth-1:
                shadows.append(f'inset {16*level+16}px 0 0 #f1eee9')
        return f'padding-left:{16*depth+6}px;box-shadow:{",".join(shadows)}'

    async def _grid(self,result,depth):
        items=result.img_contents[:self.MAX_IMAGES_DISPLAY]
        with phase("image_preview_wait") as timing:
            paths=await asyncio.gather(*(item.get_preview_path() for item in items),return_exceptions=True)
            if any(path is None or isinstance(path,BaseException) for path in paths):
                timing.status="degraded"
                mark("degraded")
        paths=[path for path in paths if path is not None and not isinstance(path,BaseException)]
        width=self._inner_width(depth)
        if len(paths)==1:
            uris=[await asyncio.to_thread(image_uri,paths[0],width=width,height=700)]
            cols=1
        else:
            cols=2 if len(paths) in (2,4) else 3
            # 按格子实际像素直接居中裁成正方形：不先缩成小图再被 object-fit 放大。
            cell=-(-(width-self.GRID_GAP*(cols-1))//cols)
            uris=await asyncio.gather(*(asyncio.to_thread(image_uri,path,width=cell,height=cell,crop=True) for path in paths))
        uris=[uri for uri in uris if uri]
        blocks=[]
        if len(uris)==1:
            blocks.append(f'<div class="cover"><img src="{uris[0]}"></div>')
        elif uris:
            # 整个九宫格是一个块（最多三行，约 750px），行距和列距一致。
            blocks.append(f'<div class="grid" style="grid-template-columns:repeat({cols},1fr)">'+''.join(f'<img src="{uri}">' for uri in uris)+'</div>')
        if len(result.img_contents)>self.MAX_IMAGES_DISPLAY:
            blocks.append(f'<p class="more">还有 {len(result.img_contents)-self.MAX_IMAGES_DISPLAY} 张图片，见原始媒体。</p>')
        return blocks

    async def _items(self,result,depth=0):
        """(层级, 块 HTML) 列表；层级 0 是原帖，1 起是逐层转发。"""
        if depth>self.MAX_DEPTH: return [(depth-1,'<p class="more">更早的转发请查看原帖。</p>')]
        with phase("avatar_header"):
            header=await self._header(result)
        blocks=[header]
        if result.title:
            blocks.extend(paragraphs(result.title,chars=300,class_name='title text'))
        cover=None
        try:
            with phase("cover_wait"):
                cover=await result.cover_path
        except Exception: mark("degraded")
        uri=await asyncio.to_thread(image_uri,cover,width=self._inner_width(depth),height=700)
        if uri:
            blocks.append(f'<div class="cover"><img src="{uri}"></div>')
        elif result.img_contents:
            blocks.extend(await self._grid(result,depth))
        elif result.graphics_contents:
            for item in result.graphics_contents:
                blocks.extend(paragraphs(item.text,chars=550,class_name='text'))
                try:
                    with phase("graphics_wait"):
                        path=await item.get_path()
                except Exception:
                    mark("degraded")
                    path=None
                uri=await asyncio.to_thread(image_uri,path,width=self._inner_width(depth),height=700)
                if uri: blocks.append(f'<div class="cover"><img src="{uri}"></div>')
                if item.alt: blocks.extend(paragraphs(item.alt,chars=450,class_name='caption text'))
        blocks.extend(paragraphs(result.text,chars=550,class_name='text'))
        blocks.extend(paragraphs(result.extra_info,chars=550,class_name='muted text'))
        items=[(depth,block) for block in blocks]
        if depth>0:
            items[0]=(depth,'<p class="repost-label">转发内容</p>'+items[0][1])
        if result.repost:
            items.extend(await self._items(result.repost,depth+1))
        return items

    async def _blocks(self,result,depth=0):
        items=await self._items(result,depth)
        blocks=[]
        for index,(level,block) in enumerate(items):
            if level==0:
                blocks.append(block); continue
            # 连续的转发块拼成一个引用框：首块圆上角，末块圆下角。
            first=index==0 or items[index-1][0]==0
            last=index==len(items)-1 or items[index+1][0]==0
            classes='repost'+(' first' if first else '')+(' last' if last else '')
            blocks.append(f'<section class="{classes}" style="{self._repost_style(level)}">{block}</section>')
        return blocks

    @staticmethod
    def page_label(result):
        """续页顶部的「平台 · 作者」，让第 2 页起也能看出是哪条内容。"""
        parts=[result.platform.display_name]
        if result.author is not None and result.author.name:
            parts.append(str(result.author.name)[:40])
        return ' · '.join(part for part in parts if part)

    async def build_html(self,result: ParseResult):
        return document(await self._blocks(result),width=self.DEFAULT_CARD_WIDTH,
            css=STYLE+'.title{font-size:32px;font-weight:600;line-height:1.35}',
            page_label=self.page_label(result))

    async def render_cards(self,result: ParseResult) -> list[Path]:
        try:
            with phase("html_assets"):
                html=await self.build_html(result)
            with phase("renderer"):
                pages=await render_document_async(html,width=self.DEFAULT_CARD_WIDTH,source='parser')
            self.cfg.cache_dir.mkdir(parents=True,exist_ok=True)
            stem=f'card_{uuid.uuid4().hex}'
            paths=[]
            for index,png in enumerate(pages):
                path=self.cfg.cache_dir/f'{stem}_{index+1}.png'
                await asyncio.to_thread(path.write_bytes,png); paths.append(path)
            return paths
        except Exception as error:
            logger.warning(f'[parser] 预览卡渲染失败（{type(error).__name__}），保留文字与原始媒体')
            return []

    async def render_card(self,result: ParseResult) -> Path | None:
        paths=await self.render_cards(result)
        return paths[0] if len(paths)==1 else None
