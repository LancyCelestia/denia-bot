"""Render the redesigned simple cards from fixed sample data, so you can check them by eye.

Usage (run from the nonebot directory, renderer reachable):
    $env:QQBOT_RENDER_URL = 'http://127.0.0.1:3100'     # PowerShell
    python ..\\tools\\diagnostics\\preview_simple_cards.py
    python ..\\tools\\diagnostics\\preview_simple_cards.py --real-covers   # download real Epic covers

Output goes to nonebot/.tmp/card-preview/. Nothing is sent to QQ and no bot data is touched.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

NONEBOT = Path(__file__).resolve().parents[2] / 'nonebot'
sys.path.insert(0, str(NONEBOT))

import epic_free_image  # noqa: E402
import epic_free_logic as logic  # noqa: E402
import epic_free_service as service  # noqa: E402
import group_topics_image  # noqa: E402
import weather_image  # noqa: E402

OUT = NONEBOT / '.tmp' / 'card-preview'
NOW = datetime(2026, 10, 31, 3, 14, tzinfo=timezone.utc)

WEATHER_NOW = {'kind': 'now', 'city': '杭州', 'place': '浙江省 杭州市 西湖区', 'queried_at': '11月22日 周日 11:06',
               'condition': '多云', 'temperature': 12, 'feels_like': 10,
               'facts': [('湿度', '68%'), ('风', '东北风 2 级'), ('能见度', '12 km')]}
WEATHER_EMPTY = {'kind': 'now', 'city': '杭州', 'place': '浙江省 杭州市 西湖区', 'queried_at': '11月22日 周日 11:06',
                 'condition': '雷阵雨伴有冰雹', 'temperature': None, 'feels_like': None, 'facts': []}
FORECAST = {'kind': 'forecast', 'city': '杭州', 'place': '浙江省 杭州市 西湖区', 'queried_at': '11月22日 周日 11:06', 'days': [
    {'date': '2026-11-22', 'label': '今天', 'short': '11/22', 'condition': '多云', 'min': 9, 'max': 16},
    {'date': '2026-11-23', 'label': '明天', 'short': '11/23', 'condition': '小雨转中雨', 'min': 8, 'max': 13},
    {'date': '2026-11-24', 'label': '后天', 'short': '11/24', 'condition': '晴', 'min': 4, 'max': 11}]}
TOPICS = [('原神', 126), ('期末', 98), ('火锅', 73), ('周末', 61), ('显卡', 54),
          ('抽卡', 47), ('考研', 39), ('一个特别长的话题词用来测省略', 33), ('下雨', 28), ('外卖', 21)]


def placeholder(seed: int) -> bytes:
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (1280, 720), (60 + seed * 37 % 140, 80, 130))
    draw = ImageDraw.Draw(image)
    for x in range(0, 1700, 90):
        draw.line((x, 0, x - 420, 720), fill=(130, 150 + seed * 23 % 90, 190), width=28)
    out = BytesIO()
    image.save(out, 'JPEG', quality=85)
    return out.getvalue()


def save(name: str, pages: list[bytes]) -> None:
    for index, png in enumerate(pages, 1):
        path = OUT / f'{name}-{index}.png'
        path.write_bytes(png)
        print(f'{path}  ({len(png):,} bytes)')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--real-covers', action='store_true', help='从 Epic CDN 下载测试样例里的真实封面')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    elements = json.loads((NONEBOT / 'tests/fixtures/epic_free_cn.json').read_text('utf-8'))
    view = logic.build_view(elements['data']['Catalog']['searchStore']['elements'], NOW)
    games = view['free'] + view['upcoming']
    if args.real_covers:
        covers = asyncio.run(service.fetch_covers(games))
        print(f'封面下载成功 {len(covers)}/{len([g for g in games if g.get("cover_url")])}')
    else:
        covers = {game['cover_url']: placeholder(i) for i, game in enumerate(games) if game.get('cover_url')}

    jobs = [
        ('weather-now', lambda: weather_image.render_pages(WEATHER_NOW)),
        ('weather-now-missing', lambda: weather_image.render_pages(WEATHER_EMPTY)),
        ('weather-forecast', lambda: weather_image.render_pages(FORECAST)),
        ('group-topics', lambda: group_topics_image.render_pages('123456789', TOPICS)),
        ('epic', lambda: epic_free_image.render_pages(view['free'], view['upcoming'], covers=covers, now=NOW)),
        ('epic-no-cover', lambda: epic_free_image.render_pages(view['free'], view['upcoming'], now=NOW)),
        ('epic-empty', lambda: epic_free_image.render_pages([], [], now=NOW)),
    ]
    failed = 0
    for name, job in jobs:
        try:
            save(name, job())
        except Exception as error:  # 逐张报错，不让一张失败挡住其他
            failed += 1
            print(f'[失败] {name}: {type(error).__name__}: {error}')
    print(f'完成：{len(jobs) - failed}/{len(jobs)} 张，输出目录 {OUT}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
