@echo off
if /i "%~1"=="--no-pause" goto :run
if defined QQBOT_NO_PAUSE goto :run
cmd /c ""%~f0" --no-pause"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    pause
)
exit /b %RC%

:run
setlocal EnableExtensions
rem QQBOT-ENTRY-START-v1
rem ---------------------------------------------------------------------
rem  QQ bot start (SnowLuma + NoneBot2/MuiceBot + RAG + gsuid_core)
rem  Root comes from %~dp0, so any checkout path works. No drive letters.
rem  Order: RAG -> gsuid_core (optional) -> NoneBot(8080) -> SnowLuma(5099)
rem  Keep this file ASCII-only: cmd.exe misparses non-ASCII text in long
rem  UTF-8 batch files and then runs fragments of lines as commands.
rem ---------------------------------------------------------------------

title QQ Bot - Start

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "LOG=%ROOT%\logs"

rem Python falls back to GBK when stdout is redirected on Windows
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem Drop [::1] / ::1 from NO_PROXY so loopback traffic is not proxied.
rem Other entries are kept as-is and the value is never printed.
call :strip_loopback

rem RAG vectors were built on GPU(fp16); query with the same CUDA venv if present
set "RAG_PY=%ROOT%\rag\.venv\Scripts\python.exe"
if exist "%ROOT%\rag\.venv-gpu\Scripts\python.exe" set "RAG_PY=%ROOT%\rag\.venv-gpu\Scripts\python.exe"
set "GS_PY=%ROOT%\gsuid\core\.venv\Scripts\python.exe"
set "NONE_PY=%ROOT%\nonebot\.venv\Scripts\python.exe"

rem Bot QQ comes from env / local\bot.env / nonebot\.env, only for display.
rem Never hardcode an account id here.
set "BOT_UIN=%QQBOT_SELF_ID%"
if not defined BOT_UIN call :read_self_id "%ROOT%\local\bot.env"
if not defined BOT_UIN call :read_self_id "%ROOT%\nonebot\.env"
if defined BOT_UIN set "BOT_UIN=%BOT_UIN:"=%"
if defined BOT_UIN set "BOT_UIN=%BOT_UIN: =%"

rem ---------- admin dashboard address (display only) ----------
rem The panel is a separate process; this script does not start it.
rem Default is loopback 127.0.0.1:8092. DASHBOARD_HOST / DASHBOARD_PORT
rem (the same variables the dashboard service uses) override the shown
rem address. A wildcard bind is shown as the local loopback entry point.
set "DASH_HOST=%DASHBOARD_HOST%"
if not defined DASH_HOST set "DASH_HOST=127.0.0.1"
if "%DASH_HOST%"=="0.0.0.0" set "DASH_HOST=127.0.0.1"
if "%DASH_HOST%"=="::" set "DASH_HOST=127.0.0.1"
if "%DASH_HOST:~0,1%"==":" set "DASH_HOST=[%DASH_HOST%]"
set "DASH_PORT=%DASHBOARD_PORT%"
if not defined DASH_PORT set "DASH_PORT=8092"

echo ==========================================
echo   QQ bot start (SnowLuma + NoneBot2 + gsuid_core)
echo ==========================================
echo   Repo root: %ROOT%
echo   Admin panel URL: http://%DASH_HOST%:%DASH_PORT%  (separate process)
echo.

if not exist "%ROOT%\nonebot\bot.py" goto :not_checkout

rem Only one NoneBot per machine: if 8080 is listening, exit before starting
call :is_listening 8080
if not errorlevel 1 goto :already_running

rem Dependency check/installation must finish before any service starts.
echo [0/5] Checking startup dependencies ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\tools\startup-preflight.ps1" -Root "%ROOT%" -NoPause
if errorlevel 1 (
    echo       [!!] dependency check failed; no service was started
    exit /b 4
)

