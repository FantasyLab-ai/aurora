param()

$path = ".\scripts\run_aurora_with_steps_1_4.py"
if (-not (Test-Path $path)) { throw "run_aurora_with_steps_1_4.py not found at $path (run from Aurora_QIE root)" }

$ts  = Get-Date -Format "yyyyMMdd_HHmmss"
$bak = "$path.bak_insight_call_$ts"
Copy-Item $path $bak -Force

$src = Get-Content $path -Raw -Encoding UTF8

# Idempotent guard
if ($src -match "make_insight_plots") {
  Write-Host "ℹ️ Insight plots call already present. Backup: $bak" -ForegroundColor Yellow
  exit 0
}

# Find the whole 'out = generate_all_artifacts(...)' call block (multiline)
$pattern = '(?s)^[ \t]*out\s*=\s*generate_all_artifacts\s*\(\s*.*?\n[ \t]*\)\s*'
$m = [regex]::Match($src, $pattern, [System.Text.RegularExpressions.RegexOptions]::Multiline)

if (-not $m.Success) {
  throw "Could not find the 'out = generate_all_artifacts(...)' block to inject after. Backup: $bak"
}

# Determine indentation of that block (usually 4 spaces inside main)
$indent = ""
$firstLine = $m.Value.Split("`n")[0]
$indentMatch = [regex]::Match($firstLine, '^[ \t]*')
if ($indentMatch.Success) { $indent = $indentMatch.Value }

$inject = @(
"",
"${indent}# --- Insight plots (modern annotated variants) ---",
"${indent}try:",
"${indent}    from scripts.make_insight_plots import main as _make_insight_plots",
"${indent}    _make_insight_plots(str(run_dir))",
"${indent}except Exception as _e:",
"${indent}    print(f""[WARN] insight plots skipped: {type(_e).__name__}: {_e}"", file=sys.stderr)",
""
) -join "`n"

$src2 = $src.Substring(0, $m.Index + $m.Length) + $inject + $src.Substring($m.Index + $m.Length)

if ($src2 -eq $src) { throw "Patch produced no changes (unexpected). Backup: $bak" }

Set-Content -Path $path -Value $src2 -Encoding UTF8

Write-Host "✅ Patched: run_aurora_with_steps_1_4.py now calls scripts.make_insight_plots after artifacts generation." -ForegroundColor Green
Write-Host "🧷 Backup: $bak" -ForegroundColor Yellow
