from __future__ import annotations

import re
import json
import hashlib
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]  # .../FantasyAI
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
    \"""
    Simple forecast with uncertainty bands.

    Returns:
      {
        "target_col": ...,
        "horizon": ...,
        "method": ...,
        "yhat": [...],
        "lower": [...],
        "upper": [...],
        "note": ...
      }
    \"""
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
    if not np.isfinite(mad) or mad == 0.0:
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
    write(fp, txt.rstrip() + "\n" + shim.lstrip())
    print(f"[OK] added forecast_with_bands -> {fp} (sha={sha(fp)})")

def ensure_deep_math_entry(dp: Path) -> None:
    txt = dp.read_text(encoding="utf-8")

    # Ensure `import re`
    if not re.search(r"(?m)^\s*import\s+re\s*$", txt):
        # insert after the first import line (or at top)
        m = re.search(r"(?m)^(import\s+.+|from\s+.+\s+import\s+.+)\s*$", txt)
        if m:
            ins = m.end()
            txt = txt[:ins] + "\nimport re\n" + txt[ins:]
        else:
            txt = "import re\n" + txt

    # Fix any token corruption like "target...l"
    txt = txt.replace("target...l", "target_col")

    # Remove any prior canonical block to avoid duplicates
    txt = re.sub(
        r"(?s)\n# -+\n# Canonical deep-math entrypoint.*?^compute_deep_math\s*=\s*compute_deep_math_v3\s*$",
        "\n",
        txt,
        flags=re.M,
    )

    canonical = r"""

# ------------------------------
# Canonical deep-math entrypoint
# ------------------------------
def compute_deep_math_v3(df, seed: int = 0, max_corrs: int = 200):
    \"\"\"Stable deep-math entrypoint used by the dataset runner.\"\"\"

    # Prefer v2/v1 if they exist
    if "compute_deep_math_v2" in globals() and callable(globals().get("compute_deep_math_v2")):
        return globals()["compute_deep_math_v2"](df, seed=seed, max_corrs=max_corrs)
    if "compute_deep_math_v1" in globals() and callable(globals().get("compute_deep_math_v1")):
        return globals()["compute_deep_math_v1"](df, seed=seed)

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
    txt = txt.rstrip() + "\n" + canonical.lstrip()
    write(dp, txt)
    print(f"[OK] ensured compute_deep_math_v3 + alias -> {dp} (sha={sha(dp)})")

def patch_runner(rp: Path) -> None:
    lines = rp.read_text(encoding="utf-8").splitlines()

    # 1) Drop all deep_math imports (we'll insert canonical one)
    kept = []
    for ln in lines:
        if "fantasyai.aurora.math.deep_math" in ln:
            continue
        if ln.strip().startswith("import ") and "deep_math" in ln:
            continue
        kept.append(ln)
    lines = kept

    txt = "\n".join(lines)

    # 2) Remove broken deep-math blocks (anything containing deep_math.json / Optional deep-math / _deep_math_file)
    txt = re.sub(r"(?s)\n\s*# Optional deep-math extras.*?(?=\n\s*\n|\Z)", "\n", txt)
    txt = re.sub(r"(?s)\n\s*# --- deep math.*?(?=\n\s*\n|\Z)", "\n", txt)
    txt = re.sub(r"(?s)\n.*deep_math\.json.*?(?=\n\s*\n|\Z)", "\n", txt)
    txt = re.sub(r"(?s)\n\s*math_results\[[\"']_deep_math_file[\"']\].*?(?=\n\s*\n|\Z)", "\n", txt)

    lines = txt.splitlines()

    # 3) Safe-add deep-math argparse flag once
    safe_flag = [
        "    # deep math (safe-add; avoids argparse duplicate conflicts)",
        "    try:",
        "        ap.add_argument(\"--deep-math\", action=\"store_true\", help=\"run deep math extras (writes deep_math.json)\")",
        "    except Exception:",
        "        pass",
    ]

    out = []
    saw_flag_line = False
    for ln in lines:
        if ("--deep-math" in ln) and ("add_argument" in ln):
            if not saw_flag_line:
                out.extend(safe_flag)
                saw_flag_line = True
            continue
        out.append(ln)
    lines = out

    if not saw_flag_line:
        # insert after --math-v2 flag if present
        out = []
        inserted = False
        for ln in lines:
            out.append(ln)
            if (not inserted) and ("--math-v2" in ln) and ("add_argument" in ln):
                out.extend(safe_flag)
                inserted = True
        lines = out

    txt = "\n".join(lines)

    # 4) Insert canonical import after top imports
    if "compute_deep_math_v3 as compute_deep_math" not in txt:
        lns = txt.splitlines()
        last_imp = 0
        for i, ln in enumerate(lns[:100], 1):
            if ln.startswith("import ") or ln.startswith("from "):
                last_imp = i
        import_block = [
            "",
            "# deep math (canonical import)",
            "from fantasyai.aurora.math.deep_math import compute_deep_math_v3 as compute_deep_math",
            "",
        ]
        lns = lns[:last_imp] + import_block + lns[last_imp:]
        txt = "\n".join(lns)

    # 5) Insert canonical deep-math execution after math_results.json write_text
    if ("deep_math.json" not in txt) or ("compute_deep_math(" not in txt):
        m = re.search(r"(?m)^(?P<indent>\s*)\(run_dir\s*/\s*\"math_results\.json\"\)\.write_text\(", txt)
        if m:
            indent = m.group("indent")
            block = (
                "\n"
                f"{indent}# --- deep math (canonical) ---\n"
                f"{indent}if getattr(args, 'deep_math', False):\n"
                f"{indent}    try:\n"
                f"{indent}        deep_out = compute_deep_math(df, seed=int(args.seed))\n"
                f"{indent}    except Exception as e:\n"
                f"{indent}        deep_out = {{'error': f'deep_math_failed:{type(e).__name__}:{e}'}}\n"
                f"{indent}    (run_dir / \"deep_math.json\").write_text(json.dumps(deep_out, indent=2), encoding=\"utf-8\")\n"
                f"{indent}    math_results[\"_deep_math_file\"] = \"deep_math.json\"\n"
            )

            # insert right after the line containing the write_text call (end-of-line)
            insert_at = txt.find("\n", m.end())
            if insert_at != -1:
                txt = txt[: insert_at + 1] + block + txt[insert_at + 1 :]

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
    ensure_deep_math_entry(deep_math)
    patch_runner(runner)

    import py_compile
    py_compile.compile(str(forecasting), doraise=True)
    py_compile.compile(str(deep_math), doraise=True)
    py_compile.compile(str(runner), doraise=True)

    print("[DONE] Worldclass math hotfix applied + compiles cleanly.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
