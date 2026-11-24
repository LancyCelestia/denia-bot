"""Bulk-import local sticker folders with deterministic, editable metadata.

The importer deliberately owns one fixed directory (`nonebot/data/stickers`).
It never accepts an arbitrary filesystem path from the dashboard.
"""
from __future__ import annotations

import colorsys
import hashlib
import io
from pathlib import Path

from PIL import Image, ImageStat

import meme_logic

ROOT = Path(__file__).resolve().parent
DEFAULT_IMPORT_ROOT = ROOT / 'data' / 'stickers'
DATA_ROOT = ROOT / 'data'
MAX_IMAGES = 2000
MAX_CATEGORY_CHARS = 40
_IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}

_ROLE_TAGS = (
    ('守岸人', '角色:守岸人'),
    ('达妮娅', '角色:达妮娅'),
)
_SCENE_TAGS = (
    ('新年', '场景:新年'),
    ('演唱会', '场景:演唱会'),
    ('联动', '场景:联动'),
    ('Q版', '风格:Q版'),
    ('q版', '风格:Q版'),
    ('Pixiv', '来源:Pixiv'),
    ('pixiv', '来源:Pixiv'),
    ('小红书', '来源:小红书'),
)
_EMOTION_HINTS = (
    (('开心', '笑', '乐', '庆祝', '好耶'), '情绪:开心'),
    (('哭', '泪', '难过', '悲'), '情绪:难过'),
    (('生气', '怒', '恼'), '情绪:生气'),
    (('惊', '吓', '震惊'), '情绪:惊讶'),
    (('害羞', '脸红'), '情绪:害羞'),
    (('困', '睡', '倦'), '情绪:困倦'),
    (('问', '?', '？'), '情绪:疑惑'),
    (('爱', '心', '喜欢'), '情绪:喜欢'),
    (('无语', '汗'), '情绪:无语'),
    (('得意', '酷', '帅'), '情绪:得意'),
)


def _palette_emotion(content: bytes) -> str:
    """A coarse, local palette hint; the user can always edit the tag."""
    try:
        with Image.open(io.BytesIO(content)) as opened:
            image = opened.convert('RGB').resize((32, 32))
        red, green, blue = (value / 255 for value in ImageStat.Stat(image).mean)
        hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
        if value < 0.24:
            return '情绪:困倦'
        if saturation < 0.12:
            return '情绪:日常'
        if hue < 0.045 or hue >= 0.95:
            return '情绪:喜欢' if value > 0.62 else '情绪:生气'
        if hue < 0.16:
            return '情绪:开心'
        if hue < 0.42:
            return '情绪:日常'
        if hue < 0.58:
            return '情绪:难过'
        if hue < 0.72:
            return '情绪:害羞'
        if hue < 0.88:
            return '情绪:惊讶'
        return '情绪:喜欢'
    except (OSError, ValueError):
        return '情绪:日常'


def auto_tags(folder: str, filename: str = '', content: bytes | None = None) -> str:
    """Return a small set of deterministic tags for one local image."""
    text = f'{folder} {filename}'
    tags: list[str] = []
    for needle, tag in _ROLE_TAGS + _SCENE_TAGS:
        if needle in text and tag not in tags:
            tags.append(tag)
    emotion = next((tag for needles, tag in _EMOTION_HINTS if any(n in text for n in needles)), '')
    if not emotion and content:
        emotion = _palette_emotion(content)
    tags.append(emotion or '情绪:日常')
    return ', '.join(dict.fromkeys(tags))


def auto_weight(folder: str, filename: str = '') -> float:
    text = f'{folder} {filename}'
    if 'Q版' in text or 'q版' in text or '日常' in text:
        return 1.1
    if '新年' in text or '联动' in text:
        return 1.05
    if '演唱会' in text:
        return 0.95
    return 1.0


def _category_name(folder: str) -> str:
    name = ''.join(ch for ch in str(folder) if ch.isprintable() and ch not in '/\\\r\n').strip()
    return (name or '未分类')[:MAX_CATEGORY_CHARS]


def _category_description(folder: str) -> str:
    return f'本机导入 · {str(folder).strip()[:280]}'


def _safe_source(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()[:500]
    except ValueError:
        return path.name[:500]


def auto_tag_library(library, category_id: str | None = None) -> dict:
    """Recompute tags and the folder-level weight for existing images."""
    categories = library.categories()
    if category_id:
        categories = [category for category in categories if category['id'] == category_id]
    updated = 0
    for category in categories:
        for image in library.images(category['id']):
            try:
                content, _ = library.image_info(image['id'])
                filename = Path(str(image.get('source') or '')).name
                tags = auto_tags(category['name'], filename, content)
                weight = auto_weight(category['name'], filename)
                library.set_metadata(image['id'], tags=tags, weight=weight)
                updated += 1
            except (OSError, ValueError):
                continue
    return {'updated': updated, 'categories': len(categories)}


def import_tree(library=None, root=None) -> dict:
    """Import every supported image under the fixed sticker directory.

    Re-running is safe: the image digest is stored and duplicates are skipped.
    Categories are created from the immediate folder names.
    """
    library = library or meme_logic.Library()
    root = Path(root) if root is not None else DEFAULT_IMPORT_ROOT
    try:
        resolved = root.resolve()
        resolved.relative_to(DATA_ROOT.resolve())
    except (OSError, ValueError) as exc:
        raise ValueError('只能导入 data 目录下的表情包') from exc
    if not resolved.is_dir():
        raise ValueError('本机表情包目录不存在')

    existing = {category['name']: category for category in library.categories()}
    imported = skipped = 0
    errors: list[str] = []
    categories = 0
    for folder in sorted(resolved.iterdir(), key=lambda item: item.name.lower()):
        if not folder.is_dir() or folder.name.startswith('.'):
            continue
        files = sorted(
            (path for path in folder.rglob('*') if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES),
            key=lambda item: item.as_posix().lower(),
        )
        if not files:
            continue
        category_name = _category_name(folder.name)
        category = existing.get(category_name)
        if category is None:
            try:
                category_id = library.put_category(category_name, _category_description(folder.name))
                category = {'id': category_id, 'name': category_name}
                existing[category_name] = category
                categories += 1
            except ValueError as exc:
                errors.append(f'{folder.name}: {exc}')
                continue
        for path in files:
            if imported + skipped >= MAX_IMAGES:
                errors.append(f'达到单次导入上限 {MAX_IMAGES} 张')
                break
            try:
                content = path.read_bytes()
                if len(content) > meme_logic.MAX_IMAGE_BYTES:
                    raise ValueError('超过 5 MB')
                digest = hashlib.sha256(content).hexdigest()
                if library.find_digest(digest):
                    skipped += 1
                    continue
                library.add_image(
                    category['id'], content,
                    tags=auto_tags(folder.name, path.name, content),
                    weight=auto_weight(folder.name, path.name),
                    digest=digest,
                    source=_safe_source(path),
                )
                imported += 1
            except (OSError, ValueError) as exc:
                errors.append(f'{path.name}: {exc}'[:240])
    return {
        'imported': imported,
        'skipped': skipped,
        'categories': categories,
        'errors': errors[:20],
    }
