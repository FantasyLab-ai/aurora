from __future__ import annotations

import time
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # .../FantasyAI
DEEP_MATH_PATH = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

DEEP_MATH_V2 = r'''
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Hard deps you already use elsewhere in the stack
# Optional deps below must NEVER crash the pipeline
try:
    import scipy.stats as st
except Exception:  # pragma: no cover
    st = None

try:
    import statsmodels.api as sm
    import statsmodels.stats.api as sms
except Exception:  # pragma: no cover
    sm = None
    sms = None

try:
    from sklearn.decomposition import PCA
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import mean_squared_error, r2_score
    from sklearn.preprocessing import StandardScaler
except Exception:  # pragma: no cover
    PCA = None
    train_test_split = None
    mean_squared_error = None
    r2_score = None
    StandardScaler = None

try:
    import ruptures as rpt
except Exception:  # pragma: no cover
    rpt = None


def _safe_to_numeric(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _fdr_bh(pvals: List[float], alpha: float = 0.05) -> Dict[str, Any]:
    """Benjamini–Hochberg FDR correction. Returns q-values + rejected mask."""
    pv = np.asarray([p if (p is not None and np.isfinite(p)) else 1.0 for p in pvals], dtype=float)
    m = len(pv)
    order = np.argsort(pv)
    ranked = pv[order]
    q = np.empty_like(ranked)
    prev = 1.0
    for i in range(m - 1, -1, -1):
        rank = i + 1
        val = (ranked[i] * m) / rank
        prev = min(prev, val)
        q[i] = prev
    qvals = np.empty_like(q)
    qvals[order] = q
    rejected = qvals <= alpha
    return {"q_values": qvals.tolist(), "rejected": rejected.tolist(), "alpha": alpha}


def _infer_target(df_num: pd.DataFrame) -> Optional[str]:
    """
    Pick a plausible target column:
      - numeric
      - not constant
      - not mostly missing
      - higher variance than peers (often a 'measurement' vs a code)
    """
    if df_num.shape[1] < 2:
        return None
    stats = []
    for c in df_num.columns:
        s = df_num[c]
        miss = float(s.isna().mean())
        nunq = int(s.nunique(dropna=True))
        if miss > 0.35 or nunq < 10:
            continue
        v = float(np.nanvar(s.values))
        if not np.isfinite(v) or v <= 0:
            continue
        stats.append((c, v))
    if not stats:
        return None
    stats.sort(key=lambda t: t[1], reverse=True)
    return stats[0][0]


def _top_pairwise_tests(df_num: pd.DataFrame, max_pairs: int = 120) -> Dict[str, Any]:
    out: Dict[str, Any] = {"note": "scipy not installed" if st is None else "ok"}
    if st is None or df_num.shape[1] < 2:
        out["pairs"] = []
        return out

    cols = list(df_num.columns)
    pairs = []
    pvals = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            a, b = cols[i], cols[j]
            x = df_num[a].values
            y = df_num[b].values
            m = np.isfinite(x) & np.isfinite(y)
            n = int(m.sum())
            if n < 50:
                continue
            r, p = st.pearsonr(x[m], y[m])
            rs, ps = st.spearmanr(x[m], y[m])
            pairs.append({"a": a, "b": b, "n": n, "pearson_r": float(r), "pearson_p": float(p), "spearman_r": float(rs), "spearman_p": float(ps)})
            pvals.append(float(p))
            if len(pairs) >= max_pairs:
                break
        if len(pairs) >= max_pairs:
            break

    # rank by absolute Pearson r
    pairs.sort(key=lambda d: abs(d["pearson_r"]), reverse=True)

    # FDR q-values on Pearson p
    if pvals:
        fdr = _fdr_bh([d["pearson_p"] for d in pairs], alpha=0.05)
        for k, d in enumerate(pairs):
            d["pearson_q"] = float(fdr["q_values"][k])
            d["pearson_reject_fdr05"] = bool(fdr["rejected"][k])
        out["fdr"] = {"alpha": fdr["alpha"], "method": "BH"}
    else:
        out["fdr"] = {"alpha": 0.05, "method": "BH", "note": "no p-values computed"}

    out["top_correlations"] = pairs[:50]
    return out


def _pca_block(df_num: pd.DataFrame, n_components: int = 5) -> Dict[str, Any]:
    out: Dict[str, Any] = {"note": "sklearn not installed" if PCA is None else "ok"}
    if PCA is None or StandardScaler is None or df_num.shape[1] < 3:
        return out

    X = df_num.copy()
    X = X.replace([np.inf, -np.inf], np.nan).dropna(axis=0)
    if X.shape[0] < 200:
        out["note"] = "not enough complete rows for PCA"
        return out

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X.values)

    pca = PCA(n_components=min(n_components, X.shape[1]))
    Z = pca.fit_transform(Xs)

    evr = pca.explained_variance_ratio_.tolist()
    comps = []
    cols = list(X.columns)
    for k in range(pca.components_.shape[0]):
        w = pca.components_[k]
        idx = np.argsort(np.abs(w))[::-1][:8]
        comps.append({
            "component": int(k + 1),
            "explained_variance_ratio": float(evr[k]),
            "top_loadings": [{"col": cols[i], "loading": float(w[i])} for i in idx],
        })

    out["rows_used"] = int(X.shape[0])
    out["components"] = comps
    out["explained_variance_ratio"] = [float(v) for v in evr]
    return out


def _vif(df_X: pd.DataFrame) -> Dict[str, Any]:
    if sm is None:
        return {"note": "statsmodels not installed"}
    X = df_X.replace([np.inf, -np.inf], np.nan).dropna(axis=0)
    if X.shape[0] < 300 or X.shape[1] < 2:
        return {"note": "not enough data"}
    Xc = sm.add_constant(X, has_constant="add")
    out = []
    cols = list(X.columns)
    for c in cols:
        y = X[c]
        others = X.drop(columns=[c])
        if others.shape[1] < 1:
            continue
        m = np.isfinite(y.values) & np.isfinite(others.values).all(axis=1)
        if m.sum() < 300:
            continue
        model = sm.OLS(y.values[m], sm.add_constant(others.values[m], has_constant="add")).fit()
        r2 = float(model.rsquared) if np.isfinite(model.rsquared) else 0.0
        vif = float(1.0 / max(1e-9, (1.0 - r2)))
        out.append({"feature": c, "vif": vif, "r2_explained_by_others": r2})
    out.sort(key=lambda d: d["vif"], reverse=True)
    return {"vif": out[:25], "note": "ok"}


def _ols_block(df_num: pd.DataFrame, seed: int = 0) -> Dict[str, Any]:
    out: Dict[str, Any] = {"note": "statsmodels/sklearn missing" if (sm is None or train_test_split is None) else "ok"}
    if sm is None or train_test_split is None or r2_score is None or mean_squared_error is None:
        return out

    target = _infer_target(df_num)
    out["target_guess"] = target
    if target is None:
        out["note"] = "no suitable target inferred"
        return out

    y = df_num[target]
    X = df_num.drop(columns=[target])

    # Keep a manageable set of predictors: top variance columns
    vars_ = X.var(numeric_only=True).sort_values(ascending=False)
    keep = list(vars_.head(18).index)
    X = X[keep]

    data = pd.concat([y, X], axis=1).replace([np.inf, -np.inf], np.nan).dropna(axis=0)
    if data.shape[0] < 500:
        out["note"] = "not enough complete rows for OLS"
        return out

    y2 = data[target].values
    X2 = data.drop(columns=[target]).values
    X_train, X_test, y_train, y_test = train_test_split(X2, y2, test_size=0.25, random_state=int(seed))

    X_train_c = sm.add_constant(X_train, has_constant="add")
    X_test_c  = sm.add_constant(X_test, has_constant="add")

    model = sm.OLS(y_train, X_train_c).fit(cov_type="HC3")  # robust SE
    pred = model.predict(X_test_c)

    out["rows_used"] = int(data.shape[0])
    out["predictors"] = keep
    out["r2_test"] = float(r2_score(y_test, pred))
    out["rmse_test"] = float(math.sqrt(mean_squared_error(y_test, pred)))

    # coefficients table (top magnitude)
    coef = []
    params = model.params
    pvals = model.pvalues
    ses = model.bse
    names = ["const"] + keep
    for i, name in enumerate(names):
        coef.append({
            "term": name,
            "coef": float(params[i]),
            "se_hc3": float(ses[i]),
            "p_value": float(pvals[i]) if np.isfinite(pvals[i]) else None,
        })
    coef.sort(key=lambda d: abs(d["coef"]), reverse=True)
    out["coefficients_top"] = coef[:15]

    # assumptions tests (best-effort)
    tests = {}
    try:
        resid = model.resid
        if st is not None:
            jb = st.jarque_bera(resid)
            tests["jarque_bera"] = {"stat": float(jb.statistic), "p_value": float(jb.pvalue)}
        if sms is not None:
            # Breusch–Pagan needs exog w/ constant
            bp = sms.het_breuschpagan(resid, X_train_c)
            tests["breusch_pagan"] = {"lm_stat": float(bp[0]), "lm_pvalue": float(bp[1])}
        # Durbin–Watson
        if sm is not None:
            dw = sm.stats.stattools.durbin_watson(resid)
            tests["durbin_watson"] = float(dw)
    except Exception as e:
        tests["error"] = f"{type(e).__name__}:{e}"
    out["assumptions"] = tests

    # multicollinearity
    out["vif"] = _vif(data.drop(columns=[target]))

    return out


def _regime_detection(df_num: pd.DataFrame, seed: int = 0) -> Dict[str, Any]:
    """
    Regime detection:
      - If a datetime index exists elsewhere, you can feed a time series.
      - Here: we build a 1D proxy series using first principal component or a high-variance column.
      - Use ruptures if installed, else fallback to rolling-variance regime tags.
    """
    out: Dict[str, Any] = {"note": "ok"}
    if df_num.shape[0] < 600:
        return {"note": "not enough rows"}

    # Build a proxy series
    cols = list(df_num.columns)
    s = df_num[cols[0]].copy()
    # choose highest variance column as proxy
    vars_ = df_num.var(numeric_only=True).sort_values(ascending=False)
    if len(vars_) > 0:
        s = df_num[vars_.index[0]].copy()

    s = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if s.shape[0] < 600:
        return {"note": "not enough finite points"}

    x = s.values.astype(float)

    if rpt is not None:
        try:
            # Pelt for change points
            algo = rpt.Pelt(model="rbf").fit(x.reshape(-1, 1))
            bkps = algo.predict(pen=np.log(len(x)) * 3.0)
            # ruptures returns end indices; convert to segments
            segments = []
            start = 0
            for end in bkps:
                segments.append({"start": int(start), "end": int(end)})
                start = int(end)
            out["method"] = "ruptures_pelt_rbf"
            out["breakpoints"] = [int(b) for b in bkps]
            out["segments"] = segments[:20]
        except Exception as e:
            out["method"] = "ruptures_failed_fallback"
            out["error"] = f"{type(e).__name__}:{e}"
    else:
        out["method"] = "rolling_variance_fallback"
        w = 120
        roll = pd.Series(x).rolling(w).var()
        q1 = float(np.nanquantile(roll.values, 0.33))
        q2 = float(np.nanquantile(roll.values, 0.66))
        labels = []
        for v in roll.values:
            if not np.isfinite(v):
                labels.append(None)
            elif v < q1:
                labels.append("low_vol")
            elif v < q2:
                labels.append("mid_vol")
            else:
                labels.append("high_vol")
        out["window"] = w
        out["vol_quantiles"] = {"q33": q1, "q66": q2}
        # summarize runs
        runs = []
        cur = None
        start = 0
        for i, lab in enumerate(labels):
            if lab != cur:
                if cur is not None:
                    runs.append({"label": cur, "start": int(start), "end": int(i)})
                cur = lab
                start = i
        if cur is not None:
            runs.append({"label": cur, "start": int(start), "end": int(len(labels))})
        out["runs"] = runs[:25]

    return out


def _bootstrap_uncertainty(df_num: pd.DataFrame, seed: int = 0) -> Dict[str, Any]:
    out: Dict[str, Any] = {"note": "ok"}
    if st is None or df_num.shape[1] < 2:
        return {"note": "scipy missing or not enough cols"}

    rng = np.random.default_rng(int(seed))
    cols = list(df_num.columns)
    a, b = cols[0], cols[1]

    # pick strongest pearson pair quickly
    best = None
    for i in range(min(12, len(cols))):
        for j in range(i + 1, min(12, len(cols))):
            x = df_num[cols[i]].values
            y = df_num[cols[j]].values
            m = np.isfinite(x) & np.isfinite(y)
            if m.sum() < 300:
                continue
            r, _ = st.pearsonr(x[m], y[m])
            if best is None or abs(r) > abs(best[2]):
                best = (cols[i], cols[j], float(r))
    if best is None:
        return {"note": "no pair with enough data"}
    a, b, r0 = best

    x = df_num[a].values
    y = df_num[b].values
    m = np.isfinite(x) & np.isfinite(y)
    x = x[m]
    y = y[m]
    n = len(x)

    B = 200
    rs = []
    for _ in range(B):
        idx = rng.integers(0, n, size=n)
        r, _ = st.pearsonr(x[idx], y[idx])
        rs.append(float(r))
    lo = float(np.quantile(rs, 0.025))
    hi = float(np.quantile(rs, 0.975))

    out["pair"] = {"a": a, "b": b, "pearson_r": float(r0), "bootstrap_ci95": [lo, hi], "B": B, "n": int(n)}
    return out


def _causal_inference_block(df_num: pd.DataFrame, seed: int = 0) -> Dict[str, Any]:
    """
    Conservative: will NOT claim causality unless an explicit causal library is present.
    If EconML is installed, we can compute a best-effort ATE using a simple DML.
    Otherwise we output "causal signals" only.
    """
    out: Dict[str, Any] = {"note": "signals_only"}
    target = _infer_target(df_num)
    if target is None or df_num.shape[1] < 3:
        return {"note": "not enough structure for causal signals"}

    y = df_num[target]
    X = df_num.drop(columns=[target])
    vars_ = X.var(numeric_only=True).sort_values(ascending=False)
    keep = list(vars_.head(8).index)
    data = pd.concat([y, X[keep]], axis=1).replace([np.inf, -np.inf], np.nan).dropna(axis=0)
    if data.shape[0] < 800:
        return {"note": "not enough complete rows"}

    # Choose "treatment" as top-variance predictor
    treat = keep[0]
    conf = keep[1:] if len(keep) > 1 else []
    out["target_guess"] = target
    out["treatment_guess"] = treat
    out["confounders"] = conf

    # EconML optional
    try:
        from econml.dml import LinearDML
        from sklearn.linear_model import LassoCV
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import Pipeline

        Y = data[target].values
        T = data[treat].values
        W = data[conf].values if conf else None

        model_y = Pipeline([("sc", StandardScaler()), ("m", LassoCV(cv=3, random_state=int(seed)))])
        model_t = Pipeline([("sc", StandardScaler()), ("m", LassoCV(cv=3, random_state=int(seed)))])

        est = LinearDML(model_y=model_y, model_t=model_t, random_state=int(seed))
        est.fit(Y, T, W=W)
        ate = float(np.mean(est.effect(data[conf].values if conf else np.zeros((len(data), 0)))))
        ci = est.effect_interval(data[conf].values if conf else np.zeros((len(data), 0)))
        out["note"] = "econml_dml_best_effort"
        out["ate_estimate"] = ate
        out["ate_ci95"] = [float(np.mean(ci[0])), float(np.mean(ci[1]))]
        return out
    except Exception:
        # Safe fallback: residualization signal (not causal claim)
        if st is None:
            return out
        # partial out confounders with linear regression (if statsmodels present)
        try:
            if sm is None or not conf:
                out["signal"] = "econml_not_available"
                return out
            # residualize T and Y on confounders
            W = sm.add_constant(data[conf].values, has_constant="add")
            mT = sm.OLS(data[treat].values, W).fit()
            mY = sm.OLS(data[target].values, W).fit()
            rT = mT.resid
            rY = mY.resid
            r, p = st.pearsonr(rT, rY)
            out["note"] = "residualized_signal_only"
            out["residualized_corr"] = float(r)
            out["residualized_p"] = float(p)
        except Exception as e:
            out["error"] = f"{type(e).__name__}:{e}"
        return out


def compute_deep_math_v2(df: pd.DataFrame, seed: int = 0, max_cols: int = 18, max_rows: int = 2000) -> Dict[str, Any]:
    """
    World-class deep math:
      - Hypothesis tests + FDR
      - Multivariate modeling (OLS + robust SE + VIF + assumption tests)
      - PCA (loadings + explained variance)
      - Regime detection (ruptures if available; fallback otherwise)
      - Model-based uncertainty (bootstrap)
      - Causal inference (econml optional; otherwise signals-only)
    """
    out: Dict[str, Any] = {"version": "deep_math_v2", "seed": int(seed)}

    # Choose numeric columns and cap size (keep your runtime stable)
    df_num = df.select_dtypes(include="number").copy()
    df_num = _safe_to_numeric(df_num)

    # keep top-variance numeric columns
    if df_num.shape[1] > max_cols:
        v = df_num.var(numeric_only=True).sort_values(ascending=False)
        df_num = df_num[list(v.head(max_cols).index)]

    if df_num.shape[0] > max_rows:
        df_num = df_num.iloc[:max_rows].copy()

    out["rows"] = int(df_num.shape[0])
    out["numeric_cols_n"] = int(df_num.shape[1])
    out["numeric_cols_used"] = list(df_num.columns)

    out["hypothesis_testing"] = _top_pairwise_tests(df_num)
    out["dimensionality_reduction"] = _pca_block(df_num)
    out["multivariate_modeling"] = _ols_block(df_num, seed=seed)
    out["regime_phase_detection"] = _regime_detection(df_num, seed=seed)
    out["model_based_uncertainty"] = _bootstrap_uncertainty(df_num, seed=seed)
    out["causal_inference"] = _causal_inference_block(df_num, seed=seed)

    return out


# Backwards-compatible entrypoint used by your runner:
def compute_deep_math_v1(df: pd.DataFrame, seed: int = 0) -> Dict[str, Any]:
    return compute_deep_math_v2(df, seed=seed)
'''

def backup(p: Path) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)
    return b

def main():
    DEEP_MATH_PATH.parent.mkdir(parents=True, exist_ok=True)

    if DEEP_MATH_PATH.exists():
        b = backup(DEEP_MATH_PATH)
        print(f"[OK] backup -> {b}")

    DEEP_MATH_PATH.write_text(DEEP_MATH_V2, encoding="utf-8", newline="\n")
    print(f"[OK] wrote {DEEP_MATH_PATH}")

    # compile check
    import py_compile
    py_compile.compile(str(DEEP_MATH_PATH), doraise=True)
    print("[OK] deep_math.py compiles")

if __name__ == "__main__":
    main()
