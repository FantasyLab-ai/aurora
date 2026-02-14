from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re
import sys

ROOT = Path(__file__).resolve().parents[1]

FORECASTING = ROOT / "fantasyai" / "aurora" / "math" / "forecasting.py"
CAUSAL      = ROOT / "fantasyai" / "aurora" / "math" / "causal.py"
MATHSTACK   = ROOT / "fantasyai" / "aurora" / "mathstack_v2.py"

START = "# === WORLDCLASS_COMPAT_START ==="
END   = "# === WORLDCLASS_COMPAT_END ==="


def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak


def upsert_block(p: Path, block_text: str) -> None:
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if START in txt and END in txt:
        pre, rest = txt.split(START, 1)
        _, post = rest.split(END, 1)
        new_txt = pre.rstrip() + "\n\n" + START + "\n" + block_text.rstrip() + "\n" + END + "\n" + post.lstrip()
    else:
        new_txt = txt.rstrip() + "\n\n" + START + "\n" + block_text.rstrip() + "\n" + END + "\n"
    p.write_text(new_txt, encoding="utf-8")


def ensure_file_exists(p: Path) -> None:
    if not p.exists():
        raise SystemExit(f"Missing file: {p}")


FORECASTING_BLOCK = r'''
# Stable forecast_with_bands() used by deep_math_v3.
# Accepts extra kwargs (e.g. seasonal_periods) without breaking.
def forecast_with_bands(
    y,
    horizon: int = 24,
    seasonal_periods=None,
    alpha: float = 0.05,
    n_boot: int = 500,
    **kwargs
):
    """
    Return a simple forecast with uncertainty bands.

    Parameters
    ----------
    y : array-like
        Time series values (1D).
    horizon : int
        Forecast horizon (steps).
    seasonal_periods : any
        Accepted for compatibility; not required by this simple model.
    alpha : float
        Two-sided error rate for bands (e.g., 0.05 => 95% band).
    n_boot : int
        Number of bootstrap simulations.
    **kwargs :
        Ignored extras for API stability.

    Returns
    -------
    dict with keys: method, horizon, yhat, bands, note
    """
    try:
        import numpy as np
    except Exception as e:
        return {"error": f"ImportError:numpy:{type(e).__name__}:{e}"}

    yy = np.asarray(list(y), dtype=float)
    yy = yy[np.isfinite(yy)]
    if yy.size < 5:
        return {"error": f"ValueError:need_more_data:n={int(yy.size)}"}

    # AR(1) on differences for robustness
    x = yy[:-1]
    z = yy[1:]
    vx = np.var(x)
    if not np.isfinite(vx) or vx <= 1e-12:
        phi = 0.0
        c = float(np.nanmean(z))
    else:
        phi = float(np.cov(x, z, bias=True)[0, 1] / vx)
        c = float(np.nanmean(z) - phi * np.nanmean(x))

    resid = z - (c + phi * x)
    resid = resid[np.isfinite(resid)]
    if resid.size < 5:
        resid = np.array([0.0], dtype=float)

    last = float(yy[-1])
    yhat = []
    cur = last
    for _ in range(int(horizon)):
        cur = c + phi * cur
        yhat.append(float(cur))

    # Monte Carlo bands
    sims = np.empty((int(n_boot), int(horizon)), dtype=float)
    for b in range(int(n_boot)):
        cur = last
        for t in range(int(horizon)):
            eps = float(np.random.choice(resid))
            cur = (c + phi * cur) + eps
            sims[b, t] = cur

    lo = np.quantile(sims, alpha / 2.0, axis=0).tolist()
    hi = np.quantile(sims, 1.0 - alpha / 2.0, axis=0).tolist()

    return {
        "method": "AR1_MC",
        "horizon": int(horizon),
        "seasonal_periods": seasonal_periods,
        "yhat": yhat,
        "bands": {"p_lo": lo, "p_hi": hi},
        "note": "ok"
    }
'''.strip()


