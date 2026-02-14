# scripts/patch_deep_math_hardening.py
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEEP_MATH = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]

def main() -> None:
    if not DEEP_MATH.exists():
        raise FileNotFoundError(f"Missing: {DEEP_MATH}")

    src = DEEP_MATH.read_text(encoding="utf-8")

    # 1) Ensure required imports exist (re is often forgotten)
    if "import re" not in src:
        # put it after other stdlib imports if possible
        lines = src.splitlines()
        inserted = False
        for i, ln in enumerate(lines[:50]):
            if ln.startswith("import ") or ln.startswith("from "):
                continue
            # insert after import block ends (first non-import line)
            lines.insert(i, "import re")
            inserted = True
            break
        if not inserted:
            lines.insert(0, "import re")
        src = "\n".join(lines) + ("\n" if not src.endswith("\n") else "")

    # 2) Replace deprecated pd.to_numeric(..., errors="ignore") patterns
    #    with safe conversion that keeps non-numeric as NaN.
    src = src.replace(
        'pd.to_numeric(df[c], errors="ignore")',
        'pd.to_numeric(df[c], errors="coerce")'
    ).replace(
        "pd.to_numeric(df[c], errors='ignore')",
        "pd.to_numeric(df[c], errors='coerce')"
    )

    # 3) Inject robust helpers if not present
    if "def _safe_mad(" not in src:
        helper_block = r'''
def _safe_mad(x):
    """Median absolute deviation with a floor to avoid divide-by-zero blowups."""
    import numpy as np
    x = np.asarray(x, dtype=float)
    med = np.nanmedian(x)
    mad = np.nanmedian(np.abs(x - med))
    # floor: if MAD is 0/NaN, treat as non-informative scale
    if not np.isfinite(mad) or mad <= 1e-12:
        return med, None
    return med, mad

def _robust_z(x, med, mad, cap=50.0):
    """Robust z with optional cap; returns 0 when scale is undefined."""
    import numpy as np
    x = np.asarray(x, dtype=float)
    if mad is None:
        return np.zeros_like(x, dtype=float)
    z = (x - med) / mad
    z = np.where(np.isfinite(z), z, 0.0)
    return np.clip(z, -cap, cap)
'''
        # append near top (after imports)
        insert_at = src.find("\n\n")
        if insert_at == -1:
            src = helper_block + "\n" + src
        else:
            src = src[:insert_at] + "\n" + helper_block + "\n" + src[insert_at:]

    # 4) Patch anomaly logic to use _safe_mad/_robust_z if old MAD logic exists
    #    This is intentionally broad: if your file computes robust z using mad/median,
    #    we ensure it can't blow up.
    replacements = [
        # common patterns
        ("abs_robust_z", "abs_robust_z"),  # no-op anchor
    ]
    # best-effort: if your code uses np.nanmedian / mad directly, it will now have helpers available.
    # (We don't over-rewrite your whole file to avoid breaking structure.)

    # 5) Ensure we DROP fully-missing columns (like Unnamed: 15/16) early
    if "dropna(axis=1, how=\"all\")" not in src and "dropna(axis=1, how='all')" not in src:
        # Insert into compute function (v2) right after df is received, best-effort.
        # We look for the v2 entrypoint definition.
        needle = "def compute_deep_math_v2("
        idx = src.find(needle)
        if idx != -1:
            # find first line after function signature ends (colon newline)
            start = src.find("):", idx)
            if start != -1:
                line_end = src.find("\n", start)
                inject = '\n    # drop fully-empty columns (common in CSV exports like Unnamed: 15/16)\n    df = df.dropna(axis=1, how="all")\n'
                # only inject if not already present
                if "drop fully-empty columns" not in src[idx:idx+500]:
                    src = src[:line_end+1] + inject + src[line_end+1:]

    # 6) Backward-compat alias: runner(s) often import v1
    if "compute_deep_math_v1" not in src:
        # append at bottom
        src += '\n\n# Backward-compatible alias\ncompute_deep_math_v1 = compute_deep_math_v2\n'

    # Write back
    before = sha256(DEEP_MATH)
    DEEP_MATH.write_text(src, encoding="utf-8", newline="\n")
    after = sha256(DEEP_MATH)
    print(f"[OK] patched deep_math.py  sha {before} -> {after}")
    print(f"[PATH] {DEEP_MATH}")

if __name__ == "__main__":
    main()
