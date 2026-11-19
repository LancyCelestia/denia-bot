"""Raster card. Text, fonts and layout are local; the only remote artifact is
avatar bytes the caller already fetched, never a URL read in here."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .model import clean

DEFAULT_FONT=Path(__file__).resolve().parents[1]/'media_parser/core/resources/HYSongYunLangHeiW-1.ttf'
AVATAR_SIZE=96
AVATAR_GAP=20
AVATAR_PIXELS=4_000_000
COVER_WIDTH=880
COVER_HEIGHT=520
COVER_PIXELS=24_000_000
MAX_HEIGHT=2600


def wrap(text,font,width,max_lines):
    lines=[]
    line=''
    for char in text:
        if char=='\n':
            lines.append(line);line=''
        elif font.getlength(line+char)>width:
            lines.append(line);line=char
        else:
            line+=char
        if len(lines)>=max_lines:
            lines[-1]='…正文较长，请打开原动态查看'
            return lines
    if line or not lines:lines.append(line)
    return lines


def avatar_image(data,size=AVATAR_SIZE):
    """Circular avatar thumbnail from bounded bytes; None when anything is off."""
    if not data:
        return None
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width*source.height > AVATAR_PIXELS:
                return None
            thumb=ImageOps.fit(source.convert('RGB'),(size,size),method=Image.LANCZOS)
    except Exception:
        return None
    mask=Image.new('L',(size,size),0)
    ImageDraw.Draw(mask).ellipse((0,0,size-1,size-1),fill=255)
    return thumb,mask


def cover_image(data,width=COVER_WIDTH,height=COVER_HEIGHT):
    """Decode a bounded cover and preserve its aspect ratio inside the card."""
    if not data:
        return None
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width*source.height > COVER_PIXELS:
                return None
            source.seek(0)
            cover=source.convert('RGB')
        return ImageOps.contain(cover,(width,height),method=Image.LANCZOS)
    except Exception:
        return None


def render_card(item,*,font_file=DEFAULT_FONT,avatar=None,cover=None):
    name_font=ImageFont.truetype(str(font_file),36)
    heading=ImageFont.truetype(str(font_file),32)
    body=ImageFont.truetype(str(font_file),28)
    small=ImageFont.truetype(str(font_file),22)
    face=avatar_image(avatar)
    picture=cover_image(cover)
    size=AVATAR_SIZE if face else 0
    name_x=60+size+(AVATAR_GAP if face else 0)
    names=wrap(clean(item.name,80),name_font,1000-60-name_x,3)
    video_title=clean(getattr(item,'title',''),200)
    body_text=clean(item.text,1600)
    before_text=body_text
    after_text=''
    if video_title:
        marker=body_text.find(video_title)
        if marker<0:
            before_text=''
            after_text=body_text
        else:
            before_text=body_text[:marker].rstrip()
            after_text=body_text[marker+len(video_title):].lstrip('\n')
    before=wrap(before_text,body,880,30) if before_text else []
    title_lines=wrap(video_title,heading,880,3) if video_title else []
    after=wrap(after_text,body,880,max(0,30-len(before))) if after_text else []
    if not before and not title_lines and not after:
        before=wrap('打开原动态查看详情。',body,880,1)
    names_height=len(names)*48
    block_height=max(names_height,size)
    content_height=len(before)*42
    if title_lines:content_height+=8+len(title_lines)*42
    if picture:content_height+=14+picture.height+14
    content_height+=len(after)*42
    height=min(MAX_HEIGHT,max(420,276+block_height+content_height))
    image=Image.new('RGB',(1000,height),'#f5f7fc')
    draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((28,24,972,height-24),radius=28,fill='white')
    draw.rounded_rectangle((55,52,62,116),radius=3,fill='#fb7299')
    draw.text((82,50),'BILIBILI  ·  '+item.kind,font=small,fill='#b13567')
    stamp=datetime.fromtimestamp(item.ts,timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M  UTC+8')
    draw.text((82,88),stamp,font=small,fill='#667085')
    top=132 if face else 138
    if face:
        image.paste(face[0],(60,top),face[1])
    y=top+max(0,(size-names_height)//2)
    for line in names:
        draw.text((name_x,y),line,font=name_font,fill='#182230');y+=48
    y=top+block_height+18
    for line in before:
        draw.text((60,y),line,font=body,fill='#344054');y+=42
    if title_lines:
        y+=8
        for line in title_lines:
            draw.text((60,y),line,font=heading,fill='#182230');y+=42
    if picture:
        y+=14
        image.paste(picture,(60+(880-picture.width)//2,y))
        y+=picture.height+14
    for line in after:
        draw.text((60,y),line,font=body,fill='#344054');y+=42
    draw.line((60,height-92,940,height-92),fill='#e4e7ec',width=2)
    draw.text((60,height-73),item.url,font=small,fill='#175cd3')
    output=BytesIO()
    image.save(output,format='PNG',optimize=True)
    return output.getvalue()
