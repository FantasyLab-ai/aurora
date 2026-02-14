# scripts/patch_deep_math_worldclass.py
from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEEP_MATH_PATH = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"
RUNNER_PATH = ROOT / "scripts" / "run_aurora_dataset_runner.py"

DEEP_MATH_CODE = r'''from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# SciPy / statsmodels / sklearn are already in your environment based on logs
import scipy.stats as st
import statsmodels.api as sm
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.stats.stattools import jarque_bera
from statsmodels.tsa.stattools import adfuller
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.cluster import KMeans


def _sha(x: str) -> str:
    import hashlib
    return hashlib.sha256(x.encode("utf-8")).hexdigest()[:12]


def _drop_junk_cols(df: pd.DataFrame) -> pd.DataFrame:
    # Drop columns like "Unnamed: 15" that are artifacts
    junk = [c for c in df.columns if re.match(r"^Unnamed:\s*\d+$", str(c))]
    if junk:
        df = df.drop(columns=junk, errors="ignore")
    return df


def _numeric_hygiene(df: pd.DataFrame) -> pd.DataFrame:
    df = _drop_junk_cols(df.copy())
    # Replace inf with NaN
    df = df.replace([np.inf, -np.inf], np.nan)

    # Attempt numeric coercion for object columns (safe)
    for c in df.columns:
        if df[c].dtype == "object":
            v = pd.to_numeric(df[c], errors="ignore")
            # If coercion produced a numeric dtype (or many numerics), adopt it
            if pd.api.types.is_numeric_dtype(v):
                df[c] = v
    return df


def _select_numeric(df: pd.DataFrame, min_nonnull: float = 0.70, max_cols: int = 30) -> Tuple[pd.DataFrame, List[str]]:
    num = df.select_dtypes(include="number").copy()
    if num.shape[1] == 0:
        return num, []

    keep = []
    for c in num.columns:
        frac = float(num[c].notna().mean())
        if frac >= min_nonnull:
            # drop constant / near-constant
            vc = num[c].dropna()
            if vc.nunique() <= 1:
                continue
            keep.append(c)

    keep = keep[:max_cols]
    return num[keep].copy(), keep


def _bh_fdr(pvals: np.ndarray, alpha: float = 0.05) -> Tuple[np.ndarray, np.ndarray]:
    """Benjamini–Hochberg FDR: returns (qvals, reject_mask)."""
    p = np.asarray(pvals, dtype=float)
    n = p.size
    order = np.argsort(p)
    ranked = p[order]
    q = np.empty(n, dtype=float)
    prev = 1.0
    for i in range(n - 1, -1, -1):
        rank = i + 1
        val = ranked[i] * n / rank
        prev = min(prev, val)
        q[i] = prev
    qvals = np.empty(n, dtype=float)
    qvals[order] = q
    reject = qvals <= alpha
    return qvals, reject


def _top_correlations_with_tests(X: pd.DataFrame) -> List[Dict[str, Any]]:
    cols = list(X.columns)
    out = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            a, b = cols[i], cols[j]
            x = X[a].to_numpy()
            y = X[b].to_numpy()
            m = np.isfinite(x) & np.isfinite(y)
            n = int(m.sum())
            if n < 80:
                continue
            # handle constant input safely
            if np.nanstd(x[m]) == 0 or np.nanstd(y[m]) == 0:
                continue
            pr, pp = st.pearsonr(x[m], y[m])
            sr, sp = st.spearmanr(x[m], y[m])
            out.append({"a": a, "b": b, "n": n, "pearson_r": float(pr), "pearson_p": float(pp),
                        "spearman_r": float(sr), "spearman_p": float(sp)})
    if not out:
        return []
    # FDR on pearson p
    pvals = np.array([d["pearson_p"] for d in out], dtype=float)
    qvals, reject = _bh_fdr(pvals, alpha=0.05)
    for d, q, rj in zip(out, qvals, reject):
        d["pearson_q"] = float(q)
        d["pearson_reject_fdr05"] = bool(rj)
    out.sort(key=lambda d: abs(d["pearson_r"]), reverse=True)
    return out[:40]


def _pca_block(X: pd.DataFrame, seed: int = 0) -> Dict[str, Any]:
    # standardize
    Z = (X - X.mean()) / (X.std(ddof=0) + 1e-9)
    Z = Z.fillna(0.0)
    k = min(6, Z.shape[1])
    pca = PCA(n_components=k, random_state=seed)
    pca.fit(Z)
    comps = []
    for i in range(k):
        load = pca.components_[i]
        top = np.argsort(np.abs(load))[::-1][:6]
        comps.append({
            "component": i + 1,
            "explained_variance_ratio": float(pca.explained_variance_ratio_[i]),
            "top_loadings": [{"feature": Z.columns[t], "loading": float(load[t])} for t in top],
        })
    return {"note": "ok", "rows_used": int(Z.shape[0]), "components": comps}


def _vif_block(X: pd.DataFrame) -> Dict[str, Any]:
    # simple VIF approximation via R^2 of each feature ~ others
    Z = (X - X.mean()) / (X.std(ddof=0) + 1e-9)
    Z = Z.fillna(0.0)
    feats = list(Z.columns)
    rows = []
    for i, f in enumerate(feats):
        y = Z[f].to_numpy()
        Xo = Z.drop(columns=[f]).to_numpy()
        if Xo.shape[1] == 0:
            rows.append({"feature": f, "vif": 1.0, "r2_explained_by_others": 0.0})
            continue
        Xo = sm.add_constant(Xo, has_constant="add")
        try:
            m = sm.OLS(y, Xo).fit()
            r2 = float(m.rsquared) if np.isfinite(m.rsquared) else 0.0
        except Exception:
            r2 = 0.0
        vif = 1.0 / max(1e-6, (1.0 - r2))
        rows.append({"feature": f, "vif": float(vif), "r2_explained_by_others": float(r2)})
    rows.sort(key=lambda d: d["vif"], reverse=True)
    return {"note": "ok", "vif_top": rows[:20]}


def _assumptions_testing(df: pd.DataFrame, X: pd.DataFrame) -> Dict[str, Any]:
    out: Dict[str, Any] = {"note": "ok"}
    # Normality on a representative numeric column (highest variance)
    if X.shape[1] == 0:
        return {"note": "no_numeric"}
    vari = X.var(numeric_only=True).sort_values(ascending=False)
    col = vari.index[0]
    v = X[col].dropna().to_numpy()
    if v.size >= 20:
        jb_stat, jb_p, skew, kurt = jarque_bera(v)
        out["normality"] = {"column": col, "jarque_bera_p": float(jb_p), "skew": float(skew), "kurtosis": float(kurt)}
    # If a datetime-like index exists, we can attempt stationarity on the same column
    # (lightweight; won't throw if fails)
    try:
        adf = adfuller(pd.Series(v).astype(float).values, autolag="AIC")
        out["stationarity_adf"] = {"column": col, "adf_p": float(adf[1])}
    except Exception:
        out["stationarity_adf"] = {"column": col, "adf_p": None, "note": "adf_failed_or_not_applicable"}
    return out


def _regime_detection(X: pd.DataFrame, seed: int = 0) -> Dict[str, Any]:
    # Lightweight regime detection: cluster rows into regimes using KMeans on standardized numerics.
    if X.shape[0] < 200 or X.shape[1] < 3:
        return {"note": "insufficient_rows_or_cols"}
    Z = (X - X.mean()) / (X.std(ddof=0) + 1e-9)
    Z = Z.fillna(0.0)
    k = 3
    km = KMeans(n_clusters=k, random_state=seed, n_init=10)
    lab = km.fit_predict(Z.to_numpy())
    counts = {str(i): int((lab == i).sum()) for i in range(k)}
    # top driver features: centroid spread
    cent = km.cluster_centers_
    spread = np.std(cent, axis=0)
    top = np.argsort(spread)[::-1][:8]
    return {
        "note": "ok",
        "k": k,
        "counts": counts,
        "top_regime_drivers": [{"feature": Z.columns[i], "centroid_spread": float(spread[i])} for i in top],
    }


def _bootstrap_uncertainty(X: pd.DataFrame, seed: int = 0, B: int = 120) -> Dict[str, Any]:
    rng = np.random.default_rng(seed)
    if X.shape[1] < 2 or X.shape[0] < 200:
        return {"note": "insufficient_data"}
    cols = list(X.columns)
    # pick strongest observed pair
    best = None
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            a, b = cols[i], cols[j]
            x = X[a].to_numpy()
            y = X[b].to_numpy()
            m = np.isfinite(x) & np.isfinite(y)
            if m.sum() < 200:
                continue
            if np.nanstd(x[m]) == 0 or np.nanstd(y[m]) == 0:
                continue
            r, _ = st.pearsonr(x[m], y[m])
            if best is None or abs(r) > abs(best[2]):
                best = (a, b, float(r))
    if best is None:
        return {"note": "no_valid_pair"}
    a, b, r0 = best
    x = X[a].to_numpy()
    y = X[b].to_numpy()
    m = np.isfinite(x) & np.isfinite(y)
    x = x[m]; y = y[m]
    n = x.size
    rs = []
    for _ in range(B):
        idx = rng.integers(0, n, size=n)
        xb = x[idx]; yb = y[idx]
        if np.std(xb) == 0 or np.std(yb) == 0:
            continue
        r, _ = st.pearsonr(xb, yb)
        rs.append(float(r))
    if not rs:
        return {"note": "bootstrap_failed"}
    lo, hi = np.quantile(rs, [0.025, 0.975])
    stable_top10 = float(np.mean(np.abs(np.array(rs)) >= np.quantile(np.abs(rs), 0.90)))
    return {"note": "ok", "pair": {"a": a, "b": b, "r_observed": r0}, "bootstrap": {"B": int(len(rs)), "ci95": [float(lo), float(hi)], "stability_score": stable_top10}}


def _safe_causal_inference(df: pd.DataFrame, X: pd.DataFrame) -> Dict[str, Any]:
    """
    Very conservative: only runs if we can find a binary 'treatment' column
    AND a numeric outcome that isn't ID-like.
    """
    # binary treatment candidates
    t_candidates = []
    for c in X.columns:
        v = X[c].dropna()
        u = sorted(v.unique().tolist())
        if len(u) == 2 and set(u).issubset({0, 1}):
            # ensure not ultra-imbalanced
            p = float(v.mean())
            if 0.1 <= p <= 0.9:
                t_candidates.append(c)

    if not t_candidates:
        return {"note": "no_binary_treatment_found"}

    # outcome candidates: numeric, not near-unique, not ending with ID, not integer codes with huge cardinality
    y_candidates = []
    for c in X.columns:
        if c in t_candidates:
            continue
        v = X[c].dropna()
        if v.size < 200:
            continue
        nun = int(v.nunique())
        if nun <= 10:
            continue
        if nun >= int(0.95 * v.size):
            continue  # likely ID-like
        if re.search(r"id$", str(c), flags=re.IGNORECASE):
            continue
        y_candidates.append((c, float(v.var())))

    if not y_candidates:
        return {"note": "no_reasonable_outcome_found", "treatment_candidates": t_candidates[:6]}

    # pick highest variance outcome
    y_candidates.sort(key=lambda t: t[1], reverse=True)
    y_col = y_candidates[0][0]
    t_col = t_candidates[0]

    # confounders: remaining numerics
    conf = [c for c in X.columns if c not in (t_col, y_col)]
    if len(conf) < 2:
        return {"note": "insufficient_confounders", "treatment": t_col, "outcome": y_col}

    Z = X[[t_col, y_col] + conf].dropna()
    t = Z[t_col].astype(int).to_numpy()
    y = Z[y_col].astype(float).to_numpy()
    W = Z[conf].to_numpy()
    # standardize confounders
    W = (W - W.mean(axis=0)) / (W.std(axis=0) + 1e-9)

    # propensity model
    lr = LogisticRegression(max_iter=200)
    lr.fit(W, t)
    ps = np.clip(lr.predict_proba(W)[:, 1], 1e-3, 1 - 1e-3)

    # IPW ATE
    w = (t / ps) + ((1 - t) / (1 - ps))
    ate = float(np.average(y * (2 * t - 1), weights=w))

    return {
        "note": "ok_ipw",
        "treatment": t_col,
        "outcome": y_col,
        "n": int(Z.shape[0]),
        "ate_ipw": ate,
        "overlap": {"ps_min": float(ps.min()), "ps_max": float(ps.max())},
        "confounders_n": int(len(conf)),
    }


def compute_deep_math_v2(df: pd.DataFrame, seed: int = 0) -> Dict[str, Any]:
    out: Dict[str, Any] = {"version": "deep_math_v2", "seed": int(seed)}
    df2 = _numeric_hygiene(df)
    X, used = _select_numeric(df2, min_nonnull=0.70, max_cols=30)

    out["rows"] = int(df2.shape[0])
    out["numeric_cols_n"] = int(len(used))
    out["numeric_cols_used"] = used

    if X.shape[1] < 2 or X.shape[0] < 200:
        out["note"] = "insufficient_numeric_data"
        return out

    out["hypothesis_testing"] = {
        "note": "ok",
        "fdr": {"alpha": 0.05, "method": "BH"},
        "top_correlations": _top_correlations_with_tests(X),
    }

    out["dimensionality_reduction"] = _pca_block(X, seed=seed)
    out["multivariate_modeling"] = {"note": "ok", "vif": _vif_block(X)}

    out["formal_assumptions_testing"] = _assumptions_testing(df2, X)
    out["regime_detection"] = _regime_detection(X, seed=seed)
    out["model_uncertainty"] = _bootstrap_uncertainty(X, seed=seed, B=120)

    out["causal_inference"] = _safe_causal_inference(df2, X)

    return out
'''

