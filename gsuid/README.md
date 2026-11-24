# gsuid_core 游戏账号查询（可选外部服务）

本目录是**部署脚手架**，不是第三方代码的副本：`versions.env` 固定上游提交，
`install.sh` / `install.ps1` 在部署机把核心与插件克隆到 `gsuid/core`，
`docker-compose.yml` 提供可选的容器启动方式。

其中所有上游项目都是独立的 **GPL-3.0** 程序，按固定提交在部署时拉取，
**不随本仓库分发，也不属于本仓库 MIT 授权的部分**。来源、固定版本和许可义务见
[`docs/third-party/gsuid-NOTICE.md`](../docs/third-party/gsuid-NOTICE.md)；
NoneBot 主文档见 [`README.md`](../README.md)。

> **生产部署位置**：机器人运行在 Linux bot 服务器（部署路径 `/opt/qqbot`，服务
> `qqbot.service` / `qqbot-dashboard.service`），core 应安装在该服务器的
> `/opt/qqbot/gsuid/core`，并用 systemd 跟随 `qqbot.service`。本仓库的
> `tools\启动所有.ps1` 与 `tools\gsuid-局域网访问.ps1` 只服务于 **Windows 本机
> 开发环境**，不是生产入口。

## 1. 架构：两个进程，各管一半

QQ 消息由 NoneBot2 接收，适配器（PyPI 包 `nonebot-plugin-genshinuid==6.1.1`，
模块名 `GenshinUID`）通过本地 WebSocket 转发给 core；core 查询游戏数据、执行签到，
把结果原路回传，由 NoneBot 发回 QQ。

**core 不直连 QQ**，也不处理聊天、人格、知识库等业务。不启动 core 时，NoneBot
其余功能完全不受影响：`nonebot/bot.py` 只在 `GSUID_CORE_ENABLE` 不为 `0` 时加载
适配器，core 连不上只会报连接失败。

```
QQ ←→ SnowLuma/协议端 ←→ NoneBot2（适配器 GenshinUID，进程内加载）
                              │  ws://127.0.0.1:8765
                              ▼
                         gsuid_core（Web 控制台 /app）
                              ├─ XutheringWavesUID  鸣潮
                              ├─ EndUID             终末地
                              └─ RoverSign          签到（依赖 XutheringWavesUID）
```

启动顺序：**先 core，后 NoneBot**。core 重启后，`GSUID_CORE_REPEAT=true` 会每 10 秒主动重连；
为 `false` 时适配器在下一条消息或重启时重试。重新登录、换 WS_TOKEN 后需要重启 NoneBot。

## 2. 目录布局

```
gsuid/
├─ versions.env        上游仓库与固定提交（更新时只改这里）
├─ install.sh          Linux/macOS 安装与更新
├─ install.ps1         Windows PowerShell 安装与更新
├─ docker-compose.yml  可选：用容器跑 core
├─ qqbot-gsuid-core.service  Linux systemd 单元模板
├─ README.md
└─ core/               运行时克隆，被 .gitignore 忽略
   ├─ .venv/           uv 环境
   ├─ data/            配置、数据库、账号绑定、插件资源
   └─ gsuid_core/plugins/
      ├─ XutheringWavesUID/
      ├─ EndUID/
      └─ RoverSign/
```

`gsuid/core` 里的文件是**上游检出**，安装脚本会用 `git checkout --force --detach`
把它们对齐到固定提交，所以不要在那里改代码；本地改动会在下次安装时被覆盖。

## 3. 前置条件

- **Python 3.11–3.13**。安装脚本用 uv 固定 3.13；其他 3.11–3.13 版本可自行
  `uv sync --python <版本>`。
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** 与 **git**：
  安装脚本启动时会检查，缺失直接报错。
- **网络**：需能访问 GitHub、PyPI 与插件资源站。首次安装会下载较多依赖和资源，
  另加可选的 Playwright Chromium，建议预留数 GB 磁盘。
- **Docker 可选**：仅在用 `docker-compose.yml` 时需要 Docker Engine/Desktop +
  Compose v2。上游基础镜像已包含 Python、uv、Playwright 与 Chromium。
- **NoneBot 侧**：本仓库 `requirements.lock` 已固定
  `nonebot-plugin-genshinuid==6.1.1`，安装 NoneBot 依赖时会一并装入。单独补装
  （虚拟环境没有 pip 时也可用 uv）：
  ```bash
  uv pip install --python nonebot/.venv/bin/python nonebot-plugin-genshinuid==6.1.1
  # Windows: uv pip install --python nonebot\.venv\Scripts\python.exe nonebot-plugin-genshinuid==6.1.1
  ```

