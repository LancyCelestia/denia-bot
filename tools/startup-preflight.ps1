# Keep this file UTF-8 with BOM: Windows PowerShell 5.1 needs it for Chinese text.
[CmdletBinding()]
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [switch]$NoPause,
    [switch]$SkipInstall,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$rootPath = [IO.Path]::GetFullPath($Root)
$botDir = Join-Path $rootPath 'nonebot'
$venvDir = Join-Path $botDir '.venv'
$python = Join-Path $venvDir 'Scripts\python.exe'
$requirements = Join-Path $rootPath 'requirements.lock'
$snowNode = Join-Path $rootPath 'snowluma\node.exe'
$snowEntry = Join-Path $rootPath 'snowluma\index.mjs'
$ragIndex = Join-Path $rootPath 'rag\index\vectors.npy'
$ragPython = Join-Path $rootPath 'rag\.venv\Scripts\python.exe'
if (Test-Path -LiteralPath (Join-Path $rootPath 'rag\.venv-gpu\Scripts\python.exe')) {
    $ragPython = Join-Path $rootPath 'rag\.venv-gpu\Scripts\python.exe'
}
$gsuidPython = Join-Path $rootPath 'gsuid\core\.venv\Scripts\python.exe'
$pluginSettings = Join-Path $botDir 'data\plugins.json'

function Write-Section($Message) {
    Write-Host "[deps] $Message" -ForegroundColor Cyan
}

function Write-Ok($Message) {
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-Warn($Message) {
    Write-Host "  [--] $Message" -ForegroundColor Yellow
}

function Stop-Check($Message) {
    Write-Host "  [!!] $Message" -ForegroundColor Red
    if (-not $NoPause -and -not $env:QQBOT_NO_PAUSE -and [Environment]::UserInteractive) {
        Write-Host ''
        [void](Read-Host '按 Enter 关闭窗口')
    }
    exit 1
}

function Invoke-PipInstall {
    Write-Section '安装/补齐 requirements.lock 依赖，完成前不会启动任何服务 ...'
    & $python -m pip install --disable-pip-version-check --no-input -r $requirements
    if ($LASTEXITCODE -ne 0) {
        Stop-Check "pip install 失败（退出码 $LASTEXITCODE），未启动任何服务。"
    }
    Set-Content -LiteralPath $marker -Value $requirementsHash -NoNewline -Encoding ascii
}

trap {
    Write-Host "  [!!] 依赖检查异常: $($_.Exception.Message)" -ForegroundColor Red
    if (-not $NoPause -and -not $env:QQBOT_NO_PAUSE -and [Environment]::UserInteractive) {
        Write-Host ''
        [void](Read-Host '按 Enter 关闭窗口')
    }
    exit 1
}

Write-Host '==========================================' -ForegroundColor Cyan
Write-Host '  启动前依赖检查' -ForegroundColor Cyan
Write-Host '==========================================' -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath (Join-Path $botDir 'bot.py') -PathType Leaf)) {
    Stop-Check "不是有效检出：缺少 $botDir\bot.py"
}
if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
    Stop-Check "缺少依赖锁文件：$requirements"
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    if ($CheckOnly) {
        Stop-Check "NoneBot 虚拟环境不存在：$python"
    }
    Write-Section 'NoneBot 虚拟环境不存在，尝试用 Python 3.12 创建 ...'
    $created = $false
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        & $py.Source -3.12 -m venv $venvDir
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $python -PathType Leaf)) {
            $created = $true
        }
    }
    if (-not $created) {
        $pythonCmd = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($pythonCmd) {
            $versionOutput = & $pythonCmd.Source -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))" 2>&1
            $versionText = ($versionOutput -join "`n").Trim()
            if ($LASTEXITCODE -eq 0 -and $versionText -eq '3.12') {
                & $pythonCmd.Source -m venv $venvDir
                if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $python -PathType Leaf)) {
                    $created = $true
                }
            }
        }
    }
    if (-not $created) {
        Stop-Check '无法创建 NoneBot 虚拟环境。请安装 Python 3.12 后运行 py -3.12 -m venv nonebot\.venv。'
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $venvDir 'pyvenv.cfg') -PathType Leaf)) {
    Stop-Check "NoneBot 虚拟环境不完整（缺少 pyvenv.cfg）：$venvDir"
}
Write-Ok "NoneBot Python: $python"

if (-not (Test-Path -LiteralPath $snowNode -PathType Leaf)) {
    Stop-Check "SnowLuma 运行时缺失：$snowNode"
}
if (-not (Test-Path -LiteralPath $snowEntry -PathType Leaf)) {
    Stop-Check "SnowLuma 入口缺失：$snowEntry"
}
Write-Ok "SnowLuma runtime: $snowNode"

