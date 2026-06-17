# =====================================================================
# Aurora demos — load webhook URLs into the current PowerShell session.
#
# Usage:
#     # From the worktree root:
#     . .\demos\load_env.ps1
#
# Note the leading dot + space: that's "dot-source" syntax, which
# makes the $env: changes stick in YOUR shell instead of being lost
# when the script exits.
#
# After running this once per terminal you can launch the relay:
#     python -m demos.relay.app
# and it will pick up the webhook URLs from the environment.
#
# This script reads ./demos/.env.demos (or a path you pass as -EnvPath).
# That file is gitignored. Never commit it.
# =====================================================================

[CmdletBinding()]
param(
    [string] $EnvPath = "demos/.env.demos"
)

if (-not (Test-Path $EnvPath)) {
    Write-Host "  .env.demos not found at: $EnvPath" -ForegroundColor Yellow
    Write-Host "  Copy demos/.env.demos.example to demos/.env.demos and paste your URLs." -ForegroundColor Yellow
    return
}

$loaded = 0
$secrets = @{}

foreach ($line in Get-Content $EnvPath) {
    # Skip blanks and comments.
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    $trimmed = $line.Trim()
    if ($trimmed.StartsWith("#")) { continue }
    # KEY=VALUE form.
    $idx = $trimmed.IndexOf("=")
    if ($idx -lt 1) { continue }
    $key = $trimmed.Substring(0, $idx).Trim()
    $val = $trimmed.Substring($idx + 1).Trim()
    # Strip surrounding quotes if present.
    if ($val.Length -ge 2) {
        if (($val[0] -eq '"' -and $val[-1] -eq '"') -or
            ($val[0] -eq "'" -and $val[-1] -eq "'")) {
            $val = $val.Substring(1, $val.Length - 2)
        }
    }
    if ($val -eq "") { continue }   # don't blank-out an inherited env var
    Set-Item -Path "Env:$key" -Value $val
    $loaded++
    # Mask the value for the on-screen confirmation — never print the secret.
    if ($val.Length -gt 14) {
        $masked = $val.Substring(0, 12) + "..."
    } else {
        $masked = "(short)"
    }
    $secrets[$key] = $masked
}

Write-Host ""
Write-Host "  Aurora demos env loaded ($loaded vars from $EnvPath)" -ForegroundColor Green
foreach ($key in ($secrets.Keys | Sort-Object)) {
    Write-Host ("    {0,-34} {1}" -f $key, $secrets[$key]) -ForegroundColor DarkGray
}

# The demos POST from Aurora to the local relay at http://127.0.0.1:7077.
# Aurora's WebhookAction has an SSRF guard that refuses loopback targets
# by default; the demos require this opt-in. Skipping this is the #1
# reason a user sees "all 3 contracts matched, 0 fully fired" with the
# error "webhook target '127.0.0.1' resolves to a private/loopback address".
# Set unconditionally here so BOTH the Aurora terminal and the relay
# terminal that dot-source this script get it.
if (-not $env:AURORA_ALLOW_LOCAL_WEBHOOKS) {
    $env:AURORA_ALLOW_LOCAL_WEBHOOKS = "1"
    Write-Host ("    {0,-34} {1}" -f "AURORA_ALLOW_LOCAL_WEBHOOKS", "1 (auto-set for demos)") -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "  Now you can run:  python -m demos.relay.app" -ForegroundColor Cyan
Write-Host "  (or:              python studio_api.py    for Aurora Studio)" -ForegroundColor Cyan
Write-Host ""
