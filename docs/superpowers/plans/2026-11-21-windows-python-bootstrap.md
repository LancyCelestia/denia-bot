# Windows Python 3.12 Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Windows 一键启动在干净 clone、系统没有可用 Python 3.12 时，首次运行下载固定版本的 python-build-standalone 3.12.13 到 `runtime\python312`，校验后创建 `nonebot\.venv`，再继续现有 preflight。

**Architecture:** 在 `tools/startup-preflight.ps1` 的 venv 创建分支加入发现阶梯：已下载运行时 → `py -3.12` → PATH 上的 3.12 → 固定 URL 下载。下载先校验 SHA256，再用 Windows 自带 `tar.exe` 解压到临时目录并原子移动。运行时目录被 `.gitignore` 忽略；`-CheckOnly` / `-SkipInstall` 禁止下载。

**Tech Stack:** Windows PowerShell 5.1、python-build-standalone 3.12.13（20260510）、System32 `tar.exe`（bsdtar）、现有 `.bat` / `.ps1` 启动入口。

**Spec:** `docs/superpowers/specs/2026-11-21-windows-python-bootstrap-design.md`

## Global Constraints

- `tools/startup-preflight.ps1` 必须保持 ASCII-only（PowerShell 5.1 按 GBK 解析无 BOM 的 UTF-8 会报字符串终止错误）。
- Python 版本必须恰好为 `3.12`；不得放宽到 3.13/3.14。
- 固定资产不可漂移：版本 `3.12.13`、release `20260510`、资产 `install_only_stripped.tar.gz`、大小 21,921,642 bytes、SHA256 `24168aff2e7d93784c6a436124c4ebb79b076a4e289bde4902c08333507b71d0`。
- URL 必须固定：`https://github.com/astral-sh/python-build-standalone/releases/download/20260510/cpython-3.12.13%2B20260510-x86_64-pc-windows-msvc-install_only_stripped.tar.gz`。
- preflight 失败时不得启动任何服务；一键启动脚本必须继续返回非 0。
- 下载目录固定为 `runtime\python312`，并由 `/runtime/` 忽略；不得提交运行时二进制。
- 仅支持 x64 Windows；使用 `%SystemRoot%\System32\tar.exe`（Windows 10 1803+）。
- 不下载、不恢复 SnowLuma；干净 clone 仍停在 `snowluma\node.exe` 缺失。
- 不在本计划中修改 `snowluma`、`requirements.lock` 或 Python 版本策略。

## Review Focus

- **首次下载网络失败或代理失败**：必须清理临时文件、返回非 0、不启动任何服务，并给出可操作的 URL/代理错误。
- **SHA256 不匹配**：必须在解压和执行前中止；不得把未校验内容放进 `runtime\python312`。
- **已有 `runtime\python312` 损坏或版本不对**：不得静默复用；报告删除目录后重试。
- **`-SkipInstall` / `-CheckOnly`**：不得触发下载；缺 Python/venv 时返回非 0。
- **非 x64 或缺少 `tar.exe`**：给出明确错误；若系统 Python 3.12 可用则仍应走系统解释器。
- **并发首次运行**：临时目录唯一，移动前复查目标；不得把半成品留在 `runtime\python312`。
- **Python 下载成功但 SnowLuma 缺失**：这是干净 clone 的预期结果；退出码 4，8080/5099/8091/8765 无监听。

---

### Task 1: 在 preflight 中实现固定运行时下载与发现阶梯

**Files:**
- Modify: `tools/startup-preflight.ps1`（路径常量区约 13-26 行；`Write-Host` 之前约 67 行；venv 创建分支约 78-107 行）
- Modify: `.gitignore`（依赖/缓存区，`.venv/` 规则之后）
- Test: 在 `.tmp/wt-python-bootstrap` 这个干净检出中运行 preflight（无 `nonebot\.venv`、无 `runtime\python312`）

