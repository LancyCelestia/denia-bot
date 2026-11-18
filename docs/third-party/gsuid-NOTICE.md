# 第三方来源：gsuid_core 与游戏账号查询插件

本仓库可选接入 [gsuid_core](https://github.com/Genshin-bots/gsuid_core) 生态，提供鸣潮、终末地等游戏的账号绑定、查询与签到。**这些组件由部署者在安装时从上游仓库下载，运行在 `gsuid/core`，不随本仓库（MIT）分发，也不在本仓库的版本控制内**；每个组件均按 **GPL-3.0** 授权，与上游同源。

| 组件 | 作用 | 上游仓库 | 固定版本 | 许可证 |
|---|---|---|---|---|
| `gsuid_core` | 核心服务：账号绑定、数据存储与插件调度，监听 `127.0.0.1:8765` | <https://github.com/Genshin-bots/gsuid_core> | 提交 `aaa8bd6a6a1061d624e72ffaa547a3f1baa33931` | GPL-3.0，[LICENSE](https://github.com/Genshin-bots/gsuid_core/blob/aaa8bd6a6a1061d624e72ffaa547a3f1baa33931/LICENSE) |
| `XutheringWavesUID` | 鸣潮账号绑定与查询：面板、评分与伤害计算 | <https://github.com/Loping151/XutheringWavesUID> | 提交 `a17e3154a89f63cb8ce2ef176b07880613d33582` | GPL-3.0，[LICENSE](https://github.com/Loping151/XutheringWavesUID/blob/a17e3154a89f63cb8ce2ef176b07880613d33582/LICENSE) |
| `EndUID` | 明日方舟：终末地账号绑定与查询 | <https://github.com/Loping151/EndUID> | 提交 `a6fff57a527400bd008a85fbf72e49ddac849a14` | GPL-3.0，[LICENSE](https://github.com/Loping151/EndUID/blob/a6fff57a527400bd008a85fbf72e49ddac849a14/LICENSE) |
| `RoverSign` | 鸣潮、战双、库街区签到（依赖 `XutheringWavesUID` 及其数据库） | <https://github.com/Loping151/RoverSign> | 提交 `98be9d36144b46471c5094dd751f8f3b63d623bf` | GPL-3.0，[LICENSE](https://github.com/Loping151/RoverSign/blob/98be9d36144b46471c5094dd751f8f3b63d623bf/LICENSE) |
| `nonebot-plugin-genshinuid`（模块 `GenshinUID`） | NoneBot2 与 `gsuid_core` 的适配器，通过 WebSocket 连接 8765 | <https://github.com/KimigaiiWuyi/GenshinUID/tree/v4-nonebot2> | PyPI `6.1.1`（分支 `v4-nonebot2`） | GPL-3.0，[LICENSE](https://github.com/KimigaiiWuyi/GenshinUID/blob/v4-nonebot2/LICENSE) |

## 分发方式

- 核心与插件由 `gsuid/install.sh` / `gsuid/install.ps1` 克隆到 `gsuid/core`（插件位于 `gsuid/core/gsuid_core/plugins`），提交按上表固定；安装时应用 `gsuid/patches/enduid-render-size.py`（EndUID 渲染尺寸）和 `gsuid/patches/command-prefix-display.py`（core 显示前缀），不发布其它二次修改。
- 适配器作为普通 PyPI 依赖由 `requirements.lock` 固定：`nonebot-plugin-genshinuid==6.1.1`（额外依赖 `msgspec==0.22.0`、`aiofiles==25.1.0`）；单独补装用 `uv pip install --python <NoneBot venv 的 python> nonebot-plugin-genshinuid==6.1.1`。
- **本地兼容修改**：`gsuid/patches/enduid-render-size.py` 会在安装时把 EndUID 的 `utils/render_utils.py` 远程渲染请求补成 `width=1000,height=2400`，以适配本仓库共享渲染器 `/render` 的默认 900×1200 视口。该文件属于 EndUID 的 GPL-3.0 修改版；再分发修改后的 EndUID 时，必须保留上游声明并提供本补丁脚本或修改后的完整源码。
- **显示前缀修改**：`gsuid/patches/command-prefix-display.py` 修改 `gsuid_core/sv.py` 的 `get_plugin_available_prefix()`，在 core 的 `command_start` 包含 `.` 时让帮助、登录提示、卡片按钮显示 `.ww...` / `.end...` / `.core...`；同时修正 XWUID `change_help.json`、`config_default.py` 和 EndUID `ann_card.py` 中无法通过 `PREFIX` 模板覆盖的硬编码命令示例。它只影响显示，不改变 SV/Trigger 匹配。上述文件分别属于 `gsuid_core`、XutheringWavesUID、EndUID 的 GPL-3.0 修改版；再分发修改后的组件时，必须保留上游声明并提供本补丁脚本或修改后的完整源码。
- **NoneBot 侧前缀接线**：`nonebot/bot.py` 加载 `GenshinUID` 后把 `GenshinUID.receive.command_starts` 置空，阻止适配器剥掉 NoneBot 的 `.`；core 用 `command_start=["."]`、`enable_empty_start=false` 统一剥点。适配器包内文件未修改，这只是运行时 monkeypatch。
- 运行期配置与账号数据位于 `gsuid/core/data`（含 `config.json`），属于部署机数据，不进入本仓库。

## 义务说明

本仓库只包含接入说明与脚本，不复制上述项目的源码，因此仓库本身仍按根目录 `LICENSE`（MIT）授权。**若再分发一个打包了 `gsuid/core` 或其内容的产物，必须同时满足 GPL-3.0 的要求**：保留各上游的 LICENSE、署名与源码获取方式，并向使用者提供对应完整源码（含修改）。

部署步骤见 `gsuid/README.md`，服务与备份说明见 `docs/OPERATIONS.md`。
