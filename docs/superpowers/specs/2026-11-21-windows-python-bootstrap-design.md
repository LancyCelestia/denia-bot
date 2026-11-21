# Windows 一键启动：首次运行下载 Python 3.12

日期：2026-11-21
状态：已批准（B 方案，2026-11-21）
规模：小改动

## 背景与目标

`tools\startup-preflight.ps1` 在 `nonebot\.venv` 缺失时，只尝试 `py -3.12` 和 PATH 上的 `python.exe`。干净 clone 实测：

- `py -3.12` 找不到运行时；
- PATH 上的 `python.exe` 是 3.14.6，被版本检查拒绝；
- 本机已有 uv 管理的 3.12.13（`py -V:Astral\CPython3.12.13`），旧脚本不认识它。

结果：一键启动在 Python 发现阶段退出，无法进入依赖安装。

目标：干净 Windows x64 clone 在没有任何可用 Python 3.12 时，由脚本首次运行下载固定版本的 Python 3.12，校验后创建 `nonebot\.venv`，再继续现有 preflight。仓库不提交运行时二进制。

## 方案选择

| 方案 | 结论 |
|---|---|
| A. 提交 python-build-standalone 到 Git | 不选：仓库约 +22 MB，升级累积历史；与当前不分发第三方运行时的策略冲突。 |
| B. 首次运行下载固定版本 | **选用**：仓库保持源码分发；干净机首次联网即可。 |
| C. 只写入 Release ZIP | 不选：不能满足 `git clone` 后直接测试。 |
| D. 只修 Python 发现 | 不选：仍要求机器预装 3.12/uv，不能保证干净生产。 |

## 设计

### 运行时来源（固定）

- 项目：Astral `python-build-standalone`
- Release：`20260510`
- 资产：`cpython-3.12.13+20260510-x86_64-pc-windows-msvc-install_only_stripped.tar.gz`
- 大小：21,921,642 bytes（约 21 MB）
- SHA256：`24168aff2e7d93784c6a436124c4ebb79b076a4e289bde4902c08333507b71d0`
- URL：`https://github.com/astral-sh/python-build-standalone/releases/download/20260510/cpython-3.12.13%2B20260510-x86_64-pc-windows-msvc-install_only_stripped.tar.gz`
- 解压后约 39 MB，包含 Python 3.12.13、`venv`、`ensurepip`、`pip` 和 `LICENSE.txt`。

### 发现顺序

在 `nonebot\.venv` 缺失时：

1. 已下载的 `runtime\python312\python.exe`（版本必须为 3.12）；
2. `py -0p` 列出的已注册 3.12（列表命令，只读取已安装运行时，不启动解释器，不触发 Python Install Manager 自动安装）；
3. 以上都没有，且允许安装：下载并校验固定运行时到 `runtime\python312`；
4. 仍失败：`Stop-Check`，不启动任何服务。

发现阶段不执行 PATH 上的 `python.exe`：Python Install Manager 可能在无运行时或 `automatic_install` 被配置/管理员策略设为 true 时自动安装；`py -0p` 是列表命令，不受该设置影响。未注册到 `py` 的 PATH Python 3.12 会走固定运行时下载。

`-CheckOnly` 与 `-SkipInstall` 禁止第 3 步；缺 Python 时直接报告。

### 下载与安装

- `Invoke-WebRequest -UseBasicParsing -OutFile`，启用 TLS 1.2，设置 `$ProgressPreference = 'SilentlyContinue'`。
- 发现阶段只用 `py -0p` 列表命令；不执行 PATH 上的 `python.exe`，避免 Python Install Manager 自动安装。
- 如设置了 `HTTPS_PROXY` / `HTTP_PROXY`，传给 `-Proxy`；否则走系统代理。
- 下载后先 `Get-FileHash -Algorithm SHA256`，与固定值比较；不匹配立即删除并退出。
- 解压使用 `%SystemRoot%\System32\tar.exe -xzf ... --strip-components=1`（Windows 10 1803+ 自带的 bsdtar）。缺少 `tar.exe` 时给出明确错误。
- 解压到 `runtime\.tmp-python312-<guid>`，验证 `python.exe -V` 为 3.12，再原子移动到 `runtime\python312`。
- 失败时清理临时目录；已有的 `runtime\python312` 若版本不对，不自动删除，报告让用户删除后重试。
- 成功后用该解释器执行 `-m venv nonebot\.venv`，后续流程不变。

### 文件变更

- `tools/startup-preflight.ps1`：新增固定常量与下载/校验/解压函数，调整 venv 创建分支。
- `.gitignore`：新增 `/runtime/`。
- `README.md`、`docs/OPERATIONS.md`：说明首次下载、缓存位置、`-SkipInstall` / `-CheckOnly` 行为和删除重下。
- `CHANGELOG.md`：加入「修复与加固」。
- 本设计文档。

### 非目标

- 不内置 SnowLuma；干净 clone 仍会在 `snowluma\node.exe` 处停止（预期）。
- 不改变 `nonebot\.venv` 布局，不放宽 Python 3.12 要求。
- 不支持 ARM64；x64 以外的机器回退到系统 Python 或报错。
- 不依赖 uv 专属接口；`py -0p` 列出的已注册 3.12（包括 uv 注册的运行时）可以复用；没有注册的 3.12 时由脚本下载固定运行时。

## 验证

1. 正常 PATH（`py -0p` 可列出 3.12.13）运行 `启动机器人.bat`：
   - 复用已注册的 3.12，不下载；
   - `nonebot\.venv` 创建成功；
   - preflight 继续并停在 `snowluma\node.exe` 缺失；
   - 退出码 4，8080/5099/8091/8765 无监听。
2. 受限 PATH（移除 `py.exe` / `python.exe`，例如仅 `C:\Windows\System32;C:\Windows;C:\Windows\System32\WindowsPowerShell\v1.0`），无 `runtime\python312`、无 `nonebot\.venv`，运行一键启动：
   - 出现下载/校验/解压日志；
   - `runtime\python312\python.exe -V` 为 3.12.13；
   - `nonebot\.venv` 创建成功；
   - preflight 继续并停在 `snowluma\node.exe` 缺失；
   - 退出码 4，四个端口无监听。
3. 第二次运行：复用已下载运行时，不重复下载。
4. `-SkipInstall`：不下载，缺 Python 时报告并退出。
5. `-CheckOnly`：不下载，只做文件检查。
6. `git status` 在 clone 中保持干净（`runtime/` 被忽略）。
7. 删除 `runtime\python312` 后可重新下载。
8. 非 ASCII 路径：`py -0p` 列表输出在 CP437 等旧代码页下仍能解码；`D:\测试目录\python.exe` 提取结果逐字一致。
9. 哈希不匹配：临时改错 SHA 时退出 1，不产生 `runtime\python312`，不留下 `.tmp-python312-*`，无服务监听。

## 风险与回滚

- 首次运行需要访问 GitHub；网络失败时不启动服务，错误信息给出 URL。
- 固定 SHA256 防止下载内容漂移；升级 Python 时同时更新版本、URL、SHA256。
- 回滚：删除 `runtime\python312` 与 `.gitignore` / 脚本改动即可；无持久状态。
