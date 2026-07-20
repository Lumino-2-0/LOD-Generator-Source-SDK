@echo off
title Source Engine LOD Builder - Release Installer
:: Check for Admin rights and elevate if necessary
net session >nul 2>&1
if %errorLevel% == 0 (
    goto :run_setup
) else (
    echo Elevating privileges to run dependency checks and system installer...
    powershell -Command "Start-Process -Verb RunAs -FilePath '%0'"
    exit /b
)

:run_setup
cls
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "Install_Release.ps1"
echo.
echo Press any key to exit...
pause >nul
exit /b
