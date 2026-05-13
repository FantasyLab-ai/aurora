# Aurora Studio - Windows launcher.
#
# Usage (from the repo root, in any Windows PowerShell or PowerShell 7 session):
#   .\start.ps1                       # standard launch on port 8000
#   .\start.ps1 -Port 8080
#   .\start.ps1 -NoBrowser
#   powershell -File .\start.ps1      # if you have execution policy issues
#
# What it does:
#   1. Activates .venv if present, otherwise warns.
#   2. Installs flask + flask-cors if missing (does NOT overwrite your requirements.txt).
#   3. Starts studio_api.py (Flask) on http://127.0.0.1:8000 by default.
#   4. Opens the default browser to the console (skip with -NoBrowser).

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$BindHost = "127.0.0.1",
    [switch]$NoBrowser,
    [switch]$DebugMode,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot

# 1. venv (look in this directory and one level up so the worktree shares the parent's venv)
$candidates = @(
    (Join-Path $repo ".venv\Scripts\python.exe"),
    (Join-Path (Split-Path -Parent $repo) ".venv\Scripts\python.exe"),
    (Join-Path (Split-Path -Parent (Split-Path -Parent $repo)) ".venv\Scripts\python.exe"),
    (Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $repo))) ".venv\Scripts\python.exe")
)
$py = $null
foreach ($c in $candidates) {
    if (Test-Path $c) { $py = $c; break }
}
if ($null -eq $py) {
    Write-Warning "No .venv found nearby - falling back to system 'python' on PATH."
    $py = "python"
} else {
    Write-Host "[aurora] using venv python: $py"
}

# 2. ensure flask is present (lightweight check; doesn't touch your requirements.txt)
if (-not $SkipInstall) {
    Write-Host "[aurora] checking flask + flask-cors..."
    & $py -c "import flask, flask_cors" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[aurora] installing flask + flask-cors"
        & $py -m pip install --quiet flask flask-cors
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "pip install returned non-zero; continuing anyway."
        }
    } else {
        Write-Host "[aurora] flask present"
    }
}

# 3. env
$env:AURORA_HOST = $BindHost
$env:AURORA_PORT = "$Port"
if ($DebugMode) { $env:AURORA_DEBUG = "1" }

# 4. browser (delayed so it opens AFTER Flask is up)
if (-not $NoBrowser) {
    $url = "http://${BindHost}:${Port}"
    Start-Job -ScriptBlock {
        param($u) Start-Sleep -Seconds 2; Start-Process $u
    } -ArgumentList $url | Out-Null
}

# 5. server
Set-Location $repo
Write-Host "[aurora] starting Flask on http://${BindHost}:${Port}"
Write-Host "[aurora] press Ctrl-C to stop"
& $py studio_api.py
