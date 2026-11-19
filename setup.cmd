@echo off
setlocal
set "GGET_SETUP_CODEPAGE="
for /f "tokens=2 delims=:" %%C in ('chcp 2^>nul') do for /f %%P in ("%%C") do set "GGET_SETUP_CODEPAGE=%%P"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "GGET_SETUP_EXIT=%ERRORLEVEL%"
if defined GGET_SETUP_CODEPAGE chcp %GGET_SETUP_CODEPAGE% >nul
if not "%GGET_SETUP_EXIT%"=="0" pause
exit /b %GGET_SETUP_EXIT%
