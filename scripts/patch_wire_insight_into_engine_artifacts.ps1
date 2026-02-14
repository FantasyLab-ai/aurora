param()

$path = ".\fantasyai\aurora\engine_artifacts.py"
if (-not (Test-Path $path)) { throw "engine_artifacts.py not found at $path" }

$ts  = Get-Date -Format "yyyyMMdd_HHmmss"
$bak = "$path.bak_wire_insight_$ts"
Copy-Item $path $bak -Force

$src = Get-Content $path -Raw -Encoding UTF8

# Idempotent: if we already injected this block, exit
if ($src -match "Insight plots \(polished dark annotated variants\)") {
  Write-Host "ℹ️ engine_artifacts.py already wired for insight plots. Backup: $bak"
  exit 0
}

# Anchor on the plots index build (stable)
$pattern = '(?m)^(?<indent>\s*)plots_index_path\s*=\s*plots_dir\s*/\s*"_PLOTS_INDEX\.json"\s*$'
$m = [regex]::Match($src, $pattern)
if (-not $m.Success) {
  throw "Could not find plots_index_path = plots_dir / ""_PLOTS_INDEX.json"". Backup: $bak"
}

$indent = $m.Groups["indent"].Value

$inject = @"
${indent}# --- Insight plots (polished dark annotated variants) ---
${indent}try:
${indent}    from scripts.make_insight_plots import main as _make_insight_plots
${indent}    _make_insight_plots(str(run_dir))
${indent}except Exception:
${indent}    # Never fail the pipeline due to insight plots
${indent}    pass

"@

$src2 = $src.Substring(0, $m.Index) + $inject + $src.Substring($m.Index)

Set-Content -Path $path -Value $src2 -Encoding UTF8

Write-Host "✅ Wired insight plot generation into engine_artifacts.py (anchored before _PLOTS_INDEX.json)." -ForegroundColor Green
Write-Host "🧷 Backup: $bak" -ForegroundColor Yellow
