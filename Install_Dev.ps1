#Requires -Version 5.1
<#
.SYNOPSIS
    Source Engine LOD Builder - Automatic Installer
.DESCRIPTION
    Installs every dependency required to build LOD_Generator.exe:
      1. Python 3.11  (via winget, or manual path if unavailable)
      2. Blender 4.x  (via winget, Steam library scan, or manual path)
      3. SourceIO addon for Blender
      4. Python packages (requirements.txt)
      5. PyInstaller + LOD_Generator.exe build
.NOTES
    Running as Administrator is recommended for the Blender installation.
    Compatible with Windows 10/11.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------
$SCRIPT_DIR      = Split-Path -Parent $MyInvocation.MyCommand.Definition
$TOOLS_DIR       = Join-Path $SCRIPT_DIR "tools"
$DIST_DIR        = Join-Path $SCRIPT_DIR "dist"
$SOURCEIO_URL    = "https://github.com/REDxEYE/SourceIO/releases/download/5.5.3/SourceIO.zip"
$SOURCEIO_ZIP    = Join-Path $env:TEMP "SourceIO.zip"
$CROWBAR_CLI_SRC = Join-Path $TOOLS_DIR "CrowbarCLI.exe"
$MAIN_SCRIPT     = Join-Path $SCRIPT_DIR "LOD_Generator.py"
$ICON_PATH       = Join-Path $SCRIPT_DIR "icon.ico"

# -----------------------------------------------------------------------------
# HELPER FUNCTIONS
# -----------------------------------------------------------------------------
function Write-Step([string]$msg) {
    Write-Host " # $msg" -ForegroundColor Cyan
}

function Write-OK([string]$msg) {
    Write-Host "  OK    $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "  Warn  $msg" -ForegroundColor Yellow
}

function Write-Fail([string]$msg) {
    Write-Host "  Error $msg" -ForegroundColor Red
}

function Get-CommandPath([string]$name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source } else { return $null }
}

function Read-ManualPath {
    param(
        [string]$ToolName,
        [string]$ExampleFile
    )
    while ($true) {
        $userPath = Read-Host "  -> Enter the full path to $ExampleFile for $ToolName (leave empty to skip)"
        if ([string]::IsNullOrWhiteSpace($userPath)) { return $null }
        $userPath = $userPath.Trim('"').Trim()
        if (Test-Path $userPath -PathType Leaf) { return $userPath }
        Write-Warn "File not found: $userPath - please try again."
    }
}

