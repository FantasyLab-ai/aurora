Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Always run from repo root
Set-Location "C:\Users\bgrut\Desktop\FantasyAI"

$py = ".\.venv\Scripts\python.exe"
if (!(Test-Path $py)) { throw "Missing venv python at $py" }

& $py -m scripts.run_aurora_dataset_runner --dataset RANDOM
