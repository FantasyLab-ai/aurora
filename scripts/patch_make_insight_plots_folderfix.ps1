param()

$path = ".\scripts\make_insight_plots.py"
if (-not (Test-Path $path)) { throw "make_insight_plots.py not found at $path (run from Aurora_QIE root)" }

$ts  = Get-Date -Format "yyyyMMdd_HHmmss"
$bak = "$path.bak_folderfix_$ts"
Copy-Item $path $bak -Force

$src = Get-Content $path -Raw -Encoding UTF8

# Idempotent: if already has the helper, exit safely
if ($src -match "def\s+_pick_latest_csv") {
  Write-Host "ℹ️ make_insight_plots.py already patched for folder CSV picking. Backup: $bak"
  exit 0
}

# We require Path + pandas usage already exists in file; we just inject helpers + override _load_dataset safely.
# Strategy:
# 1) Insert helper after "import pandas as pd" (robust anchor).
# 2) Append a NEW _load_dataset definition (same name) after the existing one (Python will use the last definition).

$anchor = "import pandas as pd"
$pos = $src.IndexOf($anchor)
if ($pos -lt 0) { throw "Could not find anchor: 'import pandas as pd'. Backup: $bak" }

$lineEnd = $src.IndexOf("`n", $pos)
if ($lineEnd -lt 0) { $lineEnd = $pos + $anchor.Length }

$helper = @"

def _pick_latest_csv(path: Path) -> Path | None:
    \"\"\"If `path` is a directory, pick newest CSV inside it (recursive).\"\"\"
    try:
        if path.is_file() and path.suffix.lower() == ".csv":
            return path
        if path.is_dir():
            csvs = [p for p in path.glob("**/*.csv") if p.is_file()]
            if not csvs:
                return None
            csvs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return csvs[0]
    except Exception:
        return None
    return None

"@

$insertAt = $lineEnd + 1
$src2 = $src.Substring(0, $insertAt) + $helper + $src.Substring($insertAt)

# Append an override _load_dataset (last definition wins in Python)
$override = @"

# --- Folder-safe loader override (last definition wins) ---
def _load_dataset(csv_path: Path) -> pd.DataFrame:
    p = Path(csv_path)

    # Allow passing a folder (raw dir). Pick newest CSV recursively.
    picked = _pick_latest_csv(p)
    if picked is None:
        raise FileNotFoundError(f"No .csv found under: {p}")
    p = picked

    df = pd.read_csv(p)
    return df
"@

$src3 = $src2 + $override

if ($src3 -eq $src) { throw "Patch produced no changes (unexpected). Backup: $bak" }

Set-Content -Path $path -Value $src3 -Encoding UTF8

Write-Host "✅ Patched make_insight_plots.py to support dataset folders (recursive newest CSV pick)." -ForegroundColor Green
Write-Host "🧷 Backup: $bak" -ForegroundColor Yellow
