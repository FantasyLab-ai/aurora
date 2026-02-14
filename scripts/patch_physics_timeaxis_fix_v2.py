from __future__ import annotations
from pathlib import Path
import datetime as dt
import shutil
import re

ROOT = Path.cwd()
PHYS = ROOT / "fantasyai" / "aurora" / "math" / "physics.py"

def backup(p: Path) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_timefixv2_{ts}")
    shutil.copy2(p, b)
    return b

def find_infer_time_func(src: str) -> tuple[str, int, int] | None:
    """
    Find a top-level function whose name contains 'infer' and 'time'.
    Return (func_name, start_idx, end_idx) where end_idx is start of next top-level def or EOF.
    """
    # Find all top-level defs
    defs = list(re.finditer(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", src, flags=re.M))
    for i, m in enumerate(defs):
        name = m.group(1)
        lname = name.lower()
        if ("infer" in lname) and ("time" in lname):
            start = m.start()
            end = defs[i + 1].start() if i + 1 < len(defs) else len(src)
            return (name, start, end)
    return None

def main():
    if not PHYS.exists():
        raise SystemExit(f"ERROR: not found: {PHYS}")

    txt = PHYS.read_text(encoding="utf-8", errors="ignore")
    b = backup(PHYS)
    print(f"[OK] backup -> {b}")

    hit = find_infer_time_func(txt)
    if not hit:
        raise SystemExit("ERROR: could not find any top-level function containing 'infer' and 'time' in physics.py")

    func_name, start, end = hit
    print(f"[OK] found time inference function: {func_name}")

    # Ensure pandas import exists (most likely already does)
    if "import pandas as pd" not in txt:
        # insert near top after other imports
        txt = re.sub(r"(^from __future__.*\n)", r"\1import pandas as pd\n", txt, count=1, flags=re.M)

    # Inject helper if missing
    if "_is_mostly_numeric" not in txt:
        helper = r'''

def _is_mostly_numeric(s: "pd.Series") -> bool:
    """True if a series is mostly numeric (or numeric-like strings)."""
    try:
        import pandas as pd  # local import safe
        if pd.api.types.is_numeric_dtype(s):
            return True
        c = pd.to_numeric(s.astype(str).str.replace(",", "", regex=False), errors="coerce")
        return c.notna().mean() > 0.8
    except Exception:
        return False
'''
        # place after pandas import
        txt = re.sub(r"(import pandas as pd\s*\n)", r"\1" + helper + "\n", txt, count=1)
        print("[OK] injected _is_mostly_numeric")

    # Inject / ensure _safe_datetime exists (most physics.py already has it; if not, add minimal)
    if "_safe_datetime" not in txt:
        safe_dt = r'''

def _safe_datetime(s: "pd.Series"):
    """Return parsed datetime series or None."""
    try:
        import pandas as pd
        dtc = pd.to_datetime(s, errors="coerce", infer_datetime_format=True)
        if dtc.notna().mean() < 0.5:
            return None
        return dtc
    except Exception:
        return None
'''
        txt = re.sub(r"(_is_mostly_numeric.*?\n)\n", r"\1" + safe_dt + "\n", txt, count=1, flags=re.S)
        print("[OK] injected _safe_datetime")

    # Replace the discovered infer-time function with hardened logic.
    # Keep the original name so callers keep working.
    new_block = f'''def {func_name}(df):
    """
    Robust time-axis inference:
      - Prefer AirQualityUCI Date+Time pairing
      - Never select mostly-numeric sensor columns as time
      - Require high parse success for datetime candidates
    Returns either:
      - "Date+Time" sentinel (caller should construct datetime from Date + Time), OR
      - column name, OR
      - None
    """
    import pandas as pd

    # 0) Prefer Date + Time pair (AirQualityUCI)
    if ("Date" in df.columns) and ("Time" in df.columns):
        try:
            comb = (df["Date"].astype(str).str.strip() + " " + df["Time"].astype(str).str.strip())
            dt2 = pd.to_datetime(comb, errors="coerce", dayfirst=True)
            if dt2.notna().mean() > 0.6:
                return "Date+Time"
        except Exception:
            pass

    # 1) Prefer explicit datetime-ish names, reject numeric
    for cand in ["datetime", "timestamp", "time", "date", "ds"]:
        if cand in df.columns:
            if _is_mostly_numeric(df[cand]):
                continue
            dtc = _safe_datetime(df[cand])
            if dtc is not None and dtc.notna().mean() > 0.8:
                return cand

    # 2) Fallback scan: only non-numeric columns, require very high parse rate
    for c in list(df.columns)[:50]:
        if _is_mostly_numeric(df[c]):
            continue
        dtc = _safe_datetime(df[c])
        if dtc is not None and dtc.notna().mean() > 0.85:
            return c

    return None
'''

    txt2 = txt[:start] + new_block + txt[end:]
    PHYS.write_text(txt2, encoding="utf-8")
    print(f"[DONE] patched -> {PHYS}")

    import py_compile
    py_compile.compile(str(PHYS), doraise=True)
    print("[DONE] physics.py compiled clean")

if __name__ == "__main__":
    main()