if ($CheckOnly) {
    Write-Ok '文件检查通过（CheckOnly，未执行 pip/import）'
    exit 0
}

& $python -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
    if ($SkipInstall) {
        Stop-Check 'pip 不可用，且当前指定了 -SkipInstall；未启动任何服务。'
    }
    Write-Section 'pip 不可用，尝试 ensurepip ...'
    & $python -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) {
        Stop-Check "pip 不可用（退出码 $LASTEXITCODE），未启动任何服务。"
    }
}
Write-Ok 'pip 可用'

$requirementsHash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash.ToLowerInvariant()
$marker = Join-Path $venvDir '.qqbot-requirements.sha256'
$markerHash = ''
if (Test-Path -LiteralPath $marker -PathType Leaf) {
    $markerText = Get-Content -LiteralPath $marker -Raw
    if ($null -ne $markerText) {
        $markerHash = $markerText.Trim().ToLowerInvariant()
    }
}
$needsInstall = $markerHash -ne $requirementsHash

if ($needsInstall) {
    if ($SkipInstall) {
        Write-Warn 'requirements.lock 已变化，但当前指定了 -SkipInstall；继续检查现有环境'
    } else {
        Invoke-PipInstall
    }
}

$pipCheck = & $python -m pip check 2>&1
if ($LASTEXITCODE -ne 0) {
    if ($SkipInstall) {
        Stop-Check "pip check 未通过（-SkipInstall 禁止修复）：`n$pipCheck"
    }
    Write-Warn 'pip check 未通过，尝试按 requirements.lock 修复 ...'
    Invoke-PipInstall
    $pipCheck = & $python -m pip check 2>&1
    if ($LASTEXITCODE -ne 0) {
        Stop-Check "依赖冲突仍存在：`n$pipCheck"
    }
}
Write-Ok 'pip check 通过'

function Invoke-ImportCheck {
    Push-Location -LiteralPath $botDir
    try {
        & $python -c "import nonebot, importlib.util; assert importlib.util.find_spec('muicebot') is not None" 2>&1
    } finally {
        Pop-Location
    }
}

$importCheck = Invoke-ImportCheck
if ($LASTEXITCODE -ne 0) {
    if ($SkipInstall) {
        Stop-Check "NoneBot/MuiceBot 导入检查失败（-SkipInstall 禁止修复）：`n$importCheck"
    }
    Write-Warn 'NoneBot/MuiceBot 导入检查失败，尝试按 requirements.lock 修复 ...'
    Invoke-PipInstall
    $importCheck = Invoke-ImportCheck
    if ($LASTEXITCODE -ne 0) {
        Stop-Check "NoneBot/MuiceBot 仍无法导入：`n$importCheck"
    }
}
Write-Ok 'nonebot 可导入，muicebot 已安装'

if (Test-Path -LiteralPath $ragIndex -PathType Leaf) {
    if (Test-Path -LiteralPath $ragPython -PathType Leaf) {
        Write-Ok 'RAG 可选服务：索引和虚拟环境均已就绪'
    } else {
        Write-Warn "RAG 索引存在，但虚拟环境缺失：$ragPython（启动时会跳过 RAG）"
    }
} else {
    Write-Warn 'RAG 可选服务未配置（rag\index\vectors.npy 不存在，启动时会跳过）'
}

$gsuidEnabled = $true
if (Test-Path -LiteralPath $pluginSettings -PathType Leaf) {
    try {
        $pluginData = Get-Content -LiteralPath $pluginSettings -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($pluginData.disabled -contains 'gsuid') {
            $gsuidEnabled = $false
        }
    } catch {
        Write-Warn "无法读取插件开关：$pluginSettings（按 gsuid 启用处理）"
    }
}
if (-not $gsuidEnabled) {
    Write-Warn 'gsuid_core 可选服务：已由插件管理关闭'
} elseif (Test-Path -LiteralPath $gsuidPython -PathType Leaf) {
    Write-Ok 'gsuid_core 可选服务：虚拟环境已就绪'
} else {
    Write-Warn "gsuid_core 可选服务未安装：$gsuidPython（启动时会跳过）"
}

foreach ($configFile in @((Join-Path $botDir '.env'), (Join-Path $botDir 'configs\models.yml'))) {
    if (-not (Test-Path -LiteralPath $configFile -PathType Leaf)) {
        Write-Warn "配置缺失：$configFile（机器人可能无法正常启动）"
    }
}

Write-Host ''
Write-Host '依赖检查完成，开始启动服务。' -ForegroundColor Green
exit 0
