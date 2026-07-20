@echo off
title Source Engine LOD Builder - Executable Compiler
cls
echo ============================================================
echo         Source Engine LOD Builder - Build Executable
echo ============================================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Python was not found in your PATH.
    echo Please install Python 3.11+ and make sure it is added to your PATH.
    pause
    exit /b 1
)

echo Checking for PyInstaller and requirements...
python -m pip install pyinstaller --upgrade --quiet

echo.
echo Running compilation process...
:: Run PyInstaller with dynamic tkinterdnd2 path discovery
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$SCRIPT_DIR = '%~dp0'.TrimEnd('\');" ^
    "$TOOLS_DIR = Join-Path $SCRIPT_DIR 'tools';" ^
    "$DIST_DIR = Join-Path $SCRIPT_DIR 'dist';" ^
    "$ICON_PATH = Join-Path $SCRIPT_DIR 'icon.ico';" ^
    "$CROWBAR_CLI_SRC = Join-Path $TOOLS_DIR 'CrowbarCLI.exe';" ^
    "$MAIN_SCRIPT = Join-Path $SCRIPT_DIR 'LOD_Generator.py';" ^
    "" ^
    "$pyinstallerArgs = @('--onefile', '--windowed', '--name', 'LOD_Generator', '--distpath', $DIST_DIR);" ^
    "if (Test-Path $ICON_PATH) { $pyinstallerArgs += '--icon', $ICON_PATH };" ^
    "if (Test-Path $CROWBAR_CLI_SRC) { $pyinstallerArgs += '--add-data', ($CROWBAR_CLI_SRC + ';tools') };" ^
    "try {" ^
    "    $tkdndPath = python -c 'import os, tkinterdnd2; print(os.path.abspath(os.path.dirname(tkinterdnd2.__file__)))';" ^
    "    if ($tkdndPath) {" ^
    "        $tkdndPath = $tkdndPath.Trim();" ^
    "        if (Test-Path $tkdndPath) {" ^
    "            $pyinstallerArgs += '--add-data', ($tkdndPath + ';tkinterdnd2');" ^
    "            Write-Host '  Detected tkinterdnd2 at: ' $tkdndPath -ForegroundColor Green" ^
    "        }" ^
    "    }" ^
    "} catch { Write-Warning 'Could not automatically locate tkinterdnd2 installation.' };" ^
    "" ^
    "$pyinstallerArgs += $MAIN_SCRIPT;" ^
    "Write-Host '  Compiling LOD_Generator.exe (this might take 1-2 minutes)...' -ForegroundColor Cyan;" ^
    "python -m PyInstaller $pyinstallerArgs"

echo.
if %errorLevel% == 0 (
    echo [OK] Executable successfully compiled! Check the 'dist' folder.
) else (
    echo [ERROR] PyInstaller compilation failed.
)
echo.
pause
exit /b
