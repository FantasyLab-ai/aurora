from __future__ import annotations

import re
import hashlib
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
TS = datetime.now().strftime("%Y%m%d_%H%M%S")

def sha(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()[:12]

def backup(p: Path) -> None:
    b = p.with_suffix(p.suffix + f".bak_{TS}")
    b.write_bytes(p.read_bytes())

def write(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8", newline="\n")

def ensure_forecast_with_bands(fp: Path) -> None:
    txt = fp.read_text(encoding="utf-8")
    if re.search(r"(?m)^def\s+forecast_with_bands\s*\(", txt):
        print(f"[OK] forecast_with_bands already present -> {fp} (sha={sha(fp)})")
        return

    shim = """
# ------------------------------
# Compatibility shim (required by deep_math.py)
# ------------------------------
def forecast_with_bands(
    df,
    target_col: str,
    time_col: str | None = None,
    horizon: int = 30,
    seed: int = 0,
    method: str = "naive",
):
    \"\"\"Simple forecast with uncertainty bands (compat shim).\"\"\"
    import numpy as np
    import pandas as pd

    s = pd.to_numeric(df.get(target_col), errors="coerce").dropna()
    horizon = int(max(1, horizon))

    if len(s) < 5:
        y = [float("nan")] * horizon
        return {
            "target_col": target_col,
            "horizon": horizon,
            "method": method,
            "yhat": y,
            "lower": y,
            "upper": y,
            "note": "insufficient_data",
        }

    y = s.to_numpy(dtype=float)
    last = float(y[-1])

    diffs = np.diff(y)
    mad = float(np.nanmedian(np.abs(diffs))) if len(diffs) else 0.0
    if (not np.isfinite(mad)) or mad == 0.0:
        mad = float(np.nanstd(y)) * 0.25

    yhat = np.full(horizon, last, dtype=float)
    lower = yhat - 1.96 * mad
    upper = yhat + 1.96 * mad

    return {
        "target_col": target_col,
        "horizon": horizon,
        "method": method,
        "yhat": yhat.tolist(),
        "lower": lower.tolist(),
        "upper": upper.tolist(),
        "note": "compat_shim",
    }
"""
    write(fp, txt.rstrip() + "\n\n" + shim.strip() + "\n")
    print(f"[OK] added forecast_with_bands -> {fp} (sha={sha(fp)})")

def repair_deep_math(dp: Path) -> None:
    txt = dp.read_text(encoding="utf-8")

    # Ensure import re exists
    if not re.search(r"(?m)^\s*import\s+re\s*$", txt):
        m = re.search(r"(?m)^(import\s+.+|from\s+.+\s+import\s+.+)\s*$", txt)
        if m:
            ins = m.end()
            txt = txt[:ins] + "\nimport re\n" + txt[ins:]
        else:
            txt = "import re\n" + txt

    # Remove any prior canonical blocks (including corrupted ones with backslashes)
    txt = re.sub(
        r"(?s)\n#\s*-+\s*\n#\s*Canonical deep-math entrypoint.*?(?=\n#\s*-+\s*\n|\Z)",
        "\n",
        txt,
        flags=re.M,
    )

    # If previous patch inserted literal \"\"\", clean them globally (safe)
    txt = txt.replace(r"\"\"\"", '"""')
    txt = txt.replace("target...l", "target_col")

    canonical = """
# ------------------------------
# Canonical deep-math entrypoint
# ------------------------------
def compute_deep_math_v3(df, seed: int = 0, max_corrs: int = 200):
    \"\"\"Stable deep-math entrypoint used by the dataset runner.\"\"\"

    # Prefer v2/v1 if present
    fn2 = globals().get("compute_deep_math_v2")
    if callable(fn2):
        return fn2(df, seed=seed, max_corrs=max_corrs)

    fn1 = globals().get("compute_deep_math_v1")
    if callable(fn1):
        return fn1(df, seed=seed)

    # Minimal fallback: correlations + BH-FDR on numeric columns
    import numpy as np
    import pandas as pd
    from scipy import stats as st

    out = {"version": "deep_math_v3_fallback", "seed": int(seed), "rows": int(len(df))}

    cols = []
    for c in df.columns:
        v = pd.to_numeric(df[c], errors="coerce")
        if v.notna().sum() >= 10 and v.nunique(dropna=True) > 1:
            cols.append(c)

    out["numeric_cols_used"] = cols
    out["numeric_cols_n"] = len(cols)

    pairs = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            a, b = cols[i], cols[j]
            x = pd.to_numeric(df[a], errors="coerce").to_numpy()
            y = pd.to_numeric(df[b], errors="coerce").to_numpy()
            m = np.isfinite(x) & np.isfinite(y)
            n = int(m.sum())
            if n < 25:
                continue
            try:
                r, p = st.pearsonr(x[m], y[m])
            except Exception:
                continue
            pairs.append((abs(float(r)), a, b, float(r), float(p), n))

    pairs.sort(reverse=True)
    pairs = pairs[: int(max_corrs)]

    ps = np.array([p for _, _, _, _, p, _ in pairs], dtype=float)
    q_full = np.ones_like(ps)
    if len(ps):
        order = np.argsort(ps)
        ranked = ps[order]
        m = len(ps)
        q = np.empty_like(ranked)
        prev = 1.0
        for k in range(m - 1, -1, -1):
            val = ranked[k] * m / (k + 1)
            prev = min(prev, val)
            q[k] = prev
        q_full[order] = q

    top = []
    for idx, item in enumerate(pairs):
        _, a, b, r, p, n = item
        qv = float(q_full[idx]) if len(q_full) else 1.0
        top.append(
            {
                "a": a,
                "b": b,
                "n": n,
                "pearson_r": r,
                "pearson_p": p,
                "pearson_q": qv,
                "pearson_reject_fdr05": qv <= 0.05,
            }
        )

    out["hypothesis_testing"] = {"note": "fallback", "fdr": {"alpha": 0.05, "method": "BH"}, "top_correlations": top}
    return out

# Runner uses this alias
compute_deep_math = compute_deep_math_v3
"""
    txt = txt.rstrip() + "\n\n" + canonical.strip() + "\n"
    write(dp, txt)
    print(f"[OK] repaired deep_math -> {dp} (sha={sha(dp)})")

def patch_runner(rp: Path) -> None:
    txt = rp.read_text(encoding="utf-8")

    # Remove any deep_math imports and re-add canonical import
    txt = re.sub(r"(?m)^\s*from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+.*$", "", txt)
    txt = re.sub(r"(?m)^\s*import\s+.*deep_math.*$", "", txt)

    # Remove previous broken deep-math blocks
    txt = re.sub(r"(?s)\n\s*#\s*---\s*deep math.*?\n(?=\s*#|\s*\Z)", "\n", txt)
    txt = re.sub(r"(?s)\n.*deep_math\.json.*?\n(?=\s*#|\s*\Z)", "\n", txt)
    txt = re.sub(r"(?s)\n\s*if\s+getattr\(args,\s*['\"]deep_math['\"].*?\n(?=\s*#|\s*\Z)", "\n", txt)

    # Insert canonical import after imports
    lines = txt.splitlines()
    last_imp = 0
    for i, ln in enumerate(lines[:120], 1):
        if ln.startswith("import ") or ln.startswith("from "):
            last_imp = i

    import_block = [
        "",
        "# deep math (canonical import)",
        "from fantasyai.aurora.math.deep_math import compute_deep_math_v3 as compute_deep_math",
        "",
    ]
    lines = lines[:last_imp] + import_block + lines[last_imp:]
    txt = "\n".join(lines)

    # Ensure argparse flag exists only once (safe-add)
    # Remove any existing add_argument for --deep-math
    txt = re.sub(r"(?m)^\s*ap\.add_argument\(\s*['\"]--deep-math['\"].*$", "", txt)

    safe_flag = "\n".join([
        "    # deep math (safe-add; avoids argparse duplicate conflicts)",
        "    try:",
        "        ap.add_argument('--deep-math', action='store_true', help='run deep math extras (writes deep_math.json)')",
        "    except Exception:",
        "        pass",
    ]) + "\n"

    # Insert after --math-v2 add_argument if present else near parser setup
    m = re.search(r"(?m)^\s*ap\.add_argument\(\s*['\"]--math-v2['\"].*$", txt)
    if m:
        insert_at = txt.find("\n", m.end())
        txt = txt[:insert_at+1] + safe_flag + txt[insert_at+1:]
    else:
        m2 = re.search(r"(?m)^\s*ap\s*=\s*argparse\.ArgumentParser", txt)
        if m2:
            insert_at = txt.find("\n", m2.end())
            txt = txt[:insert_at+1] + safe_flag + txt[insert_at+1:]
        else:
            txt = safe_flag + txt

    # Insert canonical deep-math execution block right after math_results.json write_text call
    wm = re.search(r"(?m)^(?P<indent>\s*)\(run_dir\s*/\s*['\"]math_results\.json['\"]\)\.write_text\(", txt)
    if wm:
        indent = wm.group("indent")

        # IMPORTANT: doubled braces so runner gets f-string braces, not patcher evaluation
        block = (
            f"{indent}# --- deep math (canonical) ---\n"
            f"{indent}if getattr(args, 'deep_math', False):\n"
            f"{indent}    try:\n"
            f"{indent}        deep_out = compute_deep_math(df, seed=int(args.seed))\n"
            f"{indent}    except Exception as e:\n"
            f"{indent}        deep_out = {{'error': f'deep_math_failed:{{type(e).__name__}}:{{e}}'}}\n"
            f"{indent}    (run_dir / 'deep_math.json').write_text(json.dumps(deep_out, indent=2), encoding='utf-8')\n"
            f"{indent}    math_results['_deep_math_file'] = 'deep_math.json'\n"
        )

        insert_at = txt.find("\n", wm.end())
        if insert_at != -1:
            txt = txt[:insert_at+1] + block + txt[insert_at+1:]

    write(rp, txt)
    print(f"[OK] runner canonicalized -> {rp} (sha={sha(rp)})")

def main() -> int:
    forecasting = ROOT / "fantasyai" / "aurora" / "math" / "forecasting.py"
    deep_math = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"
    runner = ROOT / "scripts" / "run_aurora_dataset_runner.py"

    for p in (forecasting, deep_math, runner):
        if not p.exists():
            print(f"[FAIL] missing: {p}")
            return 2

    backup(forecasting)
    backup(deep_math)
    backup(runner)

    ensure_forecast_with_bands(forecasting)
    repair_deep_math(deep_math)
    patch_runner(runner)

    import py_compile
    py_compile.compile(str(forecasting), doraise=True)
    py_compile.compile(str(deep_math), doraise=True)
    py_compile.compile(str(runner), doraise=True)

    print("[DONE] Worldclass math hotfix v2 applied + compiles cleanly.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
