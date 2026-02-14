param()

$path = ".\fantasyai\aurora\engine_artifacts.py"
if (-not (Test-Path $path)) { throw "engine_artifacts.py not found at $path (run from Aurora_QIE root)" }

$ts  = Get-Date -Format "yyyyMMdd_HHmmss"
$bak = "$path.bak_call_insight_$ts"
Copy-Item $path $bak -Force

$src = Get-Content $path -Raw -Encoding UTF8

# Idempotent guard
if ($src -match "scripts\.make_insight_plots" -or $src -match "Insight plots \(modern annotated variants\)") {
  Write-Host "ℹ️ Insight plot call already present. Backup: $bak" -ForegroundColor Yellow
  exit 0
}

# Find a line that calls _try_generate_plots(...), regardless of args/spacing
$pattern = '(?m)^(?<indent>\s*).*_try_generate_plots\s*\(.*\)\s*$'
$m = [regex]::Match($src, $pattern)
if (-not $m.Success) {
  throw "Could not find any line calling _try_generate_plots(...). Backup: $bak"
}

$indent = $m.Groups["indent"].Value

# Insert after the end of the matched line
$lineStart = $m.Index
$lineEnd = $src.IndexOf("`n", $lineStart)
if ($lineEnd -lt 0) { $lineEnd = $src.Length - 1 }

$inject = @(
"",
"${indent}# --- Insight plots (modern annotated variants) ---",
"${indent}try:",
"${indent}    from scripts.make_insight_plots import main as _make_insight_plots",
"${indent}    _make_insight_plots(str(run_dir))",
"${indent}except Exception as _e:",
"${indent}    # never fail artifact generation because of insights",
"${indent}    try:",
"${indent}        print(f`"[warn] insight plots skipped: {type(_e).__name__}: {_e}`")",
"${indent}    except Exception:",
"${indent}        pass",
""
) -join "`n"

$src2 = $src.Substring(0, $lineEnd + 1) + $inject + $src.Substring($lineEnd + 1)

if ($src2 -eq $src) { throw "Patch produced no changes (unexpected). Backup: $bak" }

Set-Content -Path $path -Value $src2 -Encoding UTF8

Write-Host "✅ Patched engine_artifacts.py to call scripts.make_insight_plots after _try_generate_plots(...)." -ForegroundColor Green
Write-Host "🧷 Backup: $bak" -ForegroundColor Yellow