## 4. 安装

```bash
# Linux / macOS
bash gsuid/install.sh

# Windows（PowerShell）
powershell -ExecutionPolicy Bypass -File gsuid\install.ps1
```

脚本按 `versions.env`：

1. 克隆 core 到 `gsuid/core`、三个插件到 `gsuid/core/gsuid_core/plugins/`；
   已存在的克隆会被复用，然后强制检出到固定提交（必要时补拉该提交）。
2. `uv python install 3.13`、在 `gsuid/core` 执行 `uv sync --python 3.13`。
3. 在核心 venv 里 `ensurepip`（插件自动补依赖需要 pip）。
4. 预装插件常用依赖 `pypinyin rapidfuzz opencv-python fonttools`。
5. 应用两个兼容补丁：`patches/enduid-render-size.py`（EndUID 图卡尺寸）和
   `patches/command-prefix-display.py`（把帮助、登录提示、卡片按钮里的
   `ww...` / `end...` 显示成实际的 `.ww...` / `.end...`，并修正少量硬编码
   帮助示例）。补丁只改显示前缀，不改变 core 的命令匹配。

脚本**不写** `nonebot/.env`，也不改 core 配置；这些在下一步手工完成。
可重复执行，用作更新（见 §9）。

## 5. 首次启动与配置

```bash
cd gsuid/core
uv run core
```

首次启动会生成 `data/config.json` 和 `data/core_config.json`。然后：

1. 浏览器打开 **<http://127.0.0.1:8765/app>**，用 `data/config.json` 里的
   `REGISTER_CODE` 注册网页控制台管理员。注册码每人不同，只能注册一个管理员账号。
2. 编辑 `gsuid/core/data/config.json`，至少设置 `masters` 与 `WS_TOKEN`：

   ```json
   {
     "HOST": "localhost",
     "PORT": "8765",
     "masters": ["你的QQ号"],
     "WS_TOKEN": "随机长串",
     "REGISTER_CODE": "首次启动生成，保持不变"
   }
   ```

   `masters` 是主人账号列表（`pm=0`）；其他平台的账号填日志里看到的 `user_id`。
   `WS_TOKEN` 用随机值生成：
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
3. 重启 core 使配置生效。此时还没接上 NoneBot，先 `Ctrl+C` 再重新
   `uv run core`；连上 NoneBot 之后可在聊天里发 **`.core重启`**。
4. 在控制台【Core 配置】里确认主人列表，日志中自己的 `pm` 应为 `0`。
   插件的前缀、开关、权限、白/黑名单也都在 WebConsole 的「插件配置 / 核心配置」里，
   能热更新的立即生效，标注需要重启的用 `.core重启`。

> **AI / 向量配置**：gsuid_core 本身不处理聊天或知识库嵌入。若某个上游插件要求填
> AI/Embedding 接口，只填 SiliconFlow：`https://api.siliconflow.cn/v1`，模型
> `BAAI/bge-m3`（1024 维），Key 用与管理面板同一个。不要指向本地 ONNX 入口
> （`rag/legacy-local/serve.py`，不受支持）。

## 6. 连接 NoneBot

在 `nonebot/.env` 设置（模板见 `nonebot/.env.example` 的 gsuid_core 段）：

```dotenv
GSUID_CORE_ENABLE=1
GSUID_CORE_HOST=127.0.0.1
GSUID_CORE_PORT=8765
GSUID_CORE_BOTID=NoneBot2
GSUID_CORE_WS_TOKEN=<与 config.json 的 WS_TOKEN 完全一致>
GSUID_CORE_REPEAT=false
```

`GSUID_CORE_REPEAT=true` 表示断线后每 10 秒重连；`false` 表示断开后等重启。
然后在仓库根目录启动 NoneBot。core 必须先于 NoneBot 启动，否则适配器首连失败，
需要重启机器人恢复。

验证：在 QQ 里发一条该 core 已注册的指令（如鸣潮插件的帮助），能收到回复即打通；
同时 core 日志会打印收到的消息与 `pm`。完全不使用游戏查询时设
`GSUID_CORE_ENABLE=0`，机器人不会加载适配器。

## 6.1 跟随机器人启动与局域网访问

> 生产机器人是 Linux 服务器的 `qqbot.service`，请优先使用下面的「Linux（systemd）」；
> Windows 一节只用于本机开发。

**Windows（本机开发，不是生产）**

- `tools\启动所有.ps1` 已把 core 作为 `[2/5]` 启动，排在本机 NoneBot 之前；
  core 未安装（`gsuid/core/.venv` 不存在）时只提示并跳过，不影响原机器人。