**Interfaces:**
- Consumes: 现有 `$rootPath`、`$botDir`、`$venvDir`、`$python`、`$requirements`、`Stop-Check`、`Write-Section`、`Write-Ok`。
- Produces:
  - `Test-Python312([string]$Exe) -> [bool]`：运行解释器并检查 `sys.version_info` 为 `3.12`。
  - `Find-HostPython312() -> [string]`：依次尝试 `py -3.12`（用 `-c "import sys; print(sys.executable)"` 取真实路径）和 PATH 上的 `python.exe`；找不到返回 `$null`。
  - `Get-BundledPython() -> [string]`：`runtime\python312\python.exe` 存在且为 3.12 时返回路径；存在但不可用时 `Stop-Check`；不存在返回 `$null`。
  - `Install-BundledPython() -> [string]`：下载、校验、解压并返回 `runtime\python312\python.exe`；任何失败调用 `Stop-Check`。
  - 常量：`$bundledPythonVersion`、`$bundledPythonUrl`、`$bundledPythonSha256`、`$bundledRuntimeDir = Join-Path $rootPath 'runtime'`、`$bundledPythonDir = Join-Path $bundledRuntimeDir 'python312'`、`$bundledPythonExe = Join-Path $bundledPythonDir 'python.exe'`。

- [ ] **Step 1: 复现基线失败**

在干净 worktree 中运行：

```powershell
cd D:\Software\qqbot\.tmp\wt-python-bootstrap
powershell -NoProfile -ExecutionPolicy Bypass -File tools\startup-preflight.ps1 -Root (Get-Location).Path -NoPause
```

Expected: 退出码 1，输出 `Could not create the NoneBot venv`，且没有 `runtime\python312`。这证明当前发现逻辑的缺口。

- [ ] **Step 2: 添加固定常量**

在 `$pluginSettings` 之后添加以下常量（保持 ASCII-only）：

```powershell
$bundledPythonVersion = '3.12.13'
$bundledPythonUrl = 'https://github.com/astral-sh/python-build-standalone/releases/download/20260510/cpython-3.12.13%2B20260510-x86_64-pc-windows-msvc-install_only_stripped.tar.gz'
$bundledPythonSha256 = '24168aff2e7d93784c6a436124c4ebb79b076a4e289bde4902c08333507b71d0'
$bundledRuntimeDir = Join-Path $rootPath 'runtime'
$bundledPythonDir = Join-Path $bundledRuntimeDir 'python312'
$bundledPythonExe = Join-Path $bundledPythonDir 'python.exe'
```

- [ ] **Step 3: 添加辅助函数**

在 `Write-Host '====...'` 横幅之前添加 `Test-Python312`、`Find-HostPython312`、`Get-BundledPython`、`Install-BundledPython`。

实现要求：

- `Test-Python312` 用 `& $Exe -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))" 2>$null`，仅当退出码 0 且结果为 `3.12` 时返回 `$true`。
- `Find-HostPython312` 先用 `Get-Command py.exe`，执行 `& $py.Source -3.12 -c "import sys; print(sys.executable)" 2>$null`；成功且路径存在则返回。再检查 `Get-Command python.exe`，用 `Test-Python312` 验证；通过则返回其 `Source`。否则返回 `$null`。
- `Get-BundledPython` 先判断 `$bundledPythonExe` 是否存在；存在但 `Test-Python312` 失败时 `Stop-Check "Bundled Python runtime is invalid: $bundledPythonExe`nDelete runtime\python312 and retry."`。
- `Install-BundledPython`：
  - 检查 `[Environment]::Is64BitOperatingSystem`；非 64 位时 `Stop-Check`（x64 固定资产）。
  - 解析 `%SystemRoot%\System32\tar.exe`；不存在时回退 `Get-Command tar.exe`，仍不存在则 `Stop-Check`。
  - 在 `$bundledRuntimeDir` 下创建唯一临时目录 `.tmp-python312-<guid>`，下载到临时目录的 `python312.tar.gz`。
  - 下载使用 `Invoke-WebRequest -UseBasicParsing -OutFile`；先设置 TLS 1.2；`$ProgressPreference = 'SilentlyContinue'`；`HTTPS_PROXY` 优先、其次 `HTTP_PROXY`，存在时传 `-Proxy`。
  - 下载后 `Get-FileHash -Algorithm SHA256`，转小写后与 `$bundledPythonSha256` 比较；不匹配立即失败。
  - 解压：`& $tarExe -xzf $archive -C $extractDir --strip-components=1`；检查 `$LASTEXITCODE`。
  - 验证 `$extractDir\python.exe` 为 3.12；失败则 `Stop-Check`。
  - 若 `$bundledPythonDir` 已存在：`Stop-Check "Bundled Python directory already exists but is not usable: ...`nDelete it and retry."`；否则 `Move-Item` 临时解压目录到 `$bundledPythonDir`。
  - 成功输出 `Write-Ok "Python runtime: $bundledPythonExe"` 并返回路径。
  - 任何失败路径先清理临时目录，再 `Stop-Check`；成功移动后清理外层临时目录。

