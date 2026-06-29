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
#     .\desktop\build_installer.ps1 -Debug     # faster compile, larger binary
# =====================================================================
[CmdletBinding()]
param(
    [switch] $Debug = $false
)
$ErrorActionPreference = "Stop"

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorktreeRoot = Split-Path -Parent $ScriptDir

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
    & $py -m pip install pyinstaller --quiet
    & $py -m PyInstaller "desktop\backend\aurora_backend.spec" --noconfirm `
        --distpath "desktop\backend\dist" --workpath "desktop\backend\build"
    $exe = Join-Path $WorktreeRoot "desktop\backend\dist\aurora-backend\aurora-backend.exe"
    if (-not (Test-Path $exe)) { Write-Host "Backend build failed -- $exe missing." -ForegroundColor Red; exit 1 }
    Write-Host "  backend ready: $exe" -ForegroundColor Green
} finally { Pop-Location }

Write-Host "[2/2] Building Tauri installer..." -ForegroundColor Cyan
Push-Location $ScriptDir
try {
    npm install
    if ($Debug) { npm run tauri build -- --debug } else { npm run tauri build }
} finally { Pop-Location }

$mode = if ($Debug) { "debug" } else { "release" }
Write-Host ""
Write-Host "Done. Installer(s) under:" -ForegroundColor Green
Write-Host "  desktop\src-tauri\target\$mode\bundle\" -ForegroundColor Green
