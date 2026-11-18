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


def render_card(item,*,font_file=DEFAULT_FONT,avatar=None):
    title=ImageFont.truetype(str(font_file),36)
    body=ImageFont.truetype(str(font_file),28)
    small=ImageFont.truetype(str(font_file),22)
    face=avatar_image(avatar)
    size=AVATAR_SIZE if face else 0
    name_x=60+size+(AVATAR_GAP if face else 0)
    names=wrap(clean(item.name,80),title,1000-60-name_x,3)
    lines=wrap(clean(item.text,1600) or '打开原动态查看详情。',body,880,30)
    names_height=len(names)*48
    block_height=max(names_height,size)
    height=min(1800,max(420,276+block_height+len(lines)*42))
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
        draw.text((name_x,y),line,font=title,fill='#182230');y+=48
    y=top+block_height+18
    for line in lines:
        draw.text((60,y),line,font=body,fill='#344054');y+=42
    draw.line((60,height-92,940,height-92),fill='#e4e7ec',width=2)
    draw.text((60,height-73),item.url,font=small,fill='#175cd3')
    output=BytesIO()
    image.save(output,format='PNG',optimize=True)
    return output.getvalue()