if not exist "%LOG%" mkdir "%LOG%" >nul 2>&1
if not exist "%LOG%" (
    echo [!!] Cannot create log dir: %LOG%
    exit /b 2
)

set "SNOW_HOME=%ROOT%\snowluma"
if exist "%LOG%\snowluma-home.ansi.txt" set /p SNOW_HOME=<"%LOG%\snowluma-home.ansi.txt"
set "SNOW_NODE=%SNOW_HOME%\node.exe"

set "FAILED=0"
set "RAG_EXPECT=0"
set "RAG_READY=0"
set "GS_EXPECT=0"
set "GS_READY=0"
set "NB_EXPECT=0"
set "NB_READY=0"
set "SNOW_EXPECT=0"
set "SNOW_READY=0"

rem ---------- 1/4 RAG service (8091) ----------
echo [1/4] RAG service (8091) ...
call :is_listening 8091
if not errorlevel 1 goto :rag_running
if not exist "%ROOT%\rag\index\vectors.npy" goto :rag_no_index
if not exist "%RAG_PY%" goto :rag_no_venv
call :start_service "%RAG_PY%" "serve.py" "%ROOT%\rag" "rag" ""
if errorlevel 1 (
    echo       [!!] failed to start, see log: %LOG%\rag.err.log
    goto :rag_done
)
echo       started
set "RAG_EXPECT=1"
goto :rag_done

:rag_running
echo       already listening, skipped
set "RAG_EXPECT=1"
set "RAG_READY=1"
goto :rag_done

:rag_no_index
echo       [--] index missing (rag\index\vectors.npy), run rag\build_index.py first
goto :rag_done

:rag_no_venv
echo       [!!] RAG venv missing: %RAG_PY%
goto :rag_done

:rag_done

rem ---------- 2/4 gsuid_core (8765, optional) ----------
echo [2/4] gsuid_core (8765, optional) ...
if exist "%ROOT%\nonebot\data\plugins.json" (
    findstr /C:"gsuid" "%ROOT%\nonebot\data\plugins.json" >nul 2>&1
    if not errorlevel 1 (
        echo       [--] disabled by plugin manager, skipped
        goto :gs_done
    )
)
call :is_listening 8765
if not errorlevel 1 goto :gs_running
if not exist "%GS_PY%" goto :gs_not_installed
call :start_service "%GS_PY%" "-m gsuid_core.core --host 0.0.0.0" "%ROOT%\gsuid\core" "gsuid" "%LOG%\gsuid-core.pid"
if errorlevel 1 (
    echo       [!!] failed to start, see log: %LOG%\gsuid.err.log
    goto :gs_done
)
echo       started
set "GS_EXPECT=1"
goto :gs_done

:gs_running
echo       already listening, skipped
set "GS_EXPECT=1"
set "GS_READY=1"
goto :gs_done

:gs_not_installed
echo       [--] not installed (optional), run gsuid\install.ps1 first, skipped
goto :gs_done

:gs_done

rem ---------- 3/4 NoneBot2 + MuiceBot (8080) ----------
echo [3/4] NoneBot2 + MuiceBot (8080) ...
if not exist "%NONE_PY%" goto :nb_missing
call :start_service "%NONE_PY%" "bot.py" "%ROOT%\nonebot" "nonebot" ""
if errorlevel 1 (
    echo       [!!] failed to start, see log: %LOG%\nonebot.err.log
    set "FAILED=1"
    goto :nb_done
)
echo       started
set "NB_EXPECT=1"
goto :nb_done

:nb_missing
echo       [!!] NoneBot venv missing: %NONE_PY%
echo            install nonebot deps first (see README)
set "FAILED=1"
goto :nb_done

:nb_done

