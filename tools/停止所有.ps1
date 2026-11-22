# =====================================================================
#  QQ 机器人一键停止（SnowLuma + NoneBot2/MuiceBot）
# =====================================================================

$ErrorActionPreference = 'Continue'

$ROOT = Split-Path -Parent $PSScriptRoot
$snowHome = "$ROOT\snowluma"
$snowHomeFile = "$ROOT\logs\snowluma-home.txt"
if (Test-Path -LiteralPath $snowHomeFile -PathType Leaf) {
    $snowHome = ([IO.File]::ReadAllText($snowHomeFile, [Text.Encoding]::UTF8)).Trim()
}
$snowNode = Join-Path $snowHome 'node.exe'

Write-Host "正在停止 QQ 机器人..." -ForegroundColor Cyan
$killed = 0

# gsuid_core（可选）
$gsuidPidFile = "$ROOT\logs\gsuid-core.pid"
if (Test-Path -LiteralPath $gsuidPidFile) {
    $gsuidPid = (Get-Content -LiteralPath $gsuidPidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($gsuidPid -match '^\d+$') {
        $gsuidProc = Get-CimInstance Win32_Process -Filter "ProcessId=$gsuidPid" -ErrorAction SilentlyContinue
        if ($gsuidProc -and $gsuidProc.CommandLine -match 'gsuid') {
            & taskkill.exe /PID $gsuidPid /T /F *> $null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  已停止 gsuid_core (PID $gsuidPid)" -ForegroundColor Green
                $killed++
            }
        }
    }
    Remove-Item -LiteralPath $gsuidPidFile -Force -ErrorAction SilentlyContinue
}

# 兜底：按命令行清理 uv/python 子进程（PID 文件丢失或父进程已退出时）
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -and
        $_.CommandLine -match 'gsuid[\\/]core'
    } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  已停止 gsuid_core 子进程 (PID $($_.ProcessId))" -ForegroundColor Green
        $killed++
    }

# NoneBot2
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'bot\.py' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  已停止 NoneBot2 (PID $($_.ProcessId))" -ForegroundColor Green
        $killed++
    }

# RAG 检索服务
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'serve\.py' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  已停止 RAG 检索服务 (PID $($_.ProcessId))" -ForegroundColor Green
        $killed++
    }

# SnowLuma
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -eq 'node.exe' -and $_.CommandLine -and (
            $_.CommandLine -match 'snowluma' -or
            $_.CommandLine.IndexOf($snowNode, [StringComparison]::OrdinalIgnoreCase) -ge 0
        )
    } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  已停止 SnowLuma (PID $($_.ProcessId))" -ForegroundColor Green
        $killed++
    }

if ($killed -eq 0) { Write-Host "  没有在运行的服务" -ForegroundColor Yellow }

Start-Sleep -Seconds 3
Write-Host ""
Write-Host "完成。已停止 $killed 个进程。" -ForegroundColor Cyan
Write-Host "注意：SnowLuma 停止后不会再 hook QQ；QQ 客户端本身不受影响。" -ForegroundColor DarkGray
