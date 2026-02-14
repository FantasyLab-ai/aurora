from __future__ import annotations

import re
import shutil
from pathlib import Path
from datetime import datetime


ROOT = Path(__file__).resolve().parents[1]
DEEP = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"
RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"


def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)
    print(f"[OK] backup -> {b}")
    return b


def ensure_fallbacks(deep_txt: str) -> str:
    """
    Inject PCA/StandardScaler fallbacks if sklearn is unavailable,
    and add numeric hygiene helper used by deep math.
    This prevents 'NoneType is not callable' forever.
    """

    # 1) Insert fallback classes AFTER the sklearn import try/except blocks.
    # We look for the typical "PCA = None" / "StandardScaler = None" sentinel lines.
    insert_after_patterns = [
        r"(?m)^\s*PCA\s*=\s*None\s*$",
        r"(?m)^\s*StandardScaler\s*=\s*None\s*$",
    ]

    fallback_block = r"""
# ------------------------------
# Fallbacks when sklearn is absent
# ------------------------------
# If sklearn didn't import, the code sets PCA/StandardScaler=None.
# Later calling PCA(...) would crash with: TypeError: 'NoneType' object is not callable.
# These lightweight fallbacks keep Deep Math working without extra deps.

if "StandardScaler" in globals() and StandardScaler is None:
    class StandardScaler:  # type: ignore
        def __init__(self, with_mean: bool = True, with_std: bool = True):
            self.with_mean = with_mean
            self.with_std = with_std
            self.mean_ = None
            self.scale_ = None

        def fit_transform(self, X):
            import numpy as np
            X = np.asarray(X, dtype=float)
            mu = np.nanmean(X, axis=0) if self.with_mean else 0.0
            sd = np.nanstd(X, axis=0) if self.with_std else 1.0
            sd = np.where(sd == 0, 1.0, sd)
            self.mean_ = mu
            self.scale_ = sd
            return (X - mu) / sd

if "PCA" in globals() and PCA is None:
    class PCA:  # type: ignore
        def __init__(self, n_components=2, random_state=None):
            self.n_components = int(n_components)
            self.random_state = random_state
            self.components_ = None
            self.explained_variance_ratio_ = None

        def fit_transform(self, X):
            import numpy as np
            X = np.asarray(X, dtype=float)
            # mean-center (ignore nans by imputing with col mean)
            col_mu = np.nanmean(X, axis=0)
            X2 = X.copy()
            nan_mask = np.isnan(X2)
            if nan_mask.any():
                X2[nan_mask] = np.take(col_mu, np.where(nan_mask)[1])
            X2 = X2 - col_mu

            # SVD PCA
            U, S, Vt = np.linalg.svd(X2, full_matrices=False)
            comps = Vt[: self.n_components]
            self.components_ = comps

            # explained variance ratio
            var = (S ** 2) / (max(len(X2) - 1, 1))
            total = var.sum() if var.size else 0.0
            evr = (var[: self.n_components] / total) if total > 0 else None
            self.explained_variance_ratio_ = evr
            return X2 @ comps.T
"""

    # Only inject once
    if "Fallbacks when sklearn is absent" in deep_txt:
        return deep_txt

    # Find last occurrence among sentinel lines and inject after it
    idx = -1
    for pat in insert_after_patterns:
        m = list(re.finditer(pat, deep_txt))
        if m:
            idx = max(idx, m[-1].end())

    if idx == -1:
        # If we can't find the sentinels, append at end (still safe).
        return deep_txt + "\n" + fallback_block + "\n"

    return deep_txt[:idx] + "\n" + fallback_block + "\n" + deep_txt[idx:]


