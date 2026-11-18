#!/usr/bin/env python3
"""让 gsuid_core 插件显示的命令文本带上实际命令前缀。

本项目的 core 配置为 command_start=["."]，而 NoneBot 侧的 GenshinUID
适配器会把原始文本原样转给 core；因此用户实际需要发送 `.ww登录`、`.end登录`
这样的命令。插件里的 `PREFIX` 来自 `get_plugin_available_prefix()`，该函数
只返回 `ww` / `end`，导致帮助、登录提示、卡片按钮等显示成 `ww登录`、
`end登录`，与实际输入不一致。

本补丁只修改 `gsuid_core/sv.py` 的显示辅助函数：当 core 的 command_start
包含 "." 且前缀非空时，返回 ".ww" / ".end" / ".core"；它不修改 force_prefix、
SV 匹配或 Trigger 注册，因此命令匹配仍由 core 在剥掉 "." 后用 `ww登录`
等文本完成。另修正几处无法用 PREFIX 模板覆盖的硬编码命令示例：
XWUID 的 `change_help.json` / `config_default.py` 和 EndUID 的 `ann_card.py`。

用法（安装脚本在 clone 后调用）：
    uv run python gsuid/patches/command-prefix-display.py gsuid/core

上游：https://github.com/Genshin-bots/gsuid_core（GPL-3.0）
      https://github.com/Loping151/XutheringWavesUID（GPL-3.0）
      https://github.com/Loping151/EndUID（GPL-3.0）
被修改的文件：gsuid_core/sv.py、XWUID change_help.json/config_default.py、
EndUID ann_card.py
"""
from __future__ import annotations

import sys
from pathlib import Path

OLD = '''def get_plugin_available_prefix(plugin_name: str) -> str:
    plugin = SL.plugins.get(plugin_name)
    if plugin is None:
        raise ValueError(t("插件{plugin_name}不存在!", plugin_name=plugin_name))
    if not plugin.disable_force_prefix and plugin.force_prefix:
        return plugin.force_prefix[0]
    elif plugin.disable_force_prefix and plugin.prefix:
        return plugin.prefix[0]
    elif plugin.allow_empty_prefix:
        return ""
    else:
        return ""
'''

NEW = '''def get_plugin_available_prefix(plugin_name: str) -> str:
    """Return the prefix shown in user-facing command hints.

    This is display-only: SV/Trigger matching still uses force_prefix and
    receives text after core has stripped command_start. Do not use this
    value for parsing or command registration.
    """
    plugin = SL.plugins.get(plugin_name)
    if plugin is None:
        raise ValueError(t("插件{plugin_name}不存在!", plugin_name=plugin_name))
    prefix = ""
    if not plugin.disable_force_prefix and plugin.force_prefix:
        prefix = plugin.force_prefix[0]
    elif plugin.disable_force_prefix and plugin.prefix:
        prefix = plugin.prefix[0]
    elif plugin.allow_empty_prefix:
        prefix = ""
    if not prefix:
        return ""
    command_start = core_config.get_config("command_start")
    if isinstance(command_start, str):
        command_start = [command_start]
    if "." in (command_start or []) and not prefix.startswith("."):
        return f".{prefix}"
    return prefix
'''

MARKER = "Return the prefix shown in user-facing command hints."
HELP_MARKER = '"desc": ".ww'
LITERAL_PATCHES = (
    (
        "gsuid_core/plugins/XutheringWavesUID/XutheringWavesUID/wutheringwaves_config/config_default.py",
        "使用ww角色攻略时选择的提供方",
        "使用.ww角色攻略时选择的提供方",
    ),
    (
        "gsuid_core/plugins/EndUID/EndUID/end_ann/ann_card.py",
        "使用 end公告#序号 查看详情",
        "使用 .end公告#序号 查看详情",
    ),
)


def patch_literals(root: Path) -> int:
    """Patch command hints that are literal strings rather than PREFIX templates."""
    for rel, old, new in LITERAL_PATCHES:
        target = root / rel
        if not target.is_file():
            print(f"[command-prefix-display] skip missing literal target: {target}")
            continue
        text = target.read_text(encoding="utf-8")
        if new in text:
            print(f"[command-prefix-display] literal already patched: {target}")
            continue
        if old not in text:
            print(
                f"[command-prefix-display] unexpected literal in {target}: {old!r}",
                file=sys.stderr,
            )
            return 1
        backup = target.with_name(target.name + ".bak-command-prefix")
        if not backup.exists():
            backup.write_text(text, encoding="utf-8")
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        print(f"[command-prefix-display] patched literal in {target}")
    return 0


def patch_help_examples(root: Path) -> int:
    """XWUID's change-help JSON has four literal ww examples.

    They are descriptions, not placeholders consumed by get_plugin_available_prefix,
    so the core display-prefix fix cannot cover them. Keep this text-only rewrite
    idempotent; the file is pinned upstream and is refreshed by the installer.
    """
    target = (
        root
        / "gsuid_core"
        / "plugins"
        / "XutheringWavesUID"
        / "XutheringWavesUID"
        / "wutheringwaves_help"
        / "change_help.json"
    )
    if not target.is_file():
        print(f"[command-prefix-display] skip missing help file: {target}")
        return 0
    text = target.read_text(encoding="utf-8")
    if HELP_MARKER in text:
        print("[command-prefix-display] help examples already patched")
        return 0
    count = text.count('"desc": "ww')
    if count == 0:
        print("[command-prefix-display] no literal ww help examples found")
        return 0
    backup = target.with_name(target.name + ".bak-command-prefix")
    if not backup.exists():
        backup.write_text(text, encoding="utf-8")
    target.write_text(text.replace('"desc": "ww', '"desc": ".ww'), encoding="utf-8")
    print(f"[command-prefix-display] patched {count} help example(s) in {target}")
    return 0


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    target = root / "gsuid_core" / "sv.py"
    if not target.is_file():
        print(f"[command-prefix-display] missing: {target}", file=sys.stderr)
        return 1

    text = target.read_text(encoding="utf-8")
    if MARKER in text:
        print("[command-prefix-display] core helper already applied")
    else:
        if text.count(OLD) != 1:
            print(
                f"[command-prefix-display] unexpected helper in {target}; "
                f"expected one exact match, found {text.count(OLD)}",
                file=sys.stderr,
            )
            return 1

        backup = target.with_name(target.name + ".bak-command-prefix")
        if not backup.exists():
            backup.write_text(text, encoding="utf-8")
        target.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
        print(f"[command-prefix-display] patched {target}")

    if patch_literals(root) != 0:
        return 1
    return patch_help_examples(root)


if __name__ == "__main__":
    raise SystemExit(main())
