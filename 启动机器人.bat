@echo off
setlocal EnableExtensions
title QQ Bot - Start
rem QQBOT-ENTRY-START-v1

rem Root = the folder of this file. The tools launcher has a non-ASCII name
rem and non-ASCII bytes in a batch file can desync cmd's parser, so locate
rem it by an ASCII marker instead of hardcoding a path or a drive letter.
set "TOOLS_BAT="
for %%F in ("%~dp0tools\*.bat") do call :pick "%%~fF"
if not defined TOOLS_BAT goto :missing

call "%TOOLS_BAT%" --no-pause
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo [!!] Start did not fully succeed, exit code %RC%.
echo.
pause
exit /b %RC%

:pick
if defined TOOLS_BAT exit /b 0
findstr /M /C:"QQBOT-ENTRY-START-v1" "%~1" >nul 2>&1
if errorlevel 1 exit /b 0
set "TOOLS_BAT=%~1"
exit /b 0

:missing
echo [!!] No start launcher found under "%~dp0tools"
echo      Expected a .bat file marked QQBOT-ENTRY-START-v1 there.
echo.
pause
exit /b 2
