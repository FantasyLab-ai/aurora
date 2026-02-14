from __future__ import annotations
from pathlib import Path
import re
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    b.write_bytes(p.read_bytes())
    return b

def main() -> None:
    if not TARGET.exists():
        raise FileNotFoundError(f"Could not find: {TARGET}")

    src = TARGET.read_text(encoding="utf-8", errors="replace")
    bak = backup(TARGET)

    # --- 1) Insert helper functions if not present ---
    if "_is_geo_like(" not in src:
        insert_point = src.find("def _infer_target")
        if insert_point < 0:
            raise RuntimeError("Could not find insertion point near def _infer_target")

        helpers = r'''
def _is_geo_like(name: str) -> bool:
    n = (name or "").strip().lower()
    geo_tokens = [
        "lat", "latitude", "lon", "long", "longitude",
        "zip", "zipcode", "postal", "address",
        "x_coord", "y_coord", "geocode", "borough",
    ]
    return any(t in n for t in geo_tokens)

def _is_id_like(name: str) -> bool:
    n = (name or "").strip().lower()
    id_tokens = ["id", "uuid", "guid", "key", "nbr", "number", "encounter", "patient"]
    # allow true measures like "number_of_persons_injured" to pass later via scoring
    return any(n == t or n.endswith("_" + t) or n.startswith(t + "_") for t in id_tokens) or (
        ("id" in n or "uuid" in n or "guid" in n or "encounter" in n) and len(n) <= 24
    )

def _target_score(name: str) -> float:
    """
    Prefer meaningful measures (counts, totals, injuries, amounts),
    penalize geo + pure IDs.
    """
    n = (name or "").strip().lower()
    score = 0.0
    if _is_geo_like(n):
        score -= 10.0
    if _is_id_like(n):
        score -= 6.0

    good_tokens = ["count", "total", "sum", "amount", "value", "rate", "injur", "killed", "fatal", "crash", "collision"]
    for t in good_tokens:
        if t in n:
            score += 2.0

    # coordinates / trivial measures penalty
    bad_tokens = ["longitude", "latitude"]
    for t in bad_tokens:
        if t in n:
            score -= 8.0

    return score

def _prepare_model_frame(df: "pd.DataFrame", tcol: str | None, ycol: str, numeric_cols: list[str]) -> tuple["pd.DataFrame", str | None, str, str]:
    """
    If we have a time column, convert event-level rows into a stable time series
    by aggregating numeric columns over time buckets (daily by default).
    """
    try:
        import pandas as pd
        import numpy as np
    except Exception:
        return df, tcol, ycol, "prep_skipped:pandas_missing"

    if not tcol or tcol not in df.columns:
        return df, tcol, ycol, "prep_note:no_time_col"

    dt = pd.to_datetime(df[tcol], errors="coerce", infer_datetime_format=True)
    ok = dt.notna().mean()
    if ok < 0.20:
        return df, tcol, ycol, f"prep_note:time_parse_low_ok={ok:.2f}"

    # pick a reasonable bucket; daily is safest default
    freq = "D"

    work = df.copy()
    work["__t"] = dt
    work = work[work["__t"].notna()].copy()

    # keep only numeric columns that are real signals
    cols = [c for c in numeric_cols if c in work.columns]
    if len(cols) == 0:
        return df, tcol, ycol, "prep_note:no_numeric_cols"

    # Heuristic: sum “count-like” measures, average everything else
    def agg_for(c: str) -> str:
        n = c.lower()
        if any(t in n for t in ["count", "total", "sum", "injur", "killed", "fatal", "crash", "collision"]):
            return "sum"
        return "mean"

    agg_map = {c: agg_for(c) for c in cols}
    g = work.groupby(pd.Grouper(key="__t", freq=freq)).agg(agg_map)

    # If ycol is geo/id-like, prefer a derived event count target
    if _is_geo_like(ycol) or _is_id_like(ycol):
        g["EVENT_COUNT"] = 1.0
        ycol2 = "EVENT_COUNT"
    else:
        ycol2 = ycol

    out = g.reset_index().rename(columns={"__t": "__time"})
    # ensure y exists
    if ycol2 not in out.columns:
        out["EVENT_COUNT"] = 1.0
        ycol2 = "EVENT_COUNT"

    # drop all-null columns
    for c in list(out.columns):
        if c != "__time" and out[c].notna().sum() == 0:
            out.drop(columns=[c], inplace=True)

    return out, "__time", ycol2, f"prep_ok:aggregated_freq={freq}"
'''
        src = src[:insert_point] + helpers + "\n" + src[insert_point:]

    # --- 2) Replace _infer_target with a semantics-aware version ---
    pattern = r"def _infer_target\(df: pd\.DataFrame, numeric_cols: List\[str\]\) -> Optional\[str\]:.*?(?=\n\ndef |\n# |\Z)"
    m = re.search(pattern, src, flags=re.S)
    if not m:
        raise RuntimeError("Could not find _infer_target() block to replace.")

    replacement = r'''def _infer_target(df: pd.DataFrame, numeric_cols: List[str]) -> Optional[str]:
    """
    Pick a sane numeric target:
      - avoid obvious IDs and geo coordinates
      - prefer “count/total/injured/killed/value”-like signals
      - require enough non-missing values and non-trivial variance
    """
    best = None
    best_score = -1e18

    for c in numeric_cols:
        base = _target_score(c)

        v = pd.to_numeric(df[c], errors="coerce")
        ok = float(v.notna().mean())
        if ok < 0.70:
            continue

        sd = float(np.nanstd(v))
        if not np.isfinite(sd) or sd <= 0:
            continue

        # penalize almost-constant columns
        q10 = float(np.nanpercentile(v, 10))
        q90 = float(np.nanpercentile(v, 90))
        spread = q90 - q10
        if not np.isfinite(spread) or spread <= 0:
            continue

        score = base + (spread / (sd + 1e-9)) + (ok * 0.5)

        if score > best_score:
            best_score = score
            best = c

    return best
'''
    src = src[:m.start()] + replacement + src[m.end():]

    # --- 3) Patch compute_deep_math_v3 to aggregate into a stable model frame ---
    anchor = 'out["time_col"] = tcol\n    out["target_col"] = ycol\n'
    if anchor not in src:
        raise RuntimeError("Could not find anchor for time/target assignment in compute_deep_math_v3.")

    inject = (
        'out["time_col"] = tcol\n'
        '    out["target_col"] = ycol\n'
        '    # Prepare a stable model frame (aggregate event rows into time buckets if possible)\n'
        '    try:\n'
        '        df, tcol, ycol, prep_note = _prepare_model_frame(df=df, tcol=tcol, ycol=ycol, numeric_cols=numeric_cols)\n'
        '        out["prep_note"] = prep_note\n'
        '        out["time_col"] = tcol\n'
        '        out["target_col"] = ycol\n'
        '    except Exception as e:\n'
        '        out["prep_note"] = f"prep_failed:{type(e).__name__}:{e}"\n'
    )

    src = src.replace(anchor, inject)

    TARGET.write_text(src, encoding="utf-8")
    print(f"✅ Patched deep_math semantics + aggregation.\n   Backup: {bak}\n   File:   {TARGET}")

if __name__ == "__main__":
    main()