function Get-SteamLibraryFolders {
    $libraries = New-Object System.Collections.Generic.List[string]
    $steamPath = $null

    try {
        $reg = Get-ItemProperty -Path "HKCU:\Software\Valve\Steam" -Name "SteamPath" -ErrorAction SilentlyContinue
        if ($reg) { $steamPath = $reg.SteamPath }
    } catch {}

    if (-not $steamPath) {
        foreach ($p in @("${env:ProgramFiles(x86)}\Steam", "$env:ProgramFiles\Steam", "C:\Steam")) {
            if ($p -and (Test-Path $p)) { $steamPath = $p; break }
        }
    }

    if ($steamPath) {
        $steamPath = $steamPath -replace '/', '\'
        if (Test-Path $steamPath) { $libraries.Add($steamPath) }

        $vdfPath = Join-Path $steamPath "steamapps\libraryfolders.vdf"
        if (Test-Path $vdfPath) {
            try {
                $content = Get-Content $vdfPath -Raw
                $found = [regex]::Matches($content, '"path"\s*"([^"]+)"')
                foreach ($m in $found) {
                    $lib = ($m.Groups[1].Value -replace '\\\\', '\')
                    if ($lib -and (Test-Path $lib) -and (-not $libraries.Contains($lib))) {
                        $libraries.Add($lib)
                    }
                }
            } catch {}
        }
    }
    return $libraries
}

function Find-BlenderExe {
    foreach ($c in @("blender", "blender.exe")) {
        $resolved = Get-CommandPath $c
        if ($resolved -and (Test-Path $resolved)) { return $resolved }
    }

    $staticCandidates = @(
        "$env:ProgramFiles\Blender Foundation\Blender 4.0\blender.exe",
        "$env:ProgramFiles\Blender Foundation\Blender 4.1\blender.exe",
        "$env:ProgramFiles\Blender Foundation\Blender 4.2\blender.exe",
        "$env:ProgramFiles\Blender Foundation\Blender 4.3\blender.exe",
        "$env:ProgramFiles\Blender Foundation\Blender\blender.exe"
    )
    foreach ($c in $staticCandidates) {
        if (Test-Path $c) { return $c }
    }

    $bf = "$env:ProgramFiles\Blender Foundation"
    if (Test-Path $bf) {
        $found = Get-ChildItem $bf -Recurse -Filter "blender.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { return $found.FullName }
    }

    foreach ($lib in (Get-SteamLibraryFolders)) {
        $steamBlender = Join-Path $lib "steamapps\common\Blender\blender.exe"
        if (Test-Path $steamBlender) { return $steamBlender }
    }

    return $null
}

# -----------------------------------------------------------------------------
# BANNER
# -----------------------------------------------------------------------------
Clear-Host
Write-Host @"
============================================================
        Source Engine LOD Builder - Installer
        github.com/Lumino-2-0/source-lod-builder
============================================================
"@ -ForegroundColor Magenta

# -----------------------------------------------------------------------------
# STEP 1 - Python
# -----------------------------------------------------------------------------
Write-Step "1/5  Python 3.11"

$pythonExe = Get-CommandPath "python"
if (-not $pythonExe) { $pythonExe = Get-CommandPath "python3" }

if ($pythonExe) {
    $pyVer = & $pythonExe --version
    Write-OK "Python already installed: $pyVer  ($pythonExe)"
} else {
    Write-Warn "Python not found. Attempting automatic installation via winget..."
    $installedOk = $false
    try {
        winget install --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("Path","User")
        $pythonExe = Get-CommandPath "python"
        if ($pythonExe) {
            Write-OK "Python installed successfully."
            $installedOk = $true
        }
    } catch {
        Write-Warn "Automatic installation via winget failed."
    }

    if (-not $installedOk) {
        Write-Warn "Could not install Python automatically."
        $manualPath = Read-ManualPath -ToolName "Python" -ExampleFile "python.exe"
        if ($manualPath) {
            $pythonExe = $manualPath
            Write-OK "Using manual path: $pythonExe"
        } else {
            Write-Fail "Python is required to continue."
            Write-Host "  -> Download it from https://www.python.org/downloads/ then re-run this script." -ForegroundColor Yellow
            exit 1
        }
    }
}

$pipExe = Get-CommandPath "pip"
if (-not $pipExe) {
    & $pythonExe -m ensurepip --upgrade | Out-Null
}

# -----------------------------------------------------------------------------
# STEP 2 - Blender
# -----------------------------------------------------------------------------
Write-Step "2/5  Blender 4.x"

$blenderExe = Find-BlenderExe

if ($blenderExe) {
    Write-OK "Blender found: $blenderExe"
} else {
    Write-Warn "Blender not found automatically (checked PATH, Program Files, and Steam libraries)."
    Write-Warn "Attempting automatic installation via winget..."
    $installedOk = $false
    try {
        winget install --id BlenderFoundation.Blender --silent --accept-package-agreements --accept-source-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("Path","User")
        $blenderExe = Find-BlenderExe
        if ($blenderExe) {
            Write-OK "Blender installed: $blenderExe"
            $installedOk = $true
        }
    } catch {
        Write-Warn "Automatic installation via winget failed."
    }

    if (-not $installedOk) {
        Write-Warn "Could not install Blender automatically."
        Write-Host "  (If installed via Steam, launch Steam once to register library folders.)" -ForegroundColor Yellow
        $manualPath = Read-ManualPath -ToolName "Blender" -ExampleFile "blender.exe"
        if ($manualPath) {
            $blenderExe = $manualPath
            Write-OK "Using manual path: $blenderExe"
        } else {
            Write-Fail "Blender is required to continue."
            Write-Host "  -> Download it from https://www.blender.org/download/ or install via Steam, then re-run." -ForegroundColor Yellow
            exit 1
        }
    }
}

# -----------------------------------------------------------------------------
# STEP 3 - SourceIO addon
# -----------------------------------------------------------------------------
Write-Step "3/5  Blender addon: SourceIO v5.5.3"

$addonCheckScript = @"
import addon_utils, sys
mods = [m.__name__ for m in addon_utils.modules()]
sys.exit(0 if any('SourceIO' in m or 'source_io' in m.lower() for m in mods) else 1)
"@
$addonCheckFile = Join-Path $env:TEMP "check_sourceio.py"
$addonCheckScript | Set-Content $addonCheckFile -Encoding UTF8

$blenderLogOut = Join-Path $env:TEMP "blender_out.log"
$blenderLogErr = Join-Path $env:TEMP "blender_err.log"

$proc = Start-Process -FilePath $blenderExe -ArgumentList "--background", "--python", "`"$addonCheckFile`"" -Wait -NoNewWindow -PassThru -RedirectStandardOutput $blenderLogOut -RedirectStandardError $blenderLogErr

if ($proc.ExitCode -eq 0) {
    Write-OK "SourceIO is already installed in Blender."
} else {
    Write-Warn "SourceIO not found. Downloading..."
    try {
        Write-Host "  Downloading SourceIO.zip..." -NoNewline
        Invoke-WebRequest -Uri $SOURCEIO_URL -OutFile $SOURCEIO_ZIP -UseBasicParsing
        Write-Host " OK" -ForegroundColor Green

        $installScript = @"
import bpy, sys
bpy.ops.preferences.addon_install(filepath=r'$($SOURCEIO_ZIP.Replace("\", "\\"))', overwrite=True)
bpy.ops.preferences.addon_enable(module='SourceIO')
bpy.ops.wm.save_userpref()
print("SourceIO_Install_Success")
"@
        $installScriptFile = Join-Path $env:TEMP "install_sourceio.py"
        $installScript | Set-Content $installScriptFile -Encoding UTF8

        Start-Process -FilePath $blenderExe -ArgumentList "--background", "--python", "`"$installScriptFile`"" -Wait -NoNewWindow -PassThru -RedirectStandardOutput $blenderLogOut -RedirectStandardError $blenderLogErr | Out-Null
        
        $output = Get-Content $blenderLogOut -Raw -ErrorAction SilentlyContinue
        if ($output -match "SourceIO_Install_Success") {
            Write-OK "SourceIO addon installed in Blender."
        } else {
            Write-Warn "Could not confirm automatic installation. Please check manually in Blender."
            Write-Host "  -> Edit > Preferences > Add-ons > Install > select $SOURCEIO_ZIP" -ForegroundColor Yellow
        }
    } catch {
        Write-Fail "Error while downloading/installing SourceIO."
        Write-Host "  -> Install it manually from: $SOURCEIO_URL" -ForegroundColor Yellow
    }
}