def write_text(path: Path, txt: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(txt, encoding="utf-8")
    h = hashlib.sha256(txt.encode("utf-8")).hexdigest()[:12]
    return h

def patch_runner_once(path: Path) -> None:
    txt = path.read_text(encoding="utf-8")

    # 1) Ensure single argparse flag --deep-math
    # Remove duplicates if any
    txt = re.sub(r'^\s*ap\.add_argument\("--deep-math".*?\)\s*\n', '', txt, flags=re.M)

    # Insert the flag right after --math-v2 if present, otherwise after argparse init.
    if '--math-v2' in txt:
        txt = re.sub(
            r'(ap\.add_argument\("--math-v2".*?\)\s*\n)',
            r'\1    ap.add_argument("--deep-math", action="store_true", help="run Deep Math (tests, PCA/VIF, regimes, uncertainty, causal)")\n',
            txt,
            count=1
        )
    else:
        txt = re.sub(
            r'(ap\s*=\s*argparse\.ArgumentParser\(.*?\)\s*\n)',
            r'\1    ap.add_argument("--deep-math", action="store_true", help="run Deep Math (tests, PCA/VIF, regimes, uncertainty, causal)")\n',
            txt,
            count=1
        )

    # 2) Ensure import exists (idempotent)
    if "compute_deep_math_v2" not in txt:
        txt = re.sub(
            r'from fantasyai\.aurora\.mathstack_v2 import compute_math_stack_v2\s*\n',
            'from fantasyai.aurora.mathstack_v2 import compute_math_stack_v2\nfrom fantasyai.aurora.math.deep_math import compute_deep_math_v2\n',
            txt,
            count=1
        )

    # 3) Ensure deep_math call exists after math stack computation
    if "deep_math.json" not in txt:
        # Try to insert after math_results["math"] assignment (works across your variants)
        txt = re.sub(
            r'(math_results\["math"\]\s*=\s*compute_math_stack_v2\(df\)\s*\n)',
            r'\1        if getattr(args, "deep_math", False):\n'
            r'            deep_out = compute_deep_math_v2(df, seed=int(args.seed))\n'
            r'            (run_dir / "deep_math.json").write_text(json.dumps(deep_out, indent=2), encoding="utf-8")\n'
            r'            math_results["_deep_math_file"] = "deep_math.json"\n',
            txt,
            count=1
        )

    # 4) Fix the classic indentation bug: args=parse_args() must be inside main()
    txt = re.sub(r'^\s*args\s*=\s*ap\.parse_args\(\)\s*$', '    args = ap.parse_args()', txt, flags=re.M)

    path.write_text(txt, encoding="utf-8")


def main() -> None:
    sha = write_text(DEEP_MATH_PATH, DEEP_MATH_CODE)
    patch_runner_once(RUNNER_PATH)

    print(f"[OK] wrote deep_math -> {DEEP_MATH_PATH} (sha={sha})")
    print(f"[OK] patched runner -> {RUNNER_PATH}")

if __name__ == "__main__":
    main()
