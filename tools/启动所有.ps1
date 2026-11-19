# =====================================================================
#  QQ 机器人一键启动（SnowLuma + NoneBot2/MuiceBot）
#  启动顺序：先 NoneBot（WebSocket 服务端），再 SnowLuma（连出去）
# =====================================================================

[CmdletBinding()]
param(
    [switch]$NoPause
)

$ErrorActionPreference = 'Continue'
$script:StartupFailed = $false

trap {
    Write-Host "[!!] 启动异常: $($_.Exception.Message)" -ForegroundColor Red
    if (-not $NoPause -and -not $env:QQBOT_NO_PAUSE -and [Environment]::UserInteractive) {
        Write-Host ''
        [void](Read-Host '按 Enter 关闭窗口')
    }
    exit 1
}

$ROOT       = Split-Path -Parent $PSScriptRoot
$SNOWLUMA   = "$ROOT\snowluma\node.exe"
$NONE_LUA   = "$ROOT\nonebot\.venv\Scripts\python.exe"
$LOG        = "$ROOT\logs"
$GSUID_CORE = "$ROOT\gsuid\core"
$GSUID_PY   = "$GSUID_CORE\.venv\Scripts\python.exe"

# 否则 Python 在 Windows 上重定向输出时会用 GBK，日志里的中文会变成乱码
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

# 依赖检查必须先完成；失败时不启动任何服务。
$preflight = Join-Path $PSScriptRoot 'startup-preflight.ps1'
if (-not (Test-Path -LiteralPath $preflight -PathType Leaf)) {
    throw "缺少依赖检查脚本：$preflight"
}
& $preflight -Root $ROOT -NoPause
if ($LASTEXITCODE -ne 0) {
    throw '依赖检查未通过，未启动任何服务。'
}

function Get-PrivateValue($Key) {
    foreach ($file in @("$ROOT\local\bot.env", "$ROOT\nonebot\.env")) {
        if (-not (Test-Path -LiteralPath $file)) { continue }
        foreach ($line in Get-Content -LiteralPath $file -Encoding UTF8) {
            if ($line -match "^\s*$([regex]::Escape($Key))\s*=\s*(.*?)\s*$") {
                return $Matches[1].Trim().Trim('"').Trim("'")
            }
        }
    }
    return [Environment]::GetEnvironmentVariable($Key)
}
$BotUin = Get-PrivateValue 'QQBOT_SELF_ID'
if ($BotUin) { $env:QQBOT_SELF_ID = $BotUin }

# RAG 服务用哪个 venv：索引是 GPU(fp16) 建的，查询向量就必须也走 CUDA，
# 否则就是拿 fp32 模型去比 fp16 向量。有 .venv-gpu 就用它，没有退回 .venv。
$RAG_PY     = "$ROOT\rag\.venv\Scripts\python.exe"
if (Test-Path "$ROOT\rag\.venv-gpu\Scripts\python.exe") {
    $RAG_PY = "$ROOT\rag\.venv-gpu\Scripts\python.exe"
}

New-Item -ItemType Directory -Force -Path $LOG | Out-Null