# -----------------------------------------------------------------------------
# STEP 4 - CrowbarCLI + Python requirements
# -----------------------------------------------------------------------------
Write-Step "4/5  CrowbarCLI + Python packages"

if (Test-Path $CROWBAR_CLI_SRC) {
    Write-OK "CrowbarCLI.exe found in tools/"
} else {
    Write-Warn "tools/CrowbarCLI.exe not found."
    $manualPath = Read-ManualPath -ToolName "CrowbarCLI" -ExampleFile "CrowbarCLI.exe"
    if ($manualPath) {
        New-Item -ItemType Directory -Path $TOOLS_DIR -Force | Out-Null
        Copy-Item $manualPath $CROWBAR_CLI_SRC -Force
        Write-OK "CrowbarCLI.exe copied into tools/"
    } else {
        Write-Warn "Continuing without CrowbarCLI.exe bundled into the build."
    }
}

$requirementsFile = Join-Path $SCRIPT_DIR "requirements.txt"
if (Test-Path $requirementsFile) {
    Write-Host "  Installing Python packages..." -NoNewline
    & $pythonExe -m pip install --upgrade pip --quiet
    & $pythonExe -m pip install -r $requirementsFile --quiet
    Write-Host " OK" -ForegroundColor Green
    Write-OK "Python packages installed."
} else {
    Write-Warn "requirements.txt not found. Installing essential packages instead..."
    & $pythonExe -m pip install --upgrade pip --quiet
    & $pythonExe -m pip install pyglet PyOpenGL Pillow tkinterdnd2 --quiet
    Write-OK "Essential packages installed."
}