- `tools\停止所有.ps1` 会按 `logs\gsuid-core.pid` 停止 core 进程树，并用
  `gsuid\core` 命令行兜底，避免 PID 文件丢失时留下孤儿进程。
- 启动完成后脚本会打印本机与局域网 WebConsole 地址，例如
  `http://<本机局域网IP>:8765/app`。
- 防火墙：用**管理员** PowerShell 运行
  `powershell -ExecutionPolicy Bypass -File tools\gsuid-局域网访问.ps1`。
  脚本只放行 `LocalSubnet` 的入站 TCP 8765，网络配置为 Public 时也生效；
  不会放开公网。重复运行会替换同名规则。

**Linux（systemd）**

```bash
sudo cp gsuid/qqbot-gsuid-core.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now qqbot-gsuid-core
```

NoneBot 的服务单元加一个 drop-in，保证 core 先于机器人：

```ini
# /etc/systemd/system/<nonebot-unit>.service.d/gsuid.conf
[Unit]
After=qqbot-gsuid-core.service
Wants=qqbot-gsuid-core.service
```

局域网防火墙（示例，按实际网段修改）：

```bash
sudo ufw allow from <内网网段> to any port 8765 proto tcp
# 或 nftables/云安全组做等价规则；不要对 0.0.0.0/0 开放
```

只在本机使用时，把 unit 的 `--host 0.0.0.0` 改成 `127.0.0.1`，并跳过防火墙规则。

**必须同时满足的安全项**

1. core 的 `data/config.json`：`HOST` 为 `0.0.0.0`（局域网监听）；只本机用就保持
   `127.0.0.1`。
2. `WS_TOKEN` 非空，且 `nonebot/.env` 的 `GSUID_CORE_WS_TOKEN` 与之一致；
   没有 token 时，来自非信任 IP 的 `/ws/*` 连接会被 core 直接拒绝。
3. WebConsole 首次用 `REGISTER_CODE` 注册后立刻设置强密码；不要把 8765 暴露到
   公网。`TRUSTED_IPS` 只保留回环，不要为了“方便”把整个局域网加进去。
4. 局域网只需要打开 WebConsole 时，防火墙规则限定 `LocalSubnet` 即可；USB 网卡、
   虚拟网卡也会被启动脚本列出，按实际需要选择地址。

## 7. 三个插件的注意事项

- **XutheringWavesUID（鸣潮）**：仓库自带构建资源，首次安装/更新后可能需要下载
  资源；权重或伤害计算更新时发 `.ww下载全部资源` 自动重载。图卡渲染推荐复用本机
  渲染器：`RemoteRenderEnable=true`、`RemoteRenderUrl=http://127.0.0.1:3100/render`
  （插件默认写的是 3000；core 容器/进程必须能访问该回环地址），失败会回退到本地
  Playwright。本地 Playwright 需额外执行 `cd gsuid/core && uv run playwright install chromium`；
  外置渲染则不需要 Chromium。日韩文字体缺失时按上游建议补字体。
- **RoverSign（签到）**：依赖 **XutheringWavesUID 及其数据库表**，必须先在
  `gsuid/core/gsuid_core/plugins/` 装好并至少启动过一次 XutheringWavesUID，
  否则表不存在。签到开关默认全部关闭，在控制台按需打开手动/自动签到；
  默认前缀 `ww`，全局点号后实际发送 `.ww签到`。它没有独立的账号绑定入口，
  直接使用 XWUID / 战双已绑定的凭据。
- **EndUID（终末地）**：支持**森空岛扫码登录**；图卡同样走
  `RemoteRenderEnable=true`、`RemoteRenderUrl=http://127.0.0.1:3100/render`。
  EndUID 日历模板固定 1000px 宽，而共享 `/render` 默认 900x1200，因此安装脚本会
  应用 `patches/enduid-render-size.py`，让远程请求带 `width=1000,height=2400`；
  更新 EndUID 的 pin 后要重跑安装脚本，补丁不匹配会明确报错。也可改用本地
  Playwright（需安装 Chromium）。

**账号绑定与 WebConsole**

WebConsole **没有**游戏账号绑定表单；它负责管理员、主人、插件开关、渲染、签到
策略和运行日志。游戏账号绑定走 QQ 命令：

| 游戏 | 绑定命令（按 core 已加载前缀） | 说明 |
|---|---|---|
| 鸣潮 | `.ww登录` | 网页扫码或短信登录；也可 `.ww邮箱登录`、`.ww抽卡登录` |
| 终末地 | `.end登录` 或 `.end扫码登录` | `.end登录` 走手机号网页/扫码；`.end扫码登录` 用森空岛 APP 扫码 |
| 鸣潮签到 | `.ww签到` / `.ww开启自动签到` | RoverSign 默认关闭，开关在 WebConsole 的 RoverSign 配置 |
| 战双签到 | 同 RoverSign 命令（按已绑定 UID） | 需先绑定战双凭据 |

