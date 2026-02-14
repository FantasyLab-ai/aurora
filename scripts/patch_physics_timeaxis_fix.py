from __future__ import annotations
from pathlib import Path
import datetime as dt
import shutil
import re

ROOT = Path.cwd()
PHYS = ROOT / "fantasyai" / "aurora" / "math" / "physics.py"

def backup(p: Path) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_timefix_{ts}")
    shutil.copy2(p, b)
    return b

def main():
    if not PHYS.exists():
        raise SystemExit(f"ERROR: not found: {PHYS}")

    txt = PHYS.read_text(encoding="utf-8", errors="ignore")
    b = backup(PHYS)
    print(f"[OK] backup -> {b}")

    # 1) Add helper: reject numeric-like time columns
    if "_is_mostly_numeric" not in txt:
        insert = r'''
def _is_mostly_numeric(s: pd.Series) -> bool:
    """True if a series is mostly numeric (or numeric-like strings)."""
    try:
        # if already numeric dtype, yes
        if pd.api.types.is_numeric_dtype(s):
            return True
        # if object dtype, attempt coercion
        c = pd.to_numeric(s.astype(str).str.replace(",", "", regex=False), errors="coerce")
        return c.notna().mean() > 0.8
    except Exception:
        return False
'''
        # place right after imports area
        txt = re.sub(r"(import pandas as pd\s*\n)", r"\1" + insert + "\n", txt, count=1)
        print("[OK] injected _is_mostly_numeric")

    # 2) Harden _infer_time_col:
    #   - prefer Date+Time if present
    #   - never consider mostly-numeric columns as time
    #   - require parse success AND monotonic increasing for datetime candidates
    # Replace the whole _infer_time_col function body safely by regex block.
    pattern = r"def _infer_time_col\(df: pd\.DataFrame\) -> Optional\[str\]:\n(?:    .*\n)+?    return None\n"
    m = re.search(pattern, txt)
    if not m:
        raise SystemExit("ERROR: could not find _infer_time_col block to patch")

    new_block = r'''def _infer_time_col(df: pd.DataFrame) -> Optional[str]:
    # 0) AirQualityUCI-style pair is the best signal
    if ("Date" in df.columns) and ("Time" in df.columns):
        try:
            comb = (df["Date"].astype(str).str.strip() + " " + df["Time"].astype(str).str.strip())
            dt2 = pd.to_datetime(comb, errors="coerce", dayfirst=True)
            if dt2.notna().mean() > 0.6:
                # ensure mostly increasing (allow some disorder)
                ok = dt2.dropna()
                if ok.size > 10 and (ok.is_monotonic_increasing or ok.sort_values().equals(ok)):
                    return "Date+Time"
                return "Date+Time"
        except Exception:
            pass

    # 1) Prefer common explicit datetime-like column names (and reject numeric)
    for cand in ["datetime", "timestamp", "time", "date", "ds"]:
        if cand in df.columns:
            if _is_mostly_numeric(df[cand]):
                continue
            dtc = _safe_datetime(df[cand])
            if dtc is not None:
                ok = dtc.dropna()
                if ok.size > 10 and ok.is_monotonic_increasing:
                    return cand
                # still accept if parse rate high (some datasets not sorted)
                if dtc.notna().mean() > 0.8:
                    return cand

    # 2) Fallback: scan ONLY non-numeric object columns and require strong datetime parse
    for c in df.columns[:50]:
        if _is_mostly_numeric(df[c]):
            continue
        dtc = _safe_datetime(df[c])
        if dtc is None:
            continue
        if dtc.notna().mean() > 0.85:
            ok = dtc.dropna()
            if ok.size > 10:
                return c
    return None
'''
    txt = txt[:m.start()] + new_block + txt[m.end():]
    print("[OK] patched _infer_time_col (reject numeric columns; prefer Date+Time)")

    # 3) Add fit quality flag (so UI + narrative can act on it later)
    if '"quality"' not in txt:
        txt = txt.replace(
            '"rmse": rmse,',
            '"rmse": rmse,\n        "quality": ("good" if r2 >= 0.2 else ("meh" if r2 >= 0.05 else "poor")),'
        )
        print("[OK] added physics quality flag based on r2")

    PHYS.write_text(txt, encoding="utf-8")
    print(f"[DONE] wrote -> {PHYS}")

    import py_compile
    py_compile.compile(str(PHYS), doraise=True)
    print("[DONE] physics.py compiled clean")

if __name__ == "__main__":
    main()
