#Requires -Version 5.1
<#
.SYNOPSIS
    Source Engine LOD Builder - Release Installer
.DESCRIPTION
    Verifies and installs external dependencies for release end-users:
      1. Blender 4.x (via winget or Steam libraries)
      2. SourceIO addon for Blender (download and auto-install)
      3. Checks for game installations (Garry's Mod, HL2, TF2, etc.)
      4. Downloads and places CrowbarCLI.exe if missing
.NOTES
    Compatible with Windows 10/11.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$SCRIPT_DIR   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$TOOLS_DIR    = Join-Path $SCRIPT_DIR "tools"
$SOURCEIO_URL = "https://github.com/REDxEYE/SourceIO/releases/download/5.5.3/SourceIO.zip"
$SOURCEIO_ZIP = Join-Path $env:TEMP "SourceIO.zip"

function Write-Step([string]$msg) {
    Write-Host " # $msg" -ForegroundColor Cyan
}

function Write-OK([string]$msg) {
    Write-Host "  [OK]    $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "  [WARN]  $msg" -ForegroundColor Yellow
}

function Write-Fail([string]$msg) {
    Write-Host "  [ERROR] $msg" -ForegroundColor Red
}

function Get-CommandPath([string]$name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source } else { return $null }
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
        "$env:ProgramFiles\Blender Foundation\Blender 4.3\blender.exe",
        "$env:ProgramFiles\Blender Foundation\Blender 4.2\blender.exe",
        "$env:ProgramFiles\Blender Foundation\Blender 4.1\blender.exe",
        "$env:ProgramFiles\Blender Foundation\Blender 4.0\blender.exe",
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

Clear-Host
Write-Host "============================================================" -ForegroundColor Magenta
Write-Host "      Source Engine LOD Builder - Release Installer" -ForegroundColor Magenta
Write-Host "             Release End-User Dependencies Setup" -ForegroundColor Magenta
Write-Host "============================================================" -ForegroundColor Magenta
Write-Host ""

# 1. Blender Check
Write-Step "1/4 Checking Blender 4.x..."
$blenderExe = Find-BlenderExe

if ($blenderExe) {
    Write-OK "Blender found: $blenderExe"
} else {
    Write-Warn "Blender was not found automatically."
    Write-Warn "Attempting automatic installation via winget..."
    try {
        Start-Process winget -ArgumentList "install --id BlenderFoundation.Blender --silent --accept-package-agreements --accept-source-agreements" -NoNewWindow -Wait
        $blenderExe = Find-BlenderExe
        if ($blenderExe) {
            Write-OK "Blender installed successfully: $blenderExe"
        } else {
            throw "Installation completed but blender.exe could not be located."
        }
    } catch {
        Write-Fail "Blender installation failed or winget was unavailable."
        Write-Fail "Please install Blender 4.x manually from https://www.blender.org/download/"
    }
}

# 2. SourceIO Addon Check
if ($blenderExe) {
    Write-Step "2/4 Checking Blender addon: SourceIO..."
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
        Write-OK "SourceIO addon is already installed and active in Blender."
    } else {
        Write-Warn "SourceIO addon not found in Blender. Downloading and installing..."
        try {
            Write-Host "  Downloading SourceIO.zip..."
            Invoke-WebRequest -Uri $SOURCEIO_URL -OutFile $SOURCEIO_ZIP -UseBasicParsing
            
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
                Write-OK "SourceIO addon successfully installed in Blender."
            } else {
                Write-Fail "Automatic installation of Blender addon failed."
                Write-Fail "Please install it manually inside Blender (Preferences -> Add-ons -> Install -> select $SOURCEIO_ZIP)."
            }
        } catch {
            Write-Fail "An error occurred while downloading or installing SourceIO: $_"
        }
    }
}

# 3. Source game / GMod check
Write-Step "3/4 Checking Source Game / GMod files..."
$gamesFound = @()
foreach ($lib in (Get-SteamLibraryFolders)) {
    foreach ($g in @("GarrysMod", "Half-Life 2", "Team Fortress 2", "Portal 2", "Counter-Strike Source", "Source SDK Base 2013 Multiplayer")) {
        $gp = Join-Path $lib "steamapps\common\$g"
        if (Test-Path $gp) {
            $gamesFound += $g
            Write-OK "Found game directory: $g ($gp)"
        }
    }
}
if ($gamesFound.Count -eq 0) {
    Write-Warn "No compatible Source Game or GMod install detected automatically in standard Steam library directories."
    Write-Warn "You will need to manually specify your Source/GMod game folder (containing gameinfo.txt) in the application."
} else {
    Write-OK "Detected $($gamesFound.Count) game directories."
}

# 4. CrowbarCLI Check
Write-Step "4/4 Checking for CrowbarCLI..."
$crowbarCandidates = @(
    (Join-Path $SCRIPT_DIR "CrowbarCLI.exe"),
    (Join-Path $SCRIPT_DIR "tools\CrowbarCLI.exe"),
    (Join-Path $SCRIPT_DIR "tool\CrowbarCLI.exe"),
    (Join-Path (Split-Path -Parent $SCRIPT_DIR) "CrowbarCLI.exe"),
    (Join-Path (Split-Path -Parent $SCRIPT_DIR) "tools\CrowbarCLI.exe")
)
$crowbarFound = $null
foreach ($c in $crowbarCandidates) {
    if (Test-Path $c) {
        $crowbarFound = $c
        break
    }
}

if ($crowbarFound) {
    Write-OK "CrowbarCLI.exe found at: $crowbarFound"
} else {
    Write-Warn "CrowbarCLI.exe was not found in the release directories."
    Write-Warn "Attempting to download CrowbarCLI automatically..."
    try {
        New-Item -ItemType Directory -Path $TOOLS_DIR -Force | Out-Null
        $crowbarTarget = Join-Path $TOOLS_DIR "CrowbarCLI.exe"
        $downloadUrl = "https://github.com/Lumino-2-0/LOD-Generator-Source-SDK/raw/main/tools/CrowbarCLI.exe"
        
        Write-Host "  Downloading CrowbarCLI.exe..."
        Invoke-WebRequest -Uri $downloadUrl -OutFile $crowbarTarget -UseBasicParsing
        if (Test-Path $crowbarTarget) {
            Write-OK "CrowbarCLI.exe successfully downloaded and placed in: $crowbarTarget"
            $crowbarFound = $crowbarTarget
        }
    } catch {
        Write-Fail "Could not download CrowbarCLI automatically: $_"
        Write-Fail "Please obtain CrowbarCLI.exe manually and place it in the 'tools' subdirectory next to your LOD_Generator.exe."
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "      Release Dependencies Verification Complete" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Blender Path       : " -NoNewline; Write-Host ($blenderExe -or "Not found") -ForegroundColor White
Write-Host "  CrowbarCLI Path    : " -NoNewline; Write-Host ($crowbarFound -or "Not found") -ForegroundColor White
Write-Host ""
Write-Host "  You can now launch your LOD_Generator tool with confidence!" -ForegroundColor Yellow
Write-Host ""