NoneBot 侧命令前缀是 `.`。`bot.py` 在加载适配器后把
`GenshinUID.receive.command_starts` 置空，**适配器不再剥点**，而是把 `.ww登录`
原样交给 core；core 配置为：

```json
"command_start": ["."],
"enable_empty_start": false
```

因此 core 统一剥掉一个点，再由插件前缀 `ww` / `end` 命中。QQ 群/私聊、
WebConsole 实时聊天都使用 `.ww登录`、`.end登录`、`.ww签到`、`.core重启`、
`.core帮助`；**不带点的 `ww登录` / `end登录` 会被 core 忽略**。这是一项
NoneBot 运行时接线补丁，不修改 adapter 包内文件；核心/适配器升级后随
`bot.py` 一起生效。

登录链接地址在 core 面板配置：

- `WavesLoginUrl` 指向本 core（直连或反向代理）时，**必须同时打开
  `WavesLoginUrlSelf`**。该开关表示“这是自己的登录页”；关闭时插件会把地址当作
  外置登录服务，请求其 `POST /waves/token`。本 core 不提供这条路由，命令会返回
  “服务请求失败! 请稍后再试”。
- `EndLoginUrl` 填本 core 的对外地址（直连或反代到 `/end` 登录页）；EndUID
  直接打开该登录页，不需要额外的 self 开关。
- 留空时插件可能回退到 core 的 `HOST:PORT` 或 `127.0.0.1`；群聊远端用户需要填
  能访问到的地址。二维码开关 `WavesQRLogin` / `EndQRLogin` 可让登录链接直接以
  二维码发送。
- **不要混淆两个开关**：`WavesQRLogin` 只决定“发文本链接还是二维码图片”；
  `WavesLoginUrlSelf` 决定“登录服务走本 core 还是外置服务”。本部署必须保持
  `WavesLoginUrlSelf=true`，否则即使 `WavesQRLogin` 已关闭，登录仍会走外置
  `/waves/token` 并失败。

三个插件的前缀与所有开关都在 core 的 WebConsole 中管理。除安装脚本自动应用的
EndUID 渲染尺寸补丁外，不要手改插件源码；需要本地修改时请写进 `gsuid/patches/`
并在 NOTICE 中署名。

## 8. Docker 方式（可选）

`docker-compose.yml` 是 `uv run core` 的替代方案，**不要同时用**（会抢 8765）。
它用上游 `Dockerfile` 的 `runtime` 阶段构建镜像，把 `gsuid/core` 挂进容器，
venv 放在命名卷里。

```bash
cd gsuid
docker compose up -d --build
docker compose logs -f
```

- 前置条件：先用 `install.sh` / `install.ps1` 克隆 `gsuid/core`，否则构建上下文为空。
- 默认只映射到回环地址：`127.0.0.1:8765`；换端口用 `GSUID_PORT`。
- 代码与数据持久化在 `./core`；`gsuid_venv` 卷只放 venv。`docker compose down`
  不会丢数据，`down -v` 会删 venv（数据仍在 `./core`）。
- 改 `gsuid/core/data/config.json` 后 `docker compose restart`。
- 构建目标可用 `GSCORE_BUILD_TARGET` 切换；pip 镜像用 `GSCORE_PYTHON_INDEX`。
- 容器内默认监听 `0.0.0.0:8765`，但宿主机映射限定在回环地址；不要改成
  `0.0.0.0:8765:8765` 直接暴露到公网。

## 9. 更新与回滚

1. 编辑 `gsuid/versions.env`，把对应 `*_SHA` 改成要升级到的上游提交（先读上游
   release/commit 说明，正式版本可用 `git ls-remote <repo>` 对照）。
2. 重跑 `gsuid/install.sh` 或 `gsuid/install.ps1`：已存在的克隆会复用并重新
   检出到新提交，核心依赖重新 `uv sync`；不会动 `gsuid/core/data`。
3. 重启 core：聊天里发 `.core重启`，或重启进程 / `docker compose restart`；
   若升级了插件依赖且适配器连不上，重启 NoneBot。
4. 回滚就是把 `*_SHA` 改回旧值，重跑脚本并重启。**升级前先备份
   `gsuid/core/data`**；数据库结构不一定能降级。