rem ---------- 4/4 SnowLuma (5099) ----------
echo [4/4] SnowLuma (5099) ...
call :is_listening 5099
if not errorlevel 1 goto :snow_running
if not exist "%SNOW_NODE%" goto :snow_missing
call :start_service "%SNOW_NODE%" "index.mjs" "%SNOW_HOME%" "snowluma" ""
if errorlevel 1 (
    echo       [!!] failed to start, see log: %LOG%\snowluma.err.log
    set "FAILED=1"
    goto :snow_done
)
echo       started
set "SNOW_EXPECT=1"
goto :snow_done

:snow_running
echo       already listening, skipped
set "SNOW_EXPECT=1"
set "SNOW_READY=1"
goto :snow_done

:snow_missing
echo       [!!] SnowLuma executable missing: %SNOW_NODE%
set "FAILED=1"
goto :snow_done

:snow_done

rem ---------- 5/5 wait for readiness (max 60s) ----------
echo [5/5] waiting for services to become ready ...
set /a WAIT=30
:wait_loop
if "%NB_EXPECT%%NB_READY%"=="10" (
    call :is_listening 8080
    if not errorlevel 1 set "NB_READY=1"
)
if "%SNOW_EXPECT%%SNOW_READY%"=="10" (
    call :is_listening 5099
    if not errorlevel 1 set "SNOW_READY=1"
)
if "%RAG_EXPECT%%RAG_READY%"=="10" (
    call :is_listening 8091
    if not errorlevel 1 set "RAG_READY=1"
)
if "%GS_EXPECT%%GS_READY%"=="10" (
    call :is_listening 8765
    if not errorlevel 1 set "GS_READY=1"
)
set "ALL_READY=1"
if "%NB_EXPECT%%NB_READY%"=="10" set "ALL_READY=0"
if "%SNOW_EXPECT%%SNOW_READY%"=="10" set "ALL_READY=0"
if "%RAG_EXPECT%%RAG_READY%"=="10" set "ALL_READY=0"
if "%GS_EXPECT%%GS_READY%"=="10" set "ALL_READY=0"
if "%ALL_READY%"=="1" goto :wait_done
set /a WAIT-=1
if %WAIT% LEQ 0 goto :wait_done
<nul set /p "=."
ping -n 3 127.0.0.1 >nul
goto :wait_loop

:wait_done
echo.
echo.

if "%NB_EXPECT%%NB_READY%"=="11" echo   [OK] NoneBot2         : http://127.0.0.1:8080
if "%NB_EXPECT%%NB_READY%"=="10" echo   [!!] NoneBot2 not ready, see log: %LOG%\nonebot.out.log
if "%NB_EXPECT%%NB_READY%"=="10" set "FAILED=1"
if "%NB_EXPECT%%NB_READY%"=="00" echo   [--] NoneBot2 not started

if "%SNOW_EXPECT%%SNOW_READY%"=="11" echo   [OK] SnowLuma WebUI    : http://127.0.0.1:5099
if "%SNOW_EXPECT%%SNOW_READY%"=="10" echo   [!!] SnowLuma not ready, see log: %LOG%\snowluma.out.log
if "%SNOW_EXPECT%%SNOW_READY%"=="10" set "FAILED=1"
if "%SNOW_EXPECT%%SNOW_READY%"=="00" echo   [--] SnowLuma not started

if "%RAG_EXPECT%%RAG_READY%"=="11" echo   [OK] RAG service       : http://127.0.0.1:8091
if "%RAG_EXPECT%%RAG_READY%"=="10" echo   [!!] RAG not ready, see log: %LOG%\rag.out.log

if "%GS_EXPECT%%GS_READY%"=="11" echo   [OK] gsuid_core WebUI  : http://127.0.0.1:8765/app
if "%GS_EXPECT%%GS_READY%"=="10" echo   [!!] gsuid_core not ready (optional), see log: %LOG%\gsuid.out.log