function Test-Listening($port) {
    [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

function Get-LanIPv4 {
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
        Select-Object -ExpandProperty IPAddress -Unique
}

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  QQ 机器人启动 (SnowLuma + NoneBot2 + gsuid_core)" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# ---------- 1. RAG 检索服务 ----------
$ragExpected = (Test-Path "$ROOT\rag\index\vectors.npy") -and (Test-Path $RAG_PY)
Write-Host "[1/5] RAG 检索服务 (8091) ..." -NoNewline
if (Test-Listening 8091) {
    Write-Host " 已在运行" -ForegroundColor Yellow
} elseif (-not (Test-Path "$ROOT\rag\index\vectors.npy")) {
    Write-Host " [--] 索引不存在，跳过（可选）" -ForegroundColor Yellow
} elseif (-not (Test-Path $RAG_PY)) {
    Write-Host " [--] 虚拟环境不存在，跳过（可选）" -ForegroundColor Yellow
} else {
    Start-Process -FilePath $RAG_PY -ArgumentList 'serve.py' `
        -WorkingDirectory "$ROOT\rag" `
        -RedirectStandardOutput "$LOG\rag.out.log" `
        -RedirectStandardError  "$LOG\rag.err.log" `
        -WindowStyle Hidden
    Write-Host " 已启动" -ForegroundColor Green
}

# ---------- 2. gsuid_core（可选游戏查询）----------
$gsuidEnabled = $true
$pluginSettings = "$ROOT\nonebot\data\plugins.json"
if (Test-Path -LiteralPath $pluginSettings) {
    try {
        $pluginData = Get-Content -LiteralPath $pluginSettings -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($pluginData.disabled -contains 'gsuid') { $gsuidEnabled = $false }
    } catch {
        $gsuidEnabled = $true
    }
}
Write-Host "[2/5] gsuid_core (8765) ..." -NoNewline
if (-not $gsuidEnabled) {
    Write-Host " [--] 已由插件管理关闭" -ForegroundColor Yellow
} elseif (Test-Listening 8765) {
    Write-Host " 已在运行" -ForegroundColor Yellow
} elseif (-not (Test-Path $GSUID_PY)) {
    Write-Host " [!!] 未安装，先运行 gsuid\install.ps1" -ForegroundColor Yellow
} else {
    $coreProc = Start-Process -FilePath $GSUID_PY `
        -ArgumentList '-m', 'gsuid_core.core', '--host', '0.0.0.0' `
        -WorkingDirectory $GSUID_CORE `
        -RedirectStandardOutput "$LOG\gsuid.out.log" `
        -RedirectStandardError  "$LOG\gsuid.err.log" `
        -WindowStyle Hidden -PassThru
    if ($coreProc) {
        Set-Content -LiteralPath "$LOG\gsuid-core.pid" -Value $coreProc.Id -Encoding ascii
        Write-Host " 已启动" -ForegroundColor Green
    } else {
        Write-Host " 启动失败" -ForegroundColor Red
    }
}

# ---------- 3. NoneBot2 + MuiceBot ----------
Write-Host "[3/5] NoneBot2 + MuiceBot ..." -NoNewline
if (Test-Listening 8080) {
    Write-Host " 已在运行" -ForegroundColor Yellow
} else {
    Start-Process -FilePath $NONE_LUA -ArgumentList 'bot.py' `
        -WorkingDirectory "$ROOT\nonebot" `
        -RedirectStandardOutput "$LOG\nonebot.out.log" `
        -RedirectStandardError  "$LOG\nonebot.err.log" `
        -WindowStyle Hidden
    Write-Host " 已启动" -ForegroundColor Green}

# ---------- 4. SnowLuma ----------
Write-Host "[4/5] SnowLuma (QQ 协议端) ..." -NoNewline
if (Test-Listening 5099) {
    Write-Host " 已在运行" -ForegroundColor Yellow
} else {
    Start-Process -FilePath $SNOWLUMA -ArgumentList 'index.mjs' `
        -WorkingDirectory "$ROOT\snowluma" `
        -RedirectStandardOutput "$LOG\snowluma.out.log" `
        -RedirectStandardError  "$LOG\snowluma.err.log" `
        -WindowStyle Hidden
    Write-Host " 已启动" -ForegroundColor Green
}

# ---------- 5. 等待各端就绪 ----------
Write-Host "[5/5] 等待服务就绪 ..." -NoNewline
$ok1 = $false; $ok2 = $false; $ok3 = $false; $ok4 = $false
$coreExpected = $gsuidEnabled -and (Test-Path $GSUID_PY)
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    Write-Host "." -NoNewline
    if (-not $ok1) { $ok1 = Test-Listening 8080 }
    if (-not $ok2) { $ok2 = Test-Listening 5099 }
    if (-not $ok3) { $ok3 = Test-Listening 8091 }
    if ($coreExpected -and -not $ok4) { $ok4 = Test-Listening 8765 }
    if ($ok1 -and $ok2 -and ($ok3 -or -not $ragExpected) -and ($ok4 -or -not $coreExpected)) { break }
}
Write-Host ""
Write-Host ""

if (-not $ok1 -or -not $ok2) { $script:StartupFailed = $true }

if ($ok1) { Write-Host "  [OK] NoneBot2          : http://127.0.0.1:8080" -ForegroundColor Green }
else      { Write-Host "  [!!] NoneBot2 未就绪，看日志: $LOG\nonebot.out.log" -ForegroundColor Red }

if ($ok2) { Write-Host "  [OK] SnowLuma WebUI    : http://127.0.0.1:5099" -ForegroundColor Green }
else      { Write-Host "  [!!] SnowLuma 未就绪，看日志: $LOG\snowluma.out.log" -ForegroundColor Red }

if ($ok3) { Write-Host "  [OK] RAG 检索服务      : http://127.0.0.1:8091" -ForegroundColor Green }
elseif (-not $ragExpected) { Write-Host "  [--] RAG 未启用（可选）" -ForegroundColor Yellow }
else      { Write-Host "  [!!] RAG 未就绪，看日志: $LOG\rag.out.log" -ForegroundColor Red }

if ($ok4) {
    Write-Host "  [OK] gsuid_core WebUI  : http://127.0.0.1:8765/app" -ForegroundColor Green
    foreach ($ip in @(Get-LanIPv4)) {
        Write-Host "  [LAN] gsuid_core       : http://${ip}:8765/app" -ForegroundColor Green
    }
} elseif (-not (Test-Path $GSUID_PY)) {
    Write-Host "  [--] gsuid_core 未安装（可选）：bash gsuid/install.sh 或 gsuid\install.ps1" -ForegroundColor Yellow
} else {
    Write-Host "  [!!] gsuid_core 未就绪，看日志: $LOG\gsuid.out.log" -ForegroundColor Red
}

# ---------- 5. 收敛注入账号：只保留小号 ----------
Write-Host ""
Write-Host "收敛 QQ 注入账号（只保留 $BotUin）..." -ForegroundColor Cyan
if ($ok2 -and $BotUin) {
    Start-Sleep -Seconds 8   # 等 SnowLuma 完成自动注入
    & "$ROOT\nonebot\.venv\Scripts\python.exe" "$ROOT\tools\只注入小号.py"
} elseif ($ok2) {
    Write-Host "  未配置 QQBOT_SELF_ID（local/bot.env 或 nonebot/.env），跳过注入" -ForegroundColor Yellow
} else {
    Write-Host "  SnowLuma 未就绪，跳过" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "---------------- 连接信息 ----------------" -ForegroundColor DarkGray
Write-Host "  OneBot 反向 WS : ws://127.0.0.1:8080/onebot/v11/ws" -ForegroundColor DarkGray
Write-Host "  机器人 QQ      : $BotUin" -ForegroundColor DarkGray
if ($ok4) {
    Write-Host "  gsuid_core     : http://127.0.0.1:8765/app（本机）" -ForegroundColor DarkGray
    foreach ($ip in @(Get-LanIPv4)) {
        Write-Host "  gsuid_core LAN : http://${ip}:8765/app（需先放行防火墙 8765）" -ForegroundColor DarkGray
    }
}
Write-Host "  停止服务请运行 : 停止机器人.bat" -ForegroundColor DarkGray
Write-Host ""

if ($script:StartupFailed) {
    if (-not $NoPause -and -not $env:QQBOT_NO_PAUSE -and [Environment]::UserInteractive) {
        Write-Host ''
        [void](Read-Host '启动未完全成功，按 Enter 关闭窗口')
    }
    exit 1
}
exit 0
