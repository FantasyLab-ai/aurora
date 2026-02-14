from __future__ import annotations

import re
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]

RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"
MS2    = ROOT / "fantasyai" / "aurora" / "mathstack_v2.py"
DEEPM  = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def backup(p: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_name(p.name + f".bak.{stamp}")
    b.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    return b

DEEP_MATH_SRC = r'''from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

import statsmodels.api as sm
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.stats.outliers_influence import variance_inflation_factor
from statsmodels.stats.stattools import jarque_bera


def _numeric_df(df: pd.DataFrame) -> pd.DataFrame:
    num = df.select_dtypes(include=["number"]).copy()
    # also coerce object-like numerics
    for c in df.columns:
        if c in num.columns:
            continue
        if pd.api.types.is_object_dtype(df[c]) or pd.api.types.is_string_dtype(df[c]):
            s = pd.to_numeric(df[c], errors="coerce")
            if s.notna().mean() > 0.80:
                num[c] = s
    return num


def _sample_rows(df: pd.DataFrame, n: int = 200_000, seed: int = 0) -> pd.DataFrame:
    if len(df) <= n:
        return df
    return df.sample(n=n, random_state=seed)


def compute_deep_math_v1(df: pd.DataFrame, seed: int = 0, max_cols: int = 25) -> Dict[str, Any]:
    """
    Deep math layer:
    - hypothesis tests (top correlations with p-values)
    - PCA
    - multivariate OLS + assumptions tests (JB, BP, VIF)
    - simple regime/phase shift scoring (rolling mean change)
    """
    out: Dict[str, Any] = {"enabled": True}

    d0 = _sample_rows(df, n=200_000, seed=seed)
    num = _numeric_df(d0)
    out["numeric_cols_detected"] = int(num.shape[1])

    if num.shape[1] == 0:
        out["note"] = "No usable numeric columns detected for deep math."
        return out

    # choose cols with least missingness
    miss = num.isna().mean().sort_values()
    cols = list(miss.index[:max_cols])
    X = num[cols].copy()

    # 1) Hypothesis tests: correlation significance
    corr_tests: List[Dict[str, Any]] = []
    if len(cols) >= 2:
        corr = X.corr(numeric_only=True)
        pairs: List[Tuple[str, str, float]] = []
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                v = corr.iloc[i, j]
                if pd.notna(v):
                    pairs.append((cols[i], cols[j], float(v)))
        pairs.sort(key=lambda t: abs(t[2]), reverse=True)

        for a, b, _ in pairs[:10]:
            xa = pd.to_numeric(X[a], errors="coerce").to_numpy()
            xb = pd.to_numeric(X[b], errors="coerce").to_numpy()
            m = np.isfinite(xa) & np.isfinite(xb)
            if m.sum() < 80:
                continue
            r, p = stats.pearsonr(xa[m], xb[m])
            corr_tests.append({"a": a, "b": b, "pearson_r": float(r), "p_value": float(p)})
    out["hypothesis_testing"] = {"top_corr_significance": corr_tests}

    # 2) Dimensionality reduction: PCA
    pca_block: Dict[str, Any] = {}
    Xp = X.fillna(X.median(numeric_only=True))
    Z = StandardScaler().fit_transform(Xp.to_numpy(dtype=float))
    k = min(6, Z.shape[1])
    if k >= 2 and Z.shape[0] >= 100:
        pca = PCA(n_components=k, random_state=seed)
        pca.fit(Z)
        pca_block["n_components"] = int(k)
        pca_block["explained_variance_ratio"] = [float(x) for x in pca.explained_variance_ratio_]
    out["dimensionality_reduction"] = {"pca": pca_block}

    # 3) Multivariate modeling + assumptions
    modeling: Dict[str, Any] = {}
    target = cols[0]
    feats = [c for c in cols if c != target][:10]
    if len(feats) >= 2:
        dmod = X[[target] + feats].dropna()
        if len(dmod) >= 250:
            y = dmod[target].to_numpy(dtype=float)
            Xmat = sm.add_constant(dmod[feats].to_numpy(dtype=float), has_constant="add")
            model = sm.OLS(y, Xmat).fit()

            modeling["ols_target"] = target
            modeling["ols_features"] = feats
            modeling["n"] = int(len(dmod))
            modeling["r2"] = float(model.rsquared) if np.isfinite(model.rsquared) else float("nan")
            modeling["adj_r2"] = float(model.rsquared_adj) if np.isfinite(model.rsquared_adj) else float("nan")

            resid = model.resid
            jb_stat, jb_p, skew, kurt = jarque_bera(resid)
            bp = het_breuschpagan(resid, model.model.exog)

            vifs = []
            Xv = dmod[feats].to_numpy(dtype=float)
            for i, name in enumerate(feats):
                try:
                    v = variance_inflation_factor(Xv, i)
                    vifs.append({"feature": name, "vif": float(v)})
                except Exception:
                    pass
            vifs.sort(key=lambda d: d["vif"], reverse=True)

            modeling["assumptions"] = {
                "normality_jarque_bera_p": float(jb_p),
                "heteroskedasticity_bp_p": float(bp[1]),
                "top_vif": vifs[:10],
            }

    out["multivariate_modeling"] = modeling

    # 4) Regime / phase detection: rolling mean shift score
    regime: Dict[str, Any] = {}
    s = pd.to_numeric(X[target], errors="coerce").dropna()
    if len(s) >= 500:
        w = min(2000, max(200, len(s) // 50))
        r1 = s.rolling(w).mean()
        r2 = s.rolling(w).mean().shift(w)
        shift = (r1 - r2).abs()
        top = shift.sort_values(ascending=False).head(10)
        regime = {"series": target, "window": int(w),
                  "top_mean_shifts": [{"row_index": int(i), "shift_abs": float(v)}
                                     for i, v in top.items() if np.isfinite(v)]}
    out["regime_detection"] = regime

    return out
'''

def patch_mathstack_v2():
    if not MS2.exists():
        return
    txt = MS2.read_text(encoding="utf-8")
    # Remove deprecated infer_datetime_format=True argument safely
    txt2 = re.sub(
        r"pd\.to_datetime\(([^)]*?),\s*errors=\"coerce\",\s*infer_datetime_format=True\)",
        r'pd.to_datetime(\1, errors="coerce")',
        txt
    )
    if txt2 != txt:
        backup(MS2)
        MS2.write_text(txt2, encoding="utf-8")

def patch_runner():
    if not RUNNER.exists():
        raise FileNotFoundError(RUNNER)

    txt = RUNNER.read_text(encoding="utf-8")

    # Remove accidental top-level line: "out" (exact line)
    txt2 = re.sub(r"(?m)^\s*out\s*$\n?", "", txt)

    changed = (txt2 != txt)
    txt = txt2

    # Ensure import exists
    if "compute_deep_math_v1" not in txt:
        txt2 = re.sub(
            r"from fantasyai\.aurora\.mathstack_v2 import compute_math_stack_v2\s*",
            "from fantasyai.aurora.mathstack_v2 import compute_math_stack_v2\n"
            "from fantasyai.aurora.math.deep_math import compute_deep_math_v1\n",
            txt
        )
        if txt2 == txt:
            # fallback: insert after last aurora import
            txt2 = txt.replace(
                "from fantasyai.aurora.mathstack_v2 import compute_math_stack_v2\n",
                "from fantasyai.aurora.mathstack_v2 import compute_math_stack_v2\n"
                "from fantasyai.aurora.math.deep_math import compute_deep_math_v1\n"
            )
        changed = changed or (txt2 != txt)
        txt = txt2

    # Ensure argparse has --deep-math
    if "--deep-math" not in txt:
        txt2 = re.sub(
            r'(parser\.add_argument\("--math-v2".*?\)\s*)',
            r'\1parser.add_argument("--deep-math", action="store_true", help="run deep math (hypothesis tests, PCA, OLS, regime detection)")\n',
            txt,
            count=1,
            flags=re.DOTALL
        )
        changed = changed or (txt2 != txt)
        txt = txt2

    # Fix _math_stack returning None (replace lone `return` at end with `return out`)
    # Only do if a `return out` not present in that function.
    if re.search(r"def _math_stack\(df: pd\.DataFrame\):", txt) and "return out" not in txt:
        txt2 = re.sub(
            r"(def _math_stack\(df: pd\.DataFrame\):[\s\S]*?out\[\s*\"target_guess\"\s*\]\s*=\s*_infer_target\(df\)\s*)\n\s*return\s*\n",
            r"\1\n    return out\n",
            txt,
            count=1
        )
        changed = changed or (txt2 != txt)
        txt = txt2

    # Insert deep math call after the mathstack_v2 call
    if "args.deep_math" not in txt:
        txt2 = re.sub(
            r'(math_results\["math"\]\s*=\s*compute_math_stack_v2\(df\)\s*\n)',
            r'\1        if getattr(args, "deep_math", False):\n            math_results["deep_math"] = compute_deep_math_v1(df, seed=int(args.seed))\n',
            txt,
            count=1
        )
        changed = changed or (txt2 != txt)
        txt = txt2

    if changed:
        backup(RUNNER)
        RUNNER.write_text(txt, encoding="utf-8")

def write_deep_math():
    DEEPM.parent.mkdir(parents=True, exist_ok=True)
    if DEEPM.exists():
        backup(DEEPM)
    DEEPM.write_text(DEEP_MATH_SRC, encoding="utf-8")

def main():
    write_deep_math()
    patch_mathstack_v2()
    patch_runner()
    print("[OK] deep math files + runner patched.")

if __name__ == "__main__":
    main()