Write-Host "  Installing PyInstaller..." -NoNewline
& $pythonExe -m pip install pyinstaller --quiet
Write-Host " OK" -ForegroundColor Green

# -----------------------------------------------------------------------------
# STEP 5 - Build EXE
# -----------------------------------------------------------------------------
Write-Step "5/5  Build LOD_Generator.exe"

if (-not (Test-Path $MAIN_SCRIPT)) {
    Write-Fail "LOD_Generator.py not found in: $SCRIPT_DIR"
    exit 1
}

$pyinstallerArgs = @(
    "--onefile",
    "--windowed",
    "--name", "LOD_Generator",
    "--distpath", $DIST_DIR
)

if (Test-Path $ICON_PATH) {
    $pyinstallerArgs += "--icon", $ICON_PATH
    Write-Host "  Icon detected: $ICON_PATH"
}

if (Test-Path $CROWBAR_CLI_SRC) {
    $pyinstallerArgs += "--add-data", "$CROWBAR_CLI_SRC;tools"
}

# --- CORRECTIF POUR TKINTERDND2 ---
# On va chercher dynamiquement le dossier d'installation de tkinterdnd2 pour l'inclure dans l'EXE
try {
    $tkdndPath = & $pythonExe -c "import os, tkinterdnd2; print(os.path.abspath(os.path.dirname(tkinterdnd2.__file__)))"
    if ($tkdndPath) {
        $tkdndPath = $tkdndPath.Trim()
        if (Test-Path $tkdndPath) {
            $pyinstallerArgs += "--add-data", "$tkdndPath;tkinterdnd2"
            Write-OK "Library tkinterdnd2 detected at: $tkdndPath (added to bundle)"
        }
    }
} catch {
    Write-Warn "Could not auto-detect tkinterdnd2 folder path. The compiled EXE might still crash!"
}
# ----------------------------------

$pyinstallerArgs += $MAIN_SCRIPT

# Fusion correcte de la commande pour PowerShell
$allArgs = @("-m", "PyInstaller") + $pyinstallerArgs

Write-Host "  Building (this can take 1-2 minutes)..." -NoNewline
try {
    $buildLogOut = Join-Path $env:TEMP "pyinstaller_build_out.log"
    $buildLogErr = Join-Path $env:TEMP "pyinstaller_build_err.log"
    
    $buildProc = Start-Process -FilePath $pythonExe -ArgumentList $allArgs -Wait -NoNewWindow -PassThru -RedirectStandardOutput $buildLogOut -RedirectStandardError $buildLogErr
    
    if ($buildProc.ExitCode -ne 0) {
        Write-Host " FAILED" -ForegroundColor Red
        Get-Content $buildLogOut -Tail 20 -ErrorAction SilentlyContinue
        Get-Content $buildLogErr -Tail 20 -ErrorAction SilentlyContinue
        Write-Fail "Build failed."
        exit 1
    }
    Write-Host " OK" -ForegroundColor Green
} catch {
    Write-Fail "PyInstaller error: $_"
    exit 1
}

$exePath = Join-Path $DIST_DIR "LOD_Generator.exe"
if (Test-Path $exePath) {
    $exeSize = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
    Write-OK "EXE generated: $exePath  ($exeSize MB)"
} else {
    Write-Fail "EXE not found after build. Check the logs above."
    exit 1
}

# -----------------------------------------------------------------------------
# SUMMARY
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Installation completed successfully!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Executable    : $exePath" -ForegroundColor White
Write-Host "  Blender       : $blenderExe" -ForegroundColor White
if (Test-Path $CROWBAR_CLI_SRC) {
    Write-Host "  CrowbarCLI    : $CROWBAR_CLI_SRC" -ForegroundColor White
}
Write-Host ""
Write-Host "  Launch LOD_Generator.exe and set the studiomdl, Blender" -ForegroundColor Yellow
Write-Host "  and Crowbar paths in the app's Tools panel. The app tries" -ForegroundColor Yellow
Write-Host "  to auto-detect them (including GMod/Blender via Steam) and" -ForegroundColor Yellow
Write-Host "  will let you browse to them manually if needed." -ForegroundColor Yellow
Write-Host ""
