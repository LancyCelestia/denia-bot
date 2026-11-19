@echo off
setlocal EnableExtensions
rem QQBOT-ENTRY-STOP-v1
rem ---------------------------------------------------------------------
rem  QQ bot stop (SnowLuma + NoneBot2/MuiceBot + RAG + gsuid_core)
rem  Root comes from %~dp0, so any checkout path works. No drive letters.
rem  Only processes that belong to this checkout are stopped: the command
rem  line must contain this repo path. Unrelated node.exe / python.exe are
rem  never touched.
rem  Keep this file ASCII-only: cmd.exe misparses non-ASCII text in long
rem  UTF-8 batch files and then runs fragments of lines as commands.
rem ---------------------------------------------------------------------

title QQ Bot - Stop

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "LOG=%ROOT%\logs"

echo ==========================================
echo   QQ bot stop (SnowLuma + NoneBot2 + gsuid_core)
echo ==========================================
echo   Repo root: %ROOT%
echo.

if not exist "%ROOT%\nonebot\bot.py" (
    echo [!!] This does not look like a qqbot checkout: %ROOT%
    exit /b 2
)

set "QQBOT_ROOT=%ROOT%"
set "QQBOT_PID_FILE=%LOG%\gsuid-core.pid"

rem Command lines and process trees need PowerShell; batch cannot read them.
rem Statements are accumulated into one -Command string, joined with ';'.
set "PS_STOP=$ErrorActionPreference='Continue';"
set "PS_STOP=%PS_STOP% $root=$env:QQBOT_ROOT;"
set "PS_STOP=%PS_STOP% $sep=$root.TrimEnd('\')+'\';"
set "PS_STOP=%PS_STOP% function Test-Own([string]$cl) { if (-not $cl) { return $false }; return ($cl.IndexOf($sep,[StringComparison]::OrdinalIgnoreCase) -ge 0) };"
set "PS_STOP=%PS_STOP% $killed=0;"
set "PS_STOP=%PS_STOP% $pf=$env:QQBOT_PID_FILE;"

rem gsuid_core: stop the whole tree by logs\gsuid-core.pid. The command line
rem must also contain the repo path and 'gsuid', so a reused PID cannot make
rem us kill an unrelated process.
set "PS_STOP=%PS_STOP% if (Test-Path -LiteralPath $pf) {"
set "PS_STOP=%PS_STOP%   $raw=@(Get-Content -LiteralPath $pf -ErrorAction SilentlyContinue)[0];"
set "PS_STOP=%PS_STOP%   if ($raw -match '^\s*(\d+)\s*$') {"
set "PS_STOP=%PS_STOP%     $gp=[int]$Matches[1];"
set "PS_STOP=%PS_STOP%     $g=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $gp) -ErrorAction SilentlyContinue;"
set "PS_STOP=%PS_STOP%     if ($g -and (Test-Own $g.CommandLine) -and ($g.CommandLine -match 'gsuid')) {"
set "PS_STOP=%PS_STOP%       & taskkill.exe /PID $gp /T /F 2>$null | Out-Null;"
set "PS_STOP=%PS_STOP%       if ($LASTEXITCODE -eq 0) { Write-Output ('  stopped gsuid_core (PID ' + $gp + ')'); $killed++ }"
set "PS_STOP=%PS_STOP%     }"
set "PS_STOP=%PS_STOP%   }"
set "PS_STOP=%PS_STOP% };"

rem Other services: stop only when the command line contains this repo path
rem AND one of this project's entry points (bot.py / serve.py / gsuid /
rem snowluma on node.exe).
set "PS_STOP=%PS_STOP% $targets=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessId -ne $PID -and (Test-Own $_.CommandLine) -and (((($_.Name -eq 'python.exe') -or ($_.Name -eq 'pythonw.exe')) -and ($_.CommandLine -match '(?i)(bot\.py|serve\.py|gsuid_core|gsuid[\\/]core)')) -or (($_.Name -eq 'node.exe') -and ($_.CommandLine -match '(?i)snowluma'))) });"

set "PS_STOP=%PS_STOP% foreach ($t in $targets) {"
set "PS_STOP=%PS_STOP%   $l='NoneBot2';"
set "PS_STOP=%PS_STOP%   if ($t.CommandLine -match '(?i)snowluma') { $l='SnowLuma' } elseif ($t.CommandLine -match '(?i)gsuid') { $l='gsuid_core' } elseif ($t.CommandLine -match '(?i)serve\.py') { $l='RAG' };"
set "PS_STOP=%PS_STOP%   try { Stop-Process -Id $t.ProcessId -Force -ErrorAction Stop; Write-Output ('  stopped ' + $l + ' (PID ' + $t.ProcessId + ')'); $killed++ } catch { }"
set "PS_STOP=%PS_STOP% };"
set "PS_STOP=%PS_STOP% if ($killed -eq 0) { Write-Output '  no matching process found' } else { Write-Output ('  stopped ' + $killed + ' process(es)') }"

powershell -NoProfile -ExecutionPolicy Bypass -Command "%PS_STOP%"
set "RC=%ERRORLEVEL%"

rem Always drop the PID file: a stale one would mislead the next stop
if exist "%QQBOT_PID_FILE%" del /q "%QQBOT_PID_FILE%" >nul 2>&1

if not "%RC%"=="0" (
    echo.
    echo [!!] The stop helper failed; some processes may need manual cleanup.
    exit /b %RC%
)

echo.
echo Done. SnowLuma stops hooking QQ; the QQ client itself is unaffected.
exit /b 0