echo.
echo ---------------- connection info ----------------
echo   OneBot reverse WS : ws://127.0.0.1:8080/onebot/v11/ws
echo   Admin dashboard   : http://%DASH_HOST%:%DASH_PORT%
if defined BOT_UIN echo   Bot QQ            : %BOT_UIN%
if not defined BOT_UIN echo   Bot QQ            : QQBOT_SELF_ID not configured
echo   Stop services with the stop launcher in this repo root
echo.
if "%FAILED%"=="1" echo [!!] A required service is not ready, check the log paths above.
if "%FAILED%"=="1" echo.
exit /b %FAILED%

:not_checkout
echo [!!] This does not look like a qqbot checkout: %ROOT%
echo      Expected a nonebot\bot.py entrypoint in this checkout.
exit /b 2

:already_running
echo [!!] Port 8080 is already listening: a NoneBot instance is already running.
echo      Not starting anything else, to avoid a second bot.
echo      Stop it first with the stop launcher in this repo root.
exit /b 3

:start_service
rem Args: 1=exe 2=arguments 3=working dir 4=log prefix 5=pid file (may be empty)
set "SVC_EXE=%~1"
set "SVC_ARGS=%~2"
set "SVC_CWD=%~3"
set "SVC_OUT=%LOG%\%~4.out.log"
set "SVC_ERR=%LOG%\%~4.err.log"
set "SVC_PID_FILE=%~5"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $p = Start-Process -FilePath $env:SVC_EXE -ArgumentList $env:SVC_ARGS -WorkingDirectory $env:SVC_CWD -RedirectStandardOutput $env:SVC_OUT -RedirectStandardError $env:SVC_ERR -WindowStyle Hidden -PassThru -ErrorAction Stop; if ($env:SVC_PID_FILE) { Set-Content -LiteralPath $env:SVC_PID_FILE -Value $p.Id -Encoding ascii }; exit 0 } catch { Write-Output ('  [!!] ' + $_.Exception.Message); exit 1 }"
exit /b %ERRORLEVEL%

:is_listening
rem Arg: port. Exit 0 when listening, 1 otherwise.
rem All ports here are 4 digits, so ":8080" cannot match a 5-digit port.
netstat -ano -p TCP 2>nul | findstr /C:":%~1" | findstr /C:"LISTENING" >nul
exit /b %ERRORLEVEL%

:strip_loopback
rem Remove only [::1] and ::1 from NO_PROXY, keep every other entry in order.
set "NP_IN=%NO_PROXY%"
if not defined NP_IN exit /b 0
set "NP_OUT="
:strip_loop_next
set "NP_ITEM="
set "NP_REST="
for /f "tokens=1,* delims=,;" %%A in ("%NP_IN%") do (
    set "NP_ITEM=%%A"
    set "NP_REST=%%B"
)
if not defined NP_ITEM goto :strip_loop_done
call :strip_keep_item
set "NP_IN=%NP_REST%"
goto :strip_loop_next
:strip_loop_done
set "NO_PROXY=%NP_OUT%"
set "no_proxy=%NP_OUT%"
exit /b 0

:strip_keep_item
set "NP_T="
for /f "tokens=*" %%T in ("%NP_ITEM%") do set "NP_T=%%T"
if not defined NP_T exit /b 0
:strip_trim_tail
if not defined NP_T exit /b 0
if not "%NP_T:~-1%"==" " goto :strip_cmp
set "NP_T=%NP_T:~0,-1%"
goto :strip_trim_tail
:strip_cmp
if /i "%NP_T%"=="::1" exit /b 0
if /i "%NP_T%"=="[::1]" exit /b 0
if defined NP_OUT (
    set "NP_OUT=%NP_OUT%,%NP_T%"
) else (
    set "NP_OUT=%NP_T%"
)
exit /b 0

:read_self_id
rem Arg: env file. Only fills BOT_UIN when it is still empty.
if not exist "%~1" exit /b 0
for /f "usebackq tokens=1,* delims== " %%A in ("%~1") do (
    if not defined BOT_UIN if /i "%%A"=="QQBOT_SELF_ID" set "BOT_UIN=%%B"
)
exit /b 0
