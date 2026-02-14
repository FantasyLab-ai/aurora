param()

$path = ".\scripts\run_aurora_with_steps_1_4.py"
if (-not (Test-Path $path)) { throw "run_aurora_with_steps_1_4.py not found at $path" }

$ts  = Get-Date -Format "yyyyMMdd_HHmmss"
$bak = "$path.bak_insight_call_$ts"
Copy-Item $path $bak -Force

$src = Get-Content $path -Raw -Encoding UTF8

# Idempotent guard
if ($src -match "make_insight_plots" -and $src -match "insight plots") {
  Write-Host "ℹ️ Insight plots call already present. Backup: $bak"
  exit 0
}

# We insert a try/catch block immediately AFTER generate_all_artifacts(...) is invoked,
# by targeting the FIRST occurrence of a line containing 'generate_all_artifacts('.
$pattern = '(?m)^(?<indent>\s*)generate_all_artifacts\s*\(.*$'
$m = [regex]::Match($src, $pattern)
if (-not $m.Success) {
  throw "Could not find a generate_all_artifacts(...) call in $path. Backup: $bak"
}

$indent = $m.Groups["indent"].Value

$inject = @"
$indent# --- Insight plots (modern annotated variants) ---
$indenttry:
$indent    # scripts is a package when run as: python -m scripts.run_aurora_with_steps_1_4
$indent    from scripts.make_insight_plots import main as _make_insight_plots
$indent    _make_insight_plots(str(run_dir))
$indentexcept Exception as _e:
$indent    print(f"[warn] insight plots skipped: {type(_e).__name__}: {_e}")

"@

# Insert AFTER the line we matched (end of that line)
$lineStart = $m.Index
$lineEnd = $src.IndexOf("`n", $lineStart)
if ($lineEnd -lt 0) { $lineEnd = $src.Length }
$lineEndPlus = [Math]::Min($lineEnd + 1, $src.Length)

$src2 = $src.Insert($lineEndPlus, $inject)

Set-Content -Path $path -Value $src2 -Encoding UTF8

Write-Host "✅ Patched run_aurora_with_steps_1_4.py to call make_insight_plots after generate_all_artifacts()." -ForegroundColor Green
Write-Host "🧷 Backup: $bak" -ForegroundColor Yellow
