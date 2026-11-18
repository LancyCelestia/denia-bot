#!/usr/bin/env python3
"""EndUID 图卡与共享渲染服务的兼容补丁。

EndUID 的日历模板固定 width: 1000px；项目共享的 Playwright 渲染器
（`docker/playwright-renderer`）在 `/render` 未传 width/height 时默认
900x1200，会对 1000px 的终末地图卡返回 422 "card overflows viewport"。
这里让 EndUID 的远程渲染请求显式带上 1000x2400，保持在渲染器
320–1600 宽、200–2400 高和 20M 像素预算内。

用法（安装脚本在 EndUID clone 后调用）：
    uv run python gsuid/patches/enduid-render-size.py gsuid/core

上游：https://github.com/Loping151/EndUID（GPL-3.0）
被修改的文件：gsuid_core/plugins/EndUID/EndUID/utils/render_utils.py
"""
from __future__ import annotations

import sys
from pathlib import Path

OLD = 'json={"html": html_content},'
NEW = 'json={"html": html_content, "width": 1000, "height": 2400},'


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    target = root / "gsuid_core" / "plugins" / "EndUID" / "EndUID" / "utils" / "render_utils.py"
    if not target.is_file():
        print(f"[enduid-render-size] missing: {target}", file=sys.stderr)
        return 1

    text = target.read_text(encoding="utf-8")
    if NEW in text:
        print("[enduid-render-size] already applied")
        return 0
    if text.count(OLD) != 1:
        print(
            f"[enduid-render-size] unexpected render request pattern in {target}; "
            f"expected one occurrence, found {text.count(OLD)}",
            file=sys.stderr,
        )
        return 1

    backup = target.with_name(target.name + ".bak-render-size")
    if not backup.exists():
        backup.write_text(text, encoding="utf-8")
    target.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    print(f"[enduid-render-size] patched {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
