Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root    = (Get-Location).Path
$regPath = Join-Path $root "fantasyai\aurora\datasets\registry.json"

if (-not (Test-Path $regPath)) { throw "Missing registry.json at: $regPath" }

# Read registry (handles BOM if present)
$raw = Get-Content $regPath -Raw -Encoding UTF8
$reg = $raw | ConvertFrom-Json

if (-not $reg.base_dir -or -not $reg.datasets) { throw "registry.json missing base_dir or datasets" }

function Set-Dataset {
  param(
    [Parameter(Mandatory=$true)][string]$Key,
    [Parameter(Mandatory=$true)][string]$Path,
    [string[]]$Tags = @()
  )

  # Create as a plain hashtable then overwrite/add
  $reg.datasets | Add-Member -NotePropertyName $Key -NotePropertyValue (@{
    enabled = $true
    path    = $Path
    tags    = $Tags
  }) -Force
}

# ---- Add/Update your new datasets ----
Set-Dataset "civic_nyc_collisions"      "civic_nyc_collisions/raw"      @("civic","transport","nyc","collisions","csv")
Set-Dataset "energy_eia_electric_power" "energy_eia_electric_power/raw" @("energy","eia","electric","power","csv","zip")
Set-Dataset "env_air_quality"           "env_air_quality/raw"           @("environment","air-quality","csv","zip")
Set-Dataset "env_marine_sample"         "env_marine_sample/raw"         @("environment","marine","csv")
Set-Dataset "macro_gdp"                 "macro_gdp/raw"                 @("macro","gdp","csv")
Set-Dataset "macro_cpi"                 "macro_cpi/raw"                 @("macro","cpi","inflation","csv")
Set-Dataset "macro_fred_misc"           "macro_fred_misc/raw"           @("macro","fred","csv")

# ---- Write back UTF-8 NO BOM ----
$json = $reg | ConvertTo-Json -Depth 30
[System.IO.File]::WriteAllText($regPath, $json, [System.Text.UTF8Encoding]::new($false))

Write-Host "[OK] Updated registry.json (UTF-8 no BOM)" -ForegroundColor Green
Write-Host "`n[CHECK] Dataset keys now in registry:" -ForegroundColor Cyan
((Get-Content $regPath -Raw) | ConvertFrom-Json).datasets.PSObject.Properties.Name | Sort-Object