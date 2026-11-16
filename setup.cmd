@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "GGET_SETUP_EXIT=%ERRORLEVEL%"
if not "%GGET_SETUP_EXIT%"=="0" pause
exit /b %GGET_SETUP_EXIT%
