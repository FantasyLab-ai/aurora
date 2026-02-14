param()

$path = ".\scripts\run_aurora_with_steps_1_4.py"
if (-not (Test-Path $path)) { throw "Not found: $path (run this from Aurora_QIE repo root)" }

$ts  = Get-Date -Format "yyyyMMdd_HHmmss"
$bak = "$path.bak_insight_call_$ts"
Copy-Item $path $bak -Force

$src = Get-Content $path -Raw -Encoding UTF8

# Idempotent: if already patched, exit cleanly
if ($src -match "make_insight_plots" -and $src -match "_make_insight_plots") {
  Write-Host "ℹ️ Insight-plot call already present. No changes. Backup: $bak"
  exit 0
}

# We inject immediately BEFORE the first generate_all_artifacts( call
$rx = [regex]'(?m)^(?<indent>\s*)generate_all_artifacts\s*\('
$m = $rx.Match($src)
if (-not $m.Success) {
  throw "Could not find 'generate_all_artifacts(' in run_aurora_with_steps_1_4.py. Backup: $bak"
}

$indent = $m.Groups["indent"].Value

$inject = @"
${indent}# --- Insight plots (modern annotated variants) ---
${indent}try:
${indent}    from .make_insight_plots import main as _make_insight_plots
${indent}    _make_insight_plots(run_dir)
${indent}except Exception as _e:
${indent}    print(f"[warn] insight plots skipped: {type(_e).__name__}: {_e}")

${indent}
"@

$src2 = $src.Substring(0, $m.Index) + $inject + $src.Substring($m.Index)

if ($src2 -eq $src) { throw "Patch produced no changes (unexpected). Backup: $bak" }

Set-Content -Path $path -Value $src2 -Encoding UTF8

Write-Host "✅ Patched: run_aurora_with_steps_1_4.py now calls make_insight_plots(run_dir) before artifacts are indexed." -ForegroundColor Green
Write-Host "🧷 Backup: $bak" -ForegroundColor Yellow
