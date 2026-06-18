# =====================================================================
# Aurora desktop -- single-terminal launcher.
# ---------------------------------------------------------------------
# Activates the Python venv, starts studio_api.py in a background job,
# waits for it to answer /api/preflight, then launches the Tauri shell.
# On exit (closing the Aurora window) the backend job is stopped cleanly.
#
# Usage -- from ANY directory, fresh PowerShell:
#
#     .\desktop\launch.ps1                # uses prebuilt desktop.exe
#     .\desktop\launch.ps1 -DevMode       # uses `npm run tauri dev` (hot reload)
#     .\desktop\launch.ps1 -AuroraPort 8002
#
# If the prebuilt .exe is missing, the script tells you to run
# `npm run tauri build` (or use -DevMode) instead of failing silently.
# =====================================================================
[CmdletBinding()]
param(
    [string] $AuroraPort = "8001",
    [switch] $DevMode    = $false,
    [int]    $WaitSeconds = 30
)

$ErrorActionPreference = "Stop"

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorktreeRoot = Split-Path -Parent $ScriptDir
$StudioApi    = Join-Path $WorktreeRoot "studio_api.py"

function Write-Step($n, $total, $msg) {
    Write-Host ("[{0}/{1}] {2}" -f $n, $total, $msg) -ForegroundColor Cyan
}

function Find-AuroraVenv {
    # Order: worktree-local, main repo (parent of .claude/), then env override.
    $cands = @(
        (Join-Path $WorktreeRoot ".venv"),
        (Join-Path (Split-Path -Parent (Split-Path -Parent $WorktreeRoot)) ".venv")
    )
    if ($env:AURORA_VENV) { $cands = @($env:AURORA_VENV) + $cands }
    foreach ($p in $cands) {
        if ($p -and (Test-Path (Join-Path $p "Scripts\Activate.ps1"))) {
            return $p
        }
    }
    return $null
}


# 1. Reload PATH so freshly-installed Rust / Cargo are visible.
Write-Step 1 4 "Reloading PATH (picks up Rust if it was installed in another session)..."
$env:Path = [Environment]::GetEnvironmentVariable("Path","User") + ";" + [Environment]::GetEnvironmentVariable("Path","Machine")

# 2. Locate the venv.
Write-Step 2 4 "Locating Aurora venv..."
$venv = Find-AuroraVenv
if (-not $venv) {
    Write-Host "  ERROR: No .venv found." -ForegroundColor Red
    Write-Host "  Create one at the repo root:" -ForegroundColor Yellow
    Write-Host "    python -m venv .venv" -ForegroundColor Yellow
    Write-Host "    .\.venv\Scripts\Activate.ps1" -ForegroundColor Yellow
    Write-Host "    pip install -r requirements.txt" -ForegroundColor Yellow
    Write-Host "  Or set `$env:AURORA_VENV to point at it explicitly." -ForegroundColor Yellow
    exit 1
}
$pythonExe = Join-Path $venv "Scripts\python.exe"
Write-Host "  using $pythonExe" -ForegroundColor DarkGray

# 3. Start Aurora backend in a background job.
Write-Step 3 4 "Starting Aurora backend on port $AuroraPort..."
$env:AURORA_PORT = $AuroraPort
$auroraJob = Start-Job -Name "AuroraStudio" -ScriptBlock {
    param($Python, $Script, $Port, $Cwd)
    Set-Location $Cwd
    $env:AURORA_PORT = $Port
    & $Python $Script 2>&1
} -ArgumentList $pythonExe, $StudioApi, $AuroraPort, $WorktreeRoot
Write-Host "  job id: $($auroraJob.Id) | log: Receive-Job $($auroraJob.Id)" -ForegroundColor DarkGray

# Poll /api/preflight so we don't open the desktop while Aurora is mid-boot.
$ready = $false
for ($i = 0; $i -lt $WaitSeconds; $i++) {
    Start-Sleep -Seconds 1
    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:$AuroraPort/api/preflight" -TimeoutSec 1
        $ready = $true
        break
    } catch { }
}
if (-not $ready) {
    Write-Host "  WARN: Aurora didn't answer /api/preflight within $WaitSeconds s -- continuing anyway." -ForegroundColor Yellow
    Write-Host "        Run 'Receive-Job $($auroraJob.Id)' in another shell to inspect its log." -ForegroundColor DarkGray
} else {
    Write-Host "  Aurora is up at http://127.0.0.1:$AuroraPort" -ForegroundColor Green
}

# 4. Launch the desktop shell.
Write-Step 4 4 "Launching Aurora desktop..."
try {
    if ($DevMode) {
        Set-Location $ScriptDir
        npm run tauri dev
    } else {
        $exe = Join-Path $ScriptDir "src-tauri\target\release\desktop.exe"
        if (-not (Test-Path $exe)) {
            $exe = Join-Path $ScriptDir "src-tauri\target\debug\desktop.exe"
        }
        if (-not (Test-Path $exe)) {
            Write-Host "  ERROR: No built desktop.exe under src-tauri\target\(debug|release)\." -ForegroundColor Red
            Write-Host "  Build it once with:    cd desktop ; npm install ; npm run tauri build" -ForegroundColor Yellow
            Write-Host "  Or run with hot reload:   .\desktop\launch.ps1 -DevMode" -ForegroundColor Yellow
        } else {
            Write-Host "  running $exe" -ForegroundColor DarkGray
            & $exe
        }
    }
} finally {
    # Clean up Aurora background job when the desktop window closes.
    Write-Host ""
    Write-Host "Desktop closed. Stopping Aurora backend..." -ForegroundColor Cyan
    if ($auroraJob) {
        Stop-Job -Job $auroraJob -ErrorAction SilentlyContinue
        Remove-Job -Job $auroraJob -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Done." -ForegroundColor Green
}