- [ ] **Step 4: 改写 venv 创建分支**

把现有从 `if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {` 到 venv 版本检查之前的创建逻辑，替换为：

```powershell
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    if ($CheckOnly) {
        Stop-Check "NoneBot virtual environment is missing: $python"
    }
    $hostPython = Get-BundledPython
    if (-not $hostPython) {
        $hostPython = Find-HostPython312
    }
    if (-not $hostPython) {
        if ($SkipInstall) {
            Stop-Check 'Python 3.12 is unavailable, and -SkipInstall prevents the runtime download. Install Python 3.12 or rerun without -SkipInstall.'
        }
        Write-Section 'No system Python 3.12 found; downloading the pinned python-build-standalone runtime ...'
        $hostPython = Install-BundledPython
    }
    & $hostPython -m venv $venvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
        Stop-Check "Could not create the NoneBot venv with $hostPython (exit $LASTEXITCODE)."
    }
}
```

保留后面的 `pyvenv.cfg`、venv 版本必须为 3.12、SnowLuma 检查、pip 安装/修复逻辑不变。

- [ ] **Step 5: 更新 `.gitignore`**

在依赖/缓存区添加：

```gitignore
# Windows 一键启动首次运行下载的 Python 3.12 运行时
/runtime/
```

- [ ] **Step 6: 语法检查**

```powershell
powershell -NoProfile -Command "[void][scriptblock]::Create((Get-Content -Raw 'D:\Software\qqbot\.tmp\wt-python-bootstrap\tools\startup-preflight.ps1'))"
```

Expected: 无输出，退出码 0。

- [ ] **Step 7: 首次下载测试**

确认 worktree 无 `nonebot\.venv`、无 `runtime\python312`，`py -3.12` 不可用（本机现状），运行：

```powershell
cd D:\Software\qqbot\.tmp\wt-python-bootstrap
QQBOT_NO_PAUSE=1 powershell -NoProfile -ExecutionPolicy Bypass -Command "& 'D:\Software\qqbot\.tmp\wt-python-bootstrap\tools\启动所有.bat' --no-pause; exit $LASTEXITCODE"
```

Expected:

- 出现 `No system Python 3.12 found` 和下载/校验/解压日志；
- `runtime\python312\python.exe -V` 为 `Python 3.12.13`；
- `nonebot\.venv\Scripts\python.exe -V` 为 `Python 3.12.13`；
- preflight 继续并停在 `SnowLuma runtime is missing`；
- 退出码 4；
- 8080/5099/8091/8765 无监听；
- `git status --porcelain -uall` 为空。

- [ ] **Step 8: 缓存复用测试**

再次运行 Step 7 的命令。Expected: 不再出现下载日志，退出码仍为 4，`runtime\python312` 不被重复解压。

- [ ] **Step 9: `-SkipInstall` 与 `-CheckOnly` 测试**

在一个干净副本中（可临时复制 worktree 到新目录，或删除 `runtime` 与 `nonebot\.venv`）运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\startup-preflight.ps1 -Root <clean-root> -SkipInstall -NoPause
powershell -NoProfile -ExecutionPolicy Bypass -File tools\startup-preflight.ps1 -Root <clean-root> -CheckOnly -NoPause
```

Expected: 均退出码 1；`-SkipInstall` 输出 `-SkipInstall prevents the runtime download`；`-CheckOnly` 输出 `NoneBot virtual environment is missing`；两者都不创建 `runtime\python312`。

- [ ] **Step 10: 提交**

```bash
git add tools/startup-preflight.ps1 .gitignore
git commit -m "fix(startup): bootstrap pinned Python 3.12 on Windows"
```

### Task 2: 文档、GitHub 干净 clone 验收与 PR

**Files:**
- Modify: `README.md`（Windows 本机开发段落）
- Modify: `docs/OPERATIONS.md`（附录 Windows 单机）
- Modify: `CHANGELOG.md`（`[未发布]` 下新增「修复与加固」）
- Test: 从 GitHub clone 的 `feature/windows-python-bootstrap` 干净目录

**Interfaces:**
- Consumes: Task 1 的 `runtime\python312`、`-SkipInstall` / `-CheckOnly` 行为。
- Produces: 用户可见的首次下载说明、版本/缓存位置、删除重下方法和 PR 验收记录。

- [ ] **Step 1: 更新 README**

在 `## Windows 本机（开发）` 段落的 preflight 说明后补充：

