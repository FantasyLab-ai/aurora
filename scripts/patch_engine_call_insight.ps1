param()

$path = ".\fantasyai\aurora\engine_artifacts.py"
if (-not (Test-Path $path)) { throw "engine_artifacts.py not found at $path (run from Aurora_QIE root)" }

$ts  = Get-Date -Format "yyyyMMdd_HHmmss"
$bak = "$path.bak_call_insight_$ts"
Copy-Item $path $bak -Force

$src = Get-Content $path -Raw -Encoding UTF8

# Idempotent: if already wired, exit clean
if ($src -match "make_insight_plots") {
  Write-Host "ℹ️ engine_artifacts.py already calls make_insight_plots. Backup: $bak"
  exit 0
}

# Anchor: right after baseline plot generation
$anchor = "_try_generate_plots(run_dir, plots_dir)"
$pos = $src.IndexOf($anchor)
if ($pos -lt 0) { throw "Could not find anchor '$anchor' in engine_artifacts.py. Backup: $bak" }

# Find line end and capture indentation from that line
$lineEnd = $src.IndexOf("`n", $pos)
if ($lineEnd -lt 0) { $lineEnd = $pos + $anchor.Length }

# Determine indent of the anchor line
$lineStart = $src.LastIndexOf("`n", $pos)
if ($lineStart -lt 0) { $lineStart = 0 } else { $lineStart = $lineStart + 1 }
$lineText = $src.Substring($lineStart, ($lineEnd - $lineStart))
$indent = ""
if ($lineText -match '^(\s*)') { $indent = $Matches[1] }

$inject = @"
$indent
$indent# --- Insight plots (modern annotated variants) ---
$indenttry:
$indent    from scripts.make_insight_plots import main as _make_insight_plots
$indent    _make_insight_plots(str(run_dir))
$indentexcept Exception as _e:
$indent    try:
$indent        debug.setdefault("skipped", []).append({"insight_plots": f"{type(_e).__name__}:{_e}"})
$indent    except Exception:
$indent        pass
"@

# Insert injection after the anchor line
$insertAt = $lineEnd + 1
if ($insertAt -gt $src.Length) { $insertAt = $src.Length }
$src2 = $src.Substring(0, $insertAt) + $inject + $src.Substring($insertAt)

Set-Content -Path $path -Value $src2 -Encoding UTF8

Write-Host "✅ Patched engine_artifacts.py: insight plots run after baseline plots, before plots index build." -ForegroundColor Green
Write-Host "🧷 Backup: $bak" -ForegroundColor Yellow