CAUSAL_BLOCK = r'''
# Stable causal_what_if_linear() used by deep_math_v3.
# Accepts target_col kwarg + extra kwargs without breaking.
def causal_what_if_linear(
    df,
    treatment_col: str,
    target_col: str,
    delta: float = 1.0,
    covariates=None,
    **kwargs
):
    """
    Simple, stable what-if estimator (linear) for numeric treatment/target.

    Fits: target ~ a + b*treatment (+ optional covariates)
    Returns estimated effect of +delta change in treatment.
    """
    try:
        import numpy as np
        import pandas as pd
    except Exception as e:
        return {"error": f"ImportError:{type(e).__name__}:{e}"}

    if df is None:
        return {"error": "ValueError:df_is_none"}

    if treatment_col not in df.columns or target_col not in df.columns:
        return {"error": f"KeyError:missing_cols:treatment={treatment_col} target={target_col}"}

    work = df.copy()

    cols = [treatment_col, target_col]
    if covariates:
        cols += list(covariates)

    work = work[cols].replace([np.inf, -np.inf], np.nan).dropna()
    if len(work) < 25:
        return {"error": f"ValueError:need_more_rows:n={int(len(work))}"}

    y = work[target_col].astype(float).to_numpy()
    t = work[treatment_col].astype(float).to_numpy()

    X_parts = [np.ones(len(work)), t]
    cov_used = []
    if covariates:
        for c in covariates:
            if c in work.columns:
                X_parts.append(work[c].astype(float).to_numpy())
                cov_used.append(c)

    X = np.vstack(X_parts).T  # [1, t, covs...]

    # OLS via lstsq
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    a = float(beta[0])
    b = float(beta[1])

    baseline_pred = float(a + b * float(np.mean(t)))
    counterfactual_pred = float(a + b * float(np.mean(t) + delta))

    return {
        "method": "OLS",
        "treatment_col": treatment_col,
        "target_col": target_col,
        "covariates_used": cov_used,
        "intercept": a,
        "coef_treatment": b,
        "delta": float(delta),
        "estimated_delta_effect": float(b * delta),
        "baseline_pred_at_mean_treatment": baseline_pred,
        "counterfactual_pred_at_mean_plus_delta": counterfactual_pred,
        "n": int(len(work)),
        "note": "ok"
    }
'''.strip()


MATHSTACK_BLOCK = r'''
# Stable runner entrypoint expected by scripts/run_aurora_dataset_runner.py
# - Accepts seed/out_dir even if compute_math_stack_v2 doesn't.
# - Filters kwargs safely.
def run_mathstack_v2(df, seed=None, out_dir=None, **kwargs):
    """
    Wrapper around compute_math_stack_v2(df, ...) with backward/forward compatible args.
    """
    try:
        # compute_math_stack_v2 is expected to exist in this module.
        fn = compute_math_stack_v2  # noqa: F821
    except Exception as e:
        return {"error": f"mathstack_v2_missing_compute:{type(e).__name__}:{e}"}

    import inspect

    sig = None
    try:
        sig = inspect.signature(fn)
    except Exception:
        sig = None

    call_kwargs = {}
    if sig is not None:
        for k, v in kwargs.items():
            if k in sig.parameters:
                call_kwargs[k] = v

    # never pass seed/out_dir unless the underlying function supports it
    if sig is not None and "seed" in sig.parameters and seed is not None:
        call_kwargs["seed"] = seed
    if sig is not None and "out_dir" in sig.parameters and out_dir is not None:
        call_kwargs["out_dir"] = out_dir

    try:
        res = fn(df=df, **call_kwargs)
        return res if isinstance(res, dict) else {"result": res, "note": "ok"}
    except Exception as e:
        import traceback
        return {"error": f"mathstack_v2_failed:{type(e).__name__}:{e}", "trace": traceback.format_exc()}


# aliases (some runner versions expect these names)
def run_math_stack_v2(df, seed=None, out_dir=None, **kwargs):
    return run_mathstack_v2(df=df, seed=seed, out_dir=out_dir, **kwargs)
'''.strip()


def main() -> int:
    for p in (FORECASTING, CAUSAL, MATHSTACK):
        ensure_file_exists(p)

    # backups
    b1 = backup(FORECASTING); print(f"[OK] backup -> {b1}")
    b2 = backup(CAUSAL);      print(f"[OK] backup -> {b2}")
    b3 = backup(MATHSTACK);   print(f"[OK] backup -> {b3}")

    # patch files (idempotent)
    upsert_block(FORECASTING, FORECASTING_BLOCK)
    print(f"[OK] forecasting.py: installed stable forecast_with_bands()")

    upsert_block(CAUSAL, CAUSAL_BLOCK)
    print(f"[OK] causal.py: installed stable causal_what_if_linear()")

    # mathstack: only install wrapper if compute_math_stack_v2 exists
    ms_txt = MATHSTACK.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"(?m)^\s*def\s+compute_math_stack_v2\s*\(", ms_txt):
        upsert_block(MATHSTACK, MATHSTACK_BLOCK)
        print(f"[OK] mathstack_v2.py: installed stable run_mathstack_v2() wrapper + aliases")
    else:
        print("[WARN] mathstack_v2.py: compute_math_stack_v2 not found; skipped wrapper install")

    # compile checks (hard fail)
    import py_compile
    py_compile.compile(str(FORECASTING), doraise=True)
    py_compile.compile(str(CAUSAL), doraise=True)
    py_compile.compile(str(MATHSTACK), doraise=True)
    print("[DONE] Worldclass restore applied + py_compile clean")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