> 如果系统没有可用的 Python 3.12，脚本会从 python-build-standalone 下载固定的 3.12.13（约 21 MB，SHA256 校验）到 `runtime\python312`，再创建 `nonebot\.venv`；`-CheckOnly` / `-SkipInstall` 不会下载。删除该目录可强制重新下载。

- [ ] **Step 2: 更新 OPERATIONS**

在附录 Windows 单机的 preflight 条目后补充：

> `nonebot\.venv` 不存在且系统没有 3.12 时，preflight 下载 python-build-standalone 3.12.13 到 `runtime\python312`（约 21 MB，固定 SHA256），再创建 venv；`-SkipInstall` / `-CheckOnly` 不下载。该目录被 `.gitignore` 忽略，删除后下次启动重新下载。

- [ ] **Step 3: 更新 CHANGELOG**

在 `## [未发布]` 下新增：

```markdown
### 修复与加固

- Windows 一键启动：系统没有可用的 Python 3.12 时，首次运行自动下载并校验 python-build-standalone 3.12.13 到 `runtime\python312`，再创建 `nonebot\.venv`；`-CheckOnly` / `-SkipInstall` 不下载。
```

- [ ] **Step 4: 提交文档**

```bash
git add README.md docs/OPERATIONS.md CHANGELOG.md
git commit -m "docs: describe Windows Python runtime bootstrap"
```

- [ ] **Step 5: 推送工作分支**

```bash
git push -u origin feature/windows-python-bootstrap
```

- [ ] **Step 6: 从 GitHub 干净 clone 验收**

```powershell
cd D:\Software
Remove-Item -Recurse -Force qqbot-bootstrap-test -ErrorAction SilentlyContinue
git clone -b feature/windows-python-bootstrap https://github.com/Lkyloveaw/denia-bot.git qqbot-bootstrap-test
```

确认无 `nonebot\.venv`、无 `runtime\python312`、无 `snowluma`，然后运行一键启动：

```powershell
cd D:\Software\qqbot-bootstrap-test
QQBOT_NO_PAUSE=1 powershell -NoProfile -ExecutionPolicy Bypass -Command "& 'D:\Software\qqbot-bootstrap-test\tools\启动所有.bat' --no-pause; exit $LASTEXITCODE"
```

Expected: 下载/校验/解压成功；`runtime\python312\python.exe -V` 为 3.12.13；venv 创建成功；停在 `SnowLuma runtime is missing`；退出码 4；四个端口无监听；`git status --porcelain -uall` 为空。

- [ ] **Step 7: 验证 skip/check-only 和缓存**

在同一 clone 里：

- 再运行一次 Step 6，确认不重复下载，仍退出 4；
- 删除 `runtime\python312` 和 `nonebot\.venv` 后，运行 `-SkipInstall` 和 `-CheckOnly`，确认均退出 1 且不创建 `runtime\python312`；
- 记录结果供 PR body 使用。

- [ ] **Step 8: 创建 PR**

```bash
gh pr create --base main --head feature/windows-python-bootstrap \
  --title "fix(startup): bootstrap pinned Python 3.12 on Windows" \
  --body "..."
```

PR body 必须包含：变更摘要、固定版本/URL/SHA256、验证命令与结果、已知限制（首次需联网；干净 clone 仍缺 SnowLuma；仅 x64）。

- [ ] **Step 9: 报告 PR 链接**

把 PR URL 和验证结果发给用户；不自行合并。

## Self-Review

- Spec coverage: 固定资产、发现顺序、下载/校验/解压、`-SkipInstall`/`-CheckOnly`、`.gitignore`、文档、干净 clone 验收均有对应任务。
- Step scan: 每个步骤有明确命令或精确签名；下载/解压算法给出了不可由签名推断的关键参数（TLS、代理、tar 参数、临时目录、原子移动）。
- Type consistency: 函数名和常量名在 Task 1 的 Interfaces 与步骤中一致；Task 2 引用相同行为。
- Review Focus: 网络失败、哈希不匹配、缓存损坏、skip/check-only、非 x64/tar、并发、SnowLuma 预期失败均有测试或明确验收项。
- Proportion: 计划聚焦一个脚本和一个运行时下载器，文档步骤为独立可审阅单元。
