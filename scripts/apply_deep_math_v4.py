from __future__ import annotations

import re
from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]  # .../FantasyAI
RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"
DEEP_MATH = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def write_text(path: Path, s: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(s, encoding="utf-8", newline="\n")

def patch_runner() -> None:
    if not RUNNER.exists():
        raise FileNotFoundError(f"Missing runner: {RUNNER}")

    txt = RUNNER.read_text(encoding="utf-8")

    # If already patched to call compute_deep_math_v1(df, ...), do nothing
    if "compute_deep_math_v1(df" in txt and "deep_math module present but no known entrypoint" not in txt:
        return

    # Replace the existing "Optional deep-math extras" block with a deterministic call.
    # We match the whole block from the comment through attaching _deep_math_file.
    pattern = re.compile(
        r'(?s)\n\s*# Optional deep-math extras.*?'
        r'\(run_dir / "deep_math\.json"\)\.write_text\(json\.dumps\(deep_out, indent=2\), encoding="utf-8"\)\s*'
        r'math_results\["_deep_math_file"\]\s*=\s*"deep_math\.json"\s*'
    )

    replacement = textwrap.dedent(r"""
        # Optional deep-math extras (deterministic): call compute_deep_math_v1() if available
        if getattr(args, "deep_math", False):
            try:
                deep_out = compute_deep_math_v1(df, seed=int(args.seed))
            except Exception as e:
                deep_out = {"error": f"deep_math_failed:{type(e).__name__}:{e}"}

            (run_dir / "deep_math.json").write_text(json.dumps(deep_out, indent=2), encoding="utf-8")
            math_results["_deep_math_file"] = "deep_math.json"
    """)

    if pattern.search(txt):
        txt = pattern.sub("\n" + replacement + "\n", txt, count=1)
    else:
        # Fallback: insert block right after math_results.json write if the pattern changed.
        marker = r'(run_dir / "math_results\.json"\)\.write_text\(json\.dumps\(math_results, indent=2\), encoding="utf-8"\)'
        m = re.search(marker, txt)
        if not m:
            raise RuntimeError("Could not locate math_results.json write marker in runner.")
        insert_at = m.end()
        txt = txt[:insert_at] + "\n" + replacement + "\n" + txt[insert_at:]

    RUNNER.write_text(txt, encoding="utf-8", newline="\n")

def install_deep_math() -> None:
    # A robust deep math module:
    # - hypothesis testing
    # - multivariate modeling (OLS)
    # - dimensionality reduction (PCA)
    # - regime detection (simple change-point on a numeric series)
    # - uncertainty (bootstrap CI)
    # All with graceful fallbacks if optional deps are missing.
    mod = textwrap.dedent(r'''
        from __future__ import annotations

        from dataclasses import dataclass
        from typing import Any, Dict, Optional, Tuple, List

        import numpy as np
        import pandas as pd

        def _numeric_df(df: pd.DataFrame, max_cols: int = 25) -> pd.DataFrame:
            num = df.select_dtypes(include="number").copy()
            if num.shape[1] == 0:
                return num
            # Drop all-NaN columns
            num = num.dropna(axis=1, how="all")
            # cap columns for speed
            return num.iloc[:, :max_cols]

        def _pick_target(num: pd.DataFrame) -> Optional[str]:
            # pick a column with enough variance and non-missingness
            best = None
            best_score = -1.0
            for c in num.columns:
                s = num[c]
                ok = s.notna().mean()
                if ok < 0.6:
                    continue
                v = float(np.nanstd(s.values))
                score = ok * v
                if score > best_score:
                    best_score = score
                    best = c
            return best

        def _safe_float(x: Any) -> Optional[float]:
            try:
                if x is None or (isinstance(x, float) and np.isnan(x)):
                    return None
                return float(x)
            except Exception:
                return None

        def _bootstrap_ci(x: np.ndarray, stat_fn, n: int = 400, alpha: float = 0.05, seed: int = 0) -> Dict[str, Any]:
            rng = np.random.default_rng(seed)
            x = x[np.isfinite(x)]
            if x.size < 20:
                return {"note": "not_enough_data_for_bootstrap", "n": int(x.size)}
            stats = []
            for _ in range(n):
                samp = rng.choice(x, size=x.size, replace=True)
                stats.append(stat_fn(samp))
            stats = np.array(stats, dtype=float)
            lo = float(np.quantile(stats, alpha / 2))
            hi = float(np.quantile(stats, 1 - alpha / 2))
            return {"n": int(x.size), "boot_n": int(n), "ci_low": lo, "ci_high": hi, "stat_mean": float(np.mean(stats))}

        def _simple_changepoint(x: np.ndarray) -> Dict[str, Any]:
            # very simple O(n) scan using mean-shift score
            x = x[np.isfinite(x)]
            n = x.size
            if n < 200:
                return {"note": "not_enough_data_for_regime_detection", "n": int(n)}
            cums = np.cumsum(x)
            best_k = None
            best_score = -np.inf
            for k in range(50, n - 50):
                m1 = cums[k-1] / k
                m2 = (cums[-1] - cums[k-1]) / (n - k)
                score = abs(m1 - m2)
                if score > best_score:
                    best_score = score
                    best_k = k
            return {"n": int(n), "changepoint_index": int(best_k), "mean_shift_abs": float(best_score)}

        def compute_deep_math_v1(df: pd.DataFrame, seed: int = 0) -> Dict[str, Any]:
            out: Dict[str, Any] = {"version": "deep_math_v1"}

            num = _numeric_df(df, max_cols=30)
            out["numeric_cols_used"] = list(num.columns)
            out["numeric_cols_n"] = int(num.shape[1])
            out["rows"] = int(df.shape[0])

            if num.shape[1] == 0 or df.shape[0] < 50:
                out["note"] = "no_numeric_data_or_too_few_rows"
                return out

            # --- hypothesis testing: correlation significance via scipy (fallback to none)
            hyp: Dict[str, Any] = {}
            try:
                from scipy import stats  # type: ignore
                cols = list(num.columns)
                pairs: List[Dict[str, Any]] = []
                for i in range(min(len(cols), 12)):
                    for j in range(i+1, min(len(cols), 12)):
                        a, b = cols[i], cols[j]
                        x = pd.to_numeric(num[a], errors="coerce")
                        y = pd.to_numeric(num[b], errors="coerce")
                        mask = x.notna() & y.notna()
                        if mask.mean() < 0.6:
                            continue
                        r, p = stats.pearsonr(x[mask].values, y[mask].values)
                        pairs.append({"a": a, "b": b, "pearson_r": float(r), "p_value": float(p), "n": int(mask.sum())})
                pairs.sort(key=lambda d: abs(d["pearson_r"]), reverse=True)
                hyp["top_correlations_with_p"] = pairs[:15]
            except Exception as e:
                hyp["error"] = f"hypothesis_tests_failed:{type(e).__name__}:{e}"
            out["hypothesis_tests"] = hyp

            # --- multivariate modeling: OLS predicting a chosen target
            multi: Dict[str, Any] = {}
            try:
                import statsmodels.api as sm  # type: ignore
                target = _pick_target(num)
                if target is None:
                    multi["note"] = "no_target_found"
                else:
                    y = pd.to_numeric(num[target], errors="coerce")
                    X = num.drop(columns=[target]).apply(pd.to_numeric, errors="coerce")
                    # basic impute + drop low-variance
                    X = X.dropna(axis=1, how="all")
                    X = X.loc[:, X.std(numeric_only=True) > 1e-12]
                    # row filter
                    mask = y.notna()
                    for c in X.columns:
                        mask &= X[c].notna()
                    if int(mask.sum()) < 200 or X.shape[1] < 2:
                        multi["note"] = "not_enough_rows_or_features_for_ols"
                        multi["target"] = target
                        multi["n_rows"] = int(mask.sum())
                        multi["n_features"] = int(X.shape[1])
                    else:
                        X2 = sm.add_constant(X[mask].astype(float), has_constant="add")
                        model = sm.OLS(y[mask].astype(float), X2).fit(cov_type="HC3")
                        coefs = []
                        for name, val in model.params.items():
                            if name == "const":
                                continue
                            coefs.append({
                                "feature": str(name),
                                "coef": float(val),
                                "p_value": float(model.pvalues[name]),
                            })
                        coefs.sort(key=lambda d: abs(d["coef"]), reverse=True)
                        multi["target"] = target
                        multi["n_rows"] = int(mask.sum())
                        multi["r2"] = _safe_float(model.rsquared)
                        multi["adj_r2"] = _safe_float(model.rsquared_adj)
                        multi["top_effects"] = coefs[:12]
            except Exception as e:
                multi["error"] = f"multivariate_modeling_failed:{type(e).__name__}:{e}"
            out["multivariate_modeling"] = multi

            # --- dimensionality reduction: PCA
            dr: Dict[str, Any] = {}
            try:
                from sklearn.decomposition import PCA  # type: ignore
                X = num.apply(pd.to_numeric, errors="coerce").fillna(num.median(numeric_only=True))
                # standardize
                Xs = (X - X.mean()) / (X.std(ddof=0) + 1e-12)
                pca = PCA(n_components=min(6, Xs.shape[1]), random_state=seed)
                Z = pca.fit_transform(Xs.values)
                dr["explained_variance_ratio"] = [float(v) for v in pca.explained_variance_ratio_]
                # top loadings for first 2 PCs
                comps = []
                for k in range(min(2, pca.components_.shape[0])):
                    w = pca.components_[k]
                    pairs = sorted(zip(Xs.columns, w), key=lambda t: abs(t[1]), reverse=True)[:10]
                    comps.append({"pc": k+1, "top_loadings": [{"feature": str(a), "loading": float(b)} for a,b in pairs]})
                dr["components"] = comps
            except Exception as e:
                dr["error"] = f"dimensionality_reduction_failed:{type(e).__name__}:{e}"
            out["dimensionality_reduction"] = dr

            # --- regime / phase detection (simple)
            reg: Dict[str, Any] = {}
            try:
                # choose the target if available, else first numeric column
                col = None
                if isinstance(multi.get("target"), str):
                    col = multi["target"]
                if col is None and num.shape[1] > 0:
                    col = str(num.columns[0])
                x = pd.to_numeric(num[col], errors="coerce").values
                reg["signal_col"] = col
                reg["changepoint"] = _simple_changepoint(x)
            except Exception as e:
                reg["error"] = f"regime_detection_failed:{type(e).__name__}:{e}"
            out["regime_phase_detection"] = reg

            # --- model-based uncertainty: bootstrap CI for mean of target
            unc: Dict[str, Any] = {}
            try:
                tcol = None
                if isinstance(multi.get("target"), str):
                    tcol = multi["target"]
                if tcol is None and num.shape[1] > 0:
                    tcol = str(num.columns[0])
                x = pd.to_numeric(num[tcol], errors="coerce").values
                unc["target_col"] = tcol
                unc["mean_ci"] = _bootstrap_ci(x, stat_fn=np.mean, n=400, alpha=0.05, seed=seed)
            except Exception as e:
                unc["error"] = f"uncertainty_failed:{type(e).__name__}:{e}"
            out["model_based_uncertainty"] = unc

            # --- formal assumptions testing (lightweight)
            asm: Dict[str, Any] = {}
            try:
                from scipy import stats  # type: ignore
                tcol = None
                if isinstance(multi.get("target"), str):
                    tcol = multi["target"]
                if tcol is None and num.shape[1] > 0:
                    tcol = str(num.columns[0])
                x = pd.to_numeric(num[tcol], errors="coerce")
                x = x.dropna()
                if x.size < 200:
                    asm["note"] = "not_enough_data_for_assumption_tests"
                    asm["n"] = int(x.size)
                else:
                    # normality proxy: Jarque-Bera
                    jb = stats.jarque_bera(x.values)
                    asm["jarque_bera"] = {"stat": float(jb.statistic), "p_value": float(jb.pvalue), "n": int(x.size)}
            except Exception as e:
                asm["error"] = f"assumption_tests_failed:{type(e).__name__}:{e}"
            out["formal_assumptions_testing"] = asm

            return out

        # Compatibility wrappers for older runner logic
        def compute(df: pd.DataFrame) -> Dict[str, Any]:
            return compute_deep_math_v1(df, seed=0)

        def run(df: pd.DataFrame) -> Dict[str, Any]:
            return compute_deep_math_v1(df, seed=0)

        def analyze(df: pd.DataFrame) -> Dict[str, Any]:
            return compute_deep_math_v1(df, seed=0)
    ''').lstrip()

    write_text(DEEP_MATH, mod)

def main() -> None:
    install_deep_math()
    patch_runner()
    print(f"[OK] wrote: {DEEP_MATH}")
    print(f"[OK] patched runner: {RUNNER}")

if __name__ == "__main__":
    main()
