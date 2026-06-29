# =====================================================================
# Aurora desktop -- full installer build (Windows).
# ---------------------------------------------------------------------
# Sequences the two builds the self-contained installer needs:
#   1. PyInstaller freezes the Aurora backend  -> desktop/backend/dist/aurora-backend/
#   2. Tauri bundles that backend + the shell  -> the installer
#
# tauri build REQUIRES the backend bundle to already exist (it's a
# configured resource in tauri.conf.json), so this ordering matters.
# `npm run tauri dev` ignores bundle resources, so dev mode is unaffected
# and does NOT need this script.
#
# Usage (from anywhere):
#     .\desktop\build_installer.ps1
#     .\desktop\build_installer.ps1 -DebugBuild   # faster compile, larger binary
# =====================================================================
# NOTE: the switch is -DebugBuild, NOT -Debug. [CmdletBinding()] reserves
# -Debug as an automatic common parameter, so naming our own switch -Debug
# is a "parameter defined multiple times" error.
[CmdletBinding()]
param(
    [switch] $DebugBuild = $false
)
$ErrorActionPreference = "Stop"

# IMPORTANT: do NOT use $ErrorActionPreference = "Stop" here. Windows
# PowerShell 5.1 wraps a native command's stderr output as a terminating
# ErrorRecord under Stop, so pip / pyinstaller / npm printing a harmless
# notice to stderr aborts the whole script even on exit code 0. We use
# Continue + explicit $LASTEXITCODE checks instead (the robust pattern).
$ErrorActionPreference = "Continue"

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorktreeRoot = Split-Path -Parent $ScriptDir

# Run a native command and fail the script only on a real nonzero exit.
function Invoke-Native {
    param([string] $What, [scriptblock] $Block)
    & $Block
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  $What failed (exit $LASTEXITCODE)." -ForegroundColor Red
        exit 1
    }
}

# Reload PATH so Rust/Cargo are visible if installed in another session.
$env:Path = [Environment]::GetEnvironmentVariable("Path","User") + ";" + [Environment]::GetEnvironmentVariable("Path","Machine")

# Find the venv python (walk up like launch.ps1 does).
function Find-Venv {
    $cur = $WorktreeRoot
    while ($cur -and (Test-Path $cur)) {
        if (Test-Path (Join-Path $cur ".venv\Scripts\python.exe")) { return (Join-Path $cur ".venv") }
        $parent = Split-Path -Parent $cur
        if (-not $parent -or $parent -eq $cur) { break }
        $cur = $parent
    }
    return $null
}
$venv = Find-Venv
if (-not $venv) { Write-Host "No .venv found. Create one + pip install -r requirements.txt" -ForegroundColor Red; exit 1 }
$py = Join-Path $venv "Scripts\python.exe"

Write-Host "[1/2] Building Aurora backend with PyInstaller..." -ForegroundColor Cyan
Push-Location $WorktreeRoot
try {
    Invoke-Native "pip install pyinstaller" { & $py -m pip install pyinstaller --quiet }
    Invoke-Native "pyinstaller" {
        & $py -m PyInstaller "desktop\backend\aurora_backend.spec" --noconfirm `
            --distpath "desktop\backend\dist" --workpath "desktop\backend\build"
    }
    $exe = Join-Path $WorktreeRoot "desktop\backend\dist\aurora-backend\aurora-backend.exe"
    if (-not (Test-Path $exe)) { Write-Host "Backend build failed -- $exe missing." -ForegroundColor Red; exit 1 }
    Write-Host "  backend ready: $exe" -ForegroundColor Green
} finally { Pop-Location }

Write-Host "[2/2] Building Tauri installer..." -ForegroundColor Cyan
Push-Location $ScriptDir
try {
    Invoke-Native "npm install" { npm install }
    if ($DebugBuild) {
        Invoke-Native "tauri build (debug)" { npm run tauri build -- --debug }
    } else {
        Invoke-Native "tauri build (release)" { npm run tauri build }
    }
} finally { Pop-Location }

$mode = if ($DebugBuild) { "debug" } else { "release" }
Write-Host ""
Write-Host "Done. Installer(s) under:" -ForegroundColor Green
Write-Host "  desktop\src-tauri\target\$mode\bundle\" -ForegroundColor Green
