param(
  [int]$Minutes = 10
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (!(Test-Path $py)) { throw "Missing venv python at $py" }

$end = (Get-Date).AddMinutes($Minutes)

while ((Get-Date) -lt $end) {
  & $py -m scripts.run_aurora_dataset_runner --dataset RANDOM | Tee-Object -FilePath ".\outputs\aurora_dataset_runs\_console_log.txt" -Append
  Start-Sleep -Seconds 60
}