def ensure_numeric_hygiene(deep_txt: str) -> str:
    """
    Ensure deep math filters numeric columns sanely:
    - drop 'Unnamed:*'
    - drop all-NaN
    - drop constant/near-constant
    This reduces ConstantInputWarning and stabilizes correlations/OLS/PCA.
    """

    hygiene_fn = r"""
def _select_numeric_for_deep_math(df, max_cols: int = 40, min_non_na_frac: float = 0.60):
    import numpy as np
    import pandas as pd

    num = df.select_dtypes(include="number").copy()
    if num.shape[1] == 0:
        return num, []

    # Drop "Unnamed:" garbage columns (common CSV artifacts)
    drop_unnamed = [c for c in num.columns if str(c).strip().lower().startswith("unnamed:")]
    if drop_unnamed:
        num = num.drop(columns=drop_unnamed, errors="ignore")

    # Coerce everything numeric (safe)
    for c in list(num.columns):
        num[c] = pd.to_numeric(num[c], errors="coerce")

    # Drop columns with too many missing
    non_na = num.notna().mean()
    keep = non_na[non_na >= float(min_non_na_frac)].index.tolist()
    num = num[keep]

    # Drop all-NaN / constant
    keep2 = []
    for c in num.columns:
        s = num[c]
        s2 = s.dropna()
        if len(s2) < 10:
            continue
        # constant / near-constant
        if s2.nunique(dropna=True) <= 1:
            continue
        keep2.append(c)

    num = num[keep2]

    # Limit cols to avoid O(n^2) blowups
    cols = list(num.columns[:max_cols])
    return num[cols], cols
"""

    if "_select_numeric_for_deep_math" in deep_txt:
        return deep_txt

    # Insert near the top after imports; fall back to prepend if needed
    m = re.search(r"(?m)^\s*#\s*-{5,}.*\s*$", deep_txt)
    if m:
        insert_at = m.start()
        return deep_txt[:insert_at] + hygiene_fn + "\n\n" + deep_txt[insert_at:]

    # otherwise insert after initial imports block
    m2 = re.search(r"(?s)\n\n", deep_txt)
    if m2:
        insert_at = m2.end()
        return deep_txt[:insert_at] + hygiene_fn + "\n\n" + deep_txt[insert_at:]

    return hygiene_fn + "\n\n" + deep_txt


def ensure_entrypoints(deep_txt: str) -> str:
    """
    Guarantee:
      - compute_deep_math_v2 exists (assumed)
      - compute_deep_math_v1 is an alias to v2 (so runner never breaks again)
    """

    if "def compute_deep_math_v2" not in deep_txt:
        raise RuntimeError("deep_math.py does not contain compute_deep_math_v2; can't safely patch.")

    if "compute_deep_math_v1" in deep_txt:
        return deep_txt

    alias = "\n\n# Backwards-compatible alias (older runner imports)\ncompute_deep_math_v1 = compute_deep_math_v2\n"
    return deep_txt + alias


def patch_runner(runner_txt: str) -> str:
    """
    Make runner import/call v2 (and avoid conflicting/duplicate deep-math flags).
    """

    # 1) Ensure only one --deep-math add_argument exists
    # If there are duplicates, keep the first and remove the rest.
    lines = runner_txt.splitlines(True)
    deep_arg_idxs = [i for i, ln in enumerate(lines) if "--deep-math" in ln and "add_argument" in ln]
    if len(deep_arg_idxs) > 1:
        # remove all but first
        for i in reversed(deep_arg_idxs[1:]):
            lines.pop(i)
        runner_txt = "".join(lines)

    # 2) Import v2 (and don't import v1 explicitly)
    runner_txt = re.sub(
        r"(?m)^\s*from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+compute_deep_math_v1\s*$\n?",
        "",
        runner_txt,
    )

    if "compute_deep_math_v2" not in runner_txt:
        # Replace any deep_math import with v2, or insert under mathstack import
        if "from fantasyai.aurora.math.deep_math import" in runner_txt:
            runner_txt = re.sub(
                r"from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+[^\n]+",
                "from fantasyai.aurora.math.deep_math import compute_deep_math_v2",
                runner_txt,
            )
        else:
            runner_txt = re.sub(
                r"(from\s+fantasyai\.aurora\.mathstack_v2\s+import\s+compute_math_stack_v2\s*\n)",
                r"\1from fantasyai.aurora.math.deep_math import compute_deep_math_v2\n",
                runner_txt,
                count=1,
            )

    # 3) Ensure the call uses v2
    runner_txt = runner_txt.replace("compute_deep_math_v1(", "compute_deep_math_v2(")

    return runner_txt


def main():
    if not DEEP.exists():
        raise SystemExit(f"Missing deep_math.py at: {DEEP}")
    if not RUNNER.exists():
        raise SystemExit(f"Missing runner at: {RUNNER}")

    # Patch deep_math.py
    backup(DEEP)
    deep_txt = DEEP.read_text(encoding="utf-8")
    deep_txt = ensure_numeric_hygiene(deep_txt)
    deep_txt = ensure_fallbacks(deep_txt)
    deep_txt = ensure_entrypoints(deep_txt)
    DEEP.write_text(deep_txt, encoding="utf-8")
    print(f"[OK] patched deep_math -> {DEEP}")

    # Patch runner
    backup(RUNNER)
    runner_txt = RUNNER.read_text(encoding="utf-8")
    runner_txt2 = patch_runner(runner_txt)
    RUNNER.write_text(runner_txt2, encoding="utf-8")
    print(f"[OK] patched runner -> {RUNNER}")

    print("[DONE] Deep Math is now dependency-safe + runner wired to v2 + v1 alias added.")


if __name__ == "__main__":
    main()
