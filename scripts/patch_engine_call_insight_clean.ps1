param()

$path = ".\fantasyai\aurora\engine_artifacts.py"
if (-not (Test-Path $path)) { throw "engine_artifacts.py not found at $path (run from Aurora_QIE root)" }

$ts  = Get-Date -Format "yyyyMMdd_HHmmss"
$bak = "$path.bak_call_insight_$ts"
Copy-Item $path $bak -Force

$src = Get-Content $path -Raw -Encoding UTF8

# Idempotent: don't double-inject
if ($src -match "make_insight_plots" -or $src -match "Insight plots") {
  Write-Host "ℹ️ Insight plot call already present. Backup: $bak"
  exit 0
}

# Inject right after: _try_generate_plots(run_dir, plots_dir)
# This is robust to whitespace and quote styles.
$pattern = "(?m)^(\s*)_try_generate_plots\(\s*run_dir\s*,\s*plots_dir\s*\)\s*$"
$m = [regex]::Match($src, $pattern)
if (-not $m.Success) {
  throw "Could not find _try_generate_plots(run_dir, plots_dir) line to inject after. Backup: $bak"
}

$indent = $m.Groups[1].Value
$inject = @"
`n${indent}# --- Insight plots (modern annotated variants) ---
${indent}try:
${indent}    from scripts.make_insight_plots import main as _make_insight_plots
${indent}    _make_insight_plots(str(run_dir))
${indent}except Exception as _e:
${indent}    # never fail artifact generation because of insights
${indent}    try:
${indent}        print(f"[warn] insight plots skipped: {type(_e).__name__}: {_e}")
${indent}    except Exception:
${indent}        pass
`n
"@

$idx = $m.Index + $m.Length
$src2 = $src.Substring(0, $idx) + $inject + $src.Substring($idx)

if ($src2 -eq $src) { throw "Patch produced no changes (unexpected). Backup: $bak" }

Set-Content -Path $path -Value $src2 -Encoding UTF8

Write-Host "✅ Patched engine_artifacts.py to call scripts.make_insight_plots before _PLOTS_INDEX.json is built." -ForegroundColor Green
Write-Host "🧷 Backup: $bak" -ForegroundColor Yellow