不要把 `gsuid/core` 当成普通 git 仓库去 `git pull`：它处于 detached HEAD，
安装脚本会强制对齐到 pin，手动合并会丢失。插件依赖冲突时，删掉
`gsuid/core/.venv` 重跑安装脚本；Docker 方式用 `docker compose down -v && docker compose up -d --build`
（只重置 venv 卷，`data` 不受影响）。

## 10. 来源、固定版本与许可

| 组件 | 角色 | 上游仓库 | 固定版本 | 许可证 |
|---|---|---|---|---|
| `gsuid_core` | 核心服务：账号绑定、数据存储、插件调度，监听 8765 | <https://github.com/Genshin-bots/gsuid_core> | 提交 `aaa8bd6a6a1061d624e72ffaa547a3f1baa33931` | GPL-3.0 |
| `XutheringWavesUID` | 鸣潮账号绑定与查询：面板、评分与伤害计算 | <https://github.com/Loping151/XutheringWavesUID> | 提交 `a17e3154a89f63cb8ce2ef176b07880613d33582` | GPL-3.0 |
| `EndUID` | 明日方舟：终末地账号绑定与查询 | <https://github.com/Loping151/EndUID> | 提交 `a6fff57a527400bd008a85fbf72e49ddac849a14` | GPL-3.0 |
| `RoverSign` | 鸣潮 / 战双 / 库街区签到，依赖 `XutheringWavesUID` | <https://github.com/Loping151/RoverSign> | 提交 `98be9d36144b46471c5094dd751f8f3b63d623bf` | GPL-3.0 |
| `nonebot-plugin-genshinuid` | NoneBot2 与 core 的 WebSocket 适配器（模块 `GenshinUID`） | <https://github.com/KimigaiiWuyi/GenshinUID/tree/v4-nonebot2> | PyPI `6.1.1`（分支 `v4-nonebot2`） | GPL-3.0 |

以上都是**外部项目**，不属于本仓库的 MIT 代码；本仓库只保存 pin、安装脚本和文档，
代码在部署时拉取。固定提交、上游署名和许可文本对应关系见
[`docs/third-party/gsuid-NOTICE.md`](../docs/third-party/gsuid-NOTICE.md)。

## 11. 数据与备份

`gsuid/core/data` 下有：

- `config.json`：`masters`、`WS_TOKEN`、`REGISTER_CODE`、监听地址等；
- `core_config.json`：核心运行参数（尽量用 WebConsole 改）；
- `GsData.db`：大部分插件的账号绑定与 CK；
- `plugins_configs/*.json`：各插件独立配置；
- 各插件自己的资源、面板、抽卡记录等目录。

这些内容已被 `.gitignore` 排除，**等同账号凭据**，不要提交、不要外传。备份时
先停 core，再连同机器人其他数据一起冷备整个 `gsuid/core/data`。恢复后首次启动
若注册码/WS_TOKEN 变化，记得同步 `nonebot/.env`。

## 12. 安全与许可注意

- **只监听回环地址**：`config.json` 保持 `HOST=localhost`（或 `127.0.0.1`）；
  compose 默认映射 `127.0.0.1:8765`。远程访问走 SSH 隧道或反代 + HTTPS，
  不要把 8765 直接暴露公网。
- **`WS_TOKEN` 必须非空**：留空时只应在本机回环部署使用；需要跨机连接时
  同时配置 `TRUSTED_IPS`。token 只写在 `nonebot/.env` 与 `data/config.json`，
  两边一致且不进 git。
- **WebConsole 账号唯一**：`REGISTER_CODE` 是一次性注册凭据，注册完成后不要外泄；
  控制台能改插件开关与主人列表，等同于管理权限。
- **上游使用声明**：三个插件与 core 均声明仅供学习、禁止商业用途；RoverSign/
  XutheringWavesUID 另有自己的使用条款，部署前请读上游 README。
- **GPL-3.0 义务**：这些是独立程序，不是本仓库 MIT 代码。自用部署不产生额外义务；
  一旦把包含它们的镜像、整包或修改版**对外分发**，必须按 GPL-3.0 提供对应完整
  源码、保留上游署名与许可文本，并标明修改。仅通过聊天界面使用不适用 AGPL 的
  网络条款（上游是 GPL-3.0，不是 AGPL-3.0）。

## 13. 相关文档

- 第三方来源与许可：[`docs/third-party/gsuid-NOTICE.md`](../docs/third-party/gsuid-NOTICE.md)
- 本仓库总览与组件表：[`README.md`](../README.md)
- 运维手册（服务清单、启动顺序、备份）：[`docs/OPERATIONS.md`](../docs/OPERATIONS.md)
- 上游文档：<https://docs.sayu-bot.com>（安装、控制台、插件、适配器）
