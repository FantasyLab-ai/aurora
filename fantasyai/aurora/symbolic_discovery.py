"""
Symbolic discovery — discover novel equations from data using a curated form library.

Distinct from the existing ``symbolic_regression.py`` (which wraps gplearn,
requires Julia/external deps). This module is **pure numpy + scipy**, fully
glass-box, and deterministic. The cost: limited expressiveness — we can only
discover forms we've templated. The benefit: works everywhere, fast, every
considered form is explicitly named (no hidden GP search).

Approach
--------

For a target ``y`` and one or two correlated features ``x``, we fit a small
library of forms by least-squares and rank by AIC:

  1-feature:
    - linear, quadratic, cubic         (polynomial baselines)
    - power_law         y = a·x^b      (Stefan-Boltzmann, Kepler's 3rd, allometry)
    - exponential       y = a·exp(b·x) + c
    - log               y = a·log(x) + b
    - sigmoid           y = L/(1 + exp(-k·(x - x₀)))
    - saturation        y = Vmax·x / (K + x)   (Michaelis-Menten / Hill n=1)
    - inverse           y = a/x + b

  2-feature:
    - allometric_2var   y = a·x₁^b·x₂^c        (Pierson-Moskowitz, Kleiber)
    - bilinear          y = a + b·x₁ + c·x₂ + d·x₁·x₂
    - ratio_law         y = a·x₁/x₂

When a fitted form's exponents match a known signature (e.g. b≈2, c≈0.5 →
Pierson-Moskowitz), we annotate ``recovers_known_form`` so the UI can say
"Discovered relation matches Pierson-Moskowitz form."

Public API
----------

    discover_relation(df, target_col, feature_cols=None,
                      max_features=2, top_k=5) -> list[dict]
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

try:
    from scipy.optimize import curve_fit
    _HAS_SCIPY = True
except Exception:
    _HAS_SCIPY = False


# ----------------------------------------------------------------------
# Result dataclass
# ----------------------------------------------------------------------

@dataclass
class FitResult:
    form_name: str
    equation: str
    template: str
    params: Dict[str, float]
    rmse: float
    aic: float
    r2: float
    n: int
    residual_std: float
    features: List[str]
    recovers_known_form: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                d[k] = None
        return d


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _aic(rss: float, n: int, k: int) -> float:
    if n <= 0 or rss <= 0:
        return float("nan")
    return float(n * math.log(rss / n) + 2 * k)


def _r2(y: np.ndarray, yhat: np.ndarray) -> float:
    yt = np.asarray(y, dtype=float); yp = np.asarray(yhat, dtype=float)
    mask = np.isfinite(yt) & np.isfinite(yp)
    yt = yt[mask]; yp = yp[mask]
    if yt.size < 2:
        return 0.0
    ss_res = float(np.sum((yt - yp) ** 2))
    ss_tot = float(np.sum((yt - np.mean(yt)) ** 2))
    if ss_tot == 0:
        return 0.0
    return 1.0 - ss_res / ss_tot


def _safe_curve_fit(fn, xdata, ydata, p0, bounds=None, maxfev=2000):
    if not _HAS_SCIPY:
        return None, False
    try:
        kwargs = {"p0": p0, "maxfev": maxfev}
        if bounds is not None:
            kwargs["bounds"] = bounds
        popt, _ = curve_fit(fn, xdata, ydata, **kwargs)
        return popt, True
    except Exception:
        return None, False


def _build_result(form_name: str, template: str, equation: str,
                  params: Dict[str, float], y: np.ndarray, yhat: np.ndarray,
                  features: List[str], k: int) -> FitResult:
    mask = np.isfinite(y) & np.isfinite(yhat)
    n = int(mask.sum())
    if n == 0:
        return FitResult(form_name=form_name, equation=equation, template=template,
                         params={k_: float(v) for k_, v in params.items()},
                         rmse=float("nan"), aic=float("nan"), r2=0.0,
                         n=0, residual_std=0.0, features=list(features))
    resid = y[mask] - yhat[mask]
    rss = float(np.sum(resid ** 2))
    rmse = float(np.sqrt(rss / max(n, 1)))
    return FitResult(
        form_name=form_name, equation=equation, template=template,
        params={k_: float(v) for k_, v in params.items()},
        rmse=rmse, aic=_aic(rss, n, k), r2=_r2(y, yhat),
        n=n, residual_std=float(np.std(resid)) if resid.size else 0.0,
        features=list(features),
    )


def _attach_known(r: FitResult, known: str) -> FitResult:
    r.recovers_known_form = known
    return r


# ----------------------------------------------------------------------
# 1-feature fitters
# ----------------------------------------------------------------------

def _fit_linear(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    if x.size < 5:
        return None
    a, b = np.polyfit(x, y, 1)
    yhat = a * x + b
    return _build_result(
        "linear", "y = a·x + b", f"y = {a:.4g}·{feat} + {b:.4g}",
        {"a": float(a), "b": float(b)}, y, yhat, [feat], k=2,
    )


def _fit_quadratic(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    if x.size < 6:
        return None
    a, b, c = np.polyfit(x, y, 2)
    yhat = a * x ** 2 + b * x + c
    return _build_result(
        "quadratic", "y = a·x² + b·x + c",
        f"y = {a:.4g}·{feat}² + {b:.4g}·{feat} + {c:.4g}",
        {"a": float(a), "b": float(b), "c": float(c)}, y, yhat, [feat], k=3,
    )


def _fit_cubic(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    if x.size < 8:
        return None
    a, b, c, d = np.polyfit(x, y, 3)
    yhat = a * x ** 3 + b * x ** 2 + c * x + d
    return _build_result(
        "cubic", "y = a·x³ + b·x² + c·x + d",
        f"y = {a:.4g}·{feat}³ + {b:.4g}·{feat}² + {c:.4g}·{feat} + {d:.4g}",
        {"a": float(a), "b": float(b), "c": float(c), "d": float(d)},
        y, yhat, [feat], k=4,
    )


def _fit_power_law(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    """y = a·x^b — log-log linear regression. Requires x > 0, y > 0."""
    mask = (x > 0) & (y > 0) & np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 5:
        return None
    lx = np.log(x[mask]); ly = np.log(y[mask])
    b, log_a = np.polyfit(lx, ly, 1)
    a = math.exp(log_a)
    yhat = np.where(x > 0, a * np.power(np.where(x > 0, x, 1), b), np.nan)
    eq = f"y = {a:.4g}·{feat}^{b:.3g}"
    r = _build_result(
        "power_law", "y = a·x^b", eq,
        {"a": float(a), "b": float(b)}, y, yhat, [feat], k=2,
    )
    # Recognize known signatures.
    if abs(b - 4.0) < 0.5:
        return _attach_known(r, "Stefan-Boltzmann form (flux ∝ T⁴)")
    if abs(b - 0.75) < 0.15:
        return _attach_known(r, "Kleiber's law form (rate ∝ size^¾)")
    if abs(b - 1.5) < 0.2:
        return _attach_known(r, "Kepler's 3rd law form (period ∝ size^³ᐟ²)")
    return r


def _fit_exponential(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    if not _HAS_SCIPY or x.size < 6:
        return None
    span = float(x.max() - x.min()) or 1.0
    x_norm = (x - x.min()) / span
    p0 = [float(np.nanmax(y) - np.nanmedian(y)) or 1.0, 0.5,
          float(np.nanmedian(y))]
    popt, ok = _safe_curve_fit(lambda xx, A, B, C: A * np.exp(B * xx) + C,
                                x_norm, y, p0=p0)
    if not ok:
        return None
    A, B, C = (float(p) for p in popt)
    yhat = A * np.exp(B * x_norm) + C
    eq = f"y = {A:.4g}·exp({B:.3g}·{feat}_norm) + {C:.4g}"
    return _build_result(
        "exponential", "y = a·exp(b·x) + c", eq,
        {"a": A, "b": B, "c": C, "x_span": span, "x_min": float(x.min())},
        y, yhat, [feat], k=3,
    )


def _fit_log(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    mask = (x > 0) & np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 5:
        return None
    lx = np.log(x[mask])
    a, b = np.polyfit(lx, y[mask], 1)
    yhat = np.where(x > 0, a * np.log(np.where(x > 0, x, 1)) + b, np.nan)
    eq = f"y = {a:.4g}·log({feat}) + {b:.4g}"
    return _build_result(
        "log", "y = a·log(x) + b", eq,
        {"a": float(a), "b": float(b)}, y, yhat, [feat], k=2,
    )


def _fit_inverse(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    mask = (np.abs(x) > 1e-9) & np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 5:
        return None
    inv = 1.0 / x[mask]
    a, b = np.polyfit(inv, y[mask], 1)
    yhat = np.where(np.abs(x) > 1e-9,
                     a / np.where(np.abs(x) > 1e-9, x, 1) + b, np.nan)
    eq = f"y = {a:.4g}/{feat} + {b:.4g}"
    return _build_result(
        "inverse", "y = a/x + b", eq,
        {"a": float(a), "b": float(b)}, y, yhat, [feat], k=2,
    )


def _fit_sigmoid(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    if not _HAS_SCIPY or x.size < 8:
        return None
    L0 = float(np.nanmax(y)) or 1.0
    x0 = float(np.nanmedian(x))
    k0 = 1.0 / (float(np.nanstd(x)) or 1.0)
    popt, ok = _safe_curve_fit(
        lambda xx, L, k, x0: L / (1.0 + np.exp(-k * (xx - x0))),
        x, y, p0=[L0, k0, x0],
    )
    if not ok:
        return None
    L, k, x0 = (float(p) for p in popt)
    yhat = L / (1.0 + np.exp(-k * (x - x0)))
    eq = f"y = {L:.4g} / (1 + exp(-{k:.3g}·({feat} - {x0:.3g})))"
    return _build_result(
        "sigmoid", "y = L/(1 + exp(-k·(x - x₀)))", eq,
        {"L": L, "k": k, "x0": x0}, y, yhat, [feat], k=3,
    )


def _fit_saturation(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    """Michaelis-Menten / Hill (n=1): y = Vmax·x / (K + x)."""
    if not _HAS_SCIPY:
        return None
    mask = (x >= 0) & np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 6:
        return None
    Vmax0 = float(np.nanmax(y)) or 1.0
    K0 = float(np.nanmedian(x[mask])) or 1.0
    popt, ok = _safe_curve_fit(
        lambda xx, V, K: V * xx / (K + xx),
        x[mask], y[mask], p0=[Vmax0, K0],
        bounds=([0, 1e-9], [np.inf, np.inf]),
    )
    if not ok:
        return None
    V, K = (float(p) for p in popt)
    yhat = np.where(x >= 0,
                     V * x / (K + np.where(x >= 0, x, 0)), np.nan)
    eq = f"y = {V:.4g}·{feat} / ({K:.3g} + {feat})"
    r = _build_result(
        "saturation", "y = Vmax·x / (K + x)", eq,
        {"Vmax": V, "K": K}, y, yhat, [feat], k=2,
    )
    return _attach_known(r, "Michaelis-Menten / Hill (n=1) form")


# ----------------------------------------------------------------------
# 2-feature fitters
# ----------------------------------------------------------------------

def _fit_allometric_2var(x1: np.ndarray, x2: np.ndarray, y: np.ndarray,
                          feat1: str, feat2: str) -> Optional[FitResult]:
    """y = a · x1^b · x2^c — covers Pierson-Moskowitz, Kleiber, etc."""
    mask = (x1 > 0) & (x2 > 0) & (y > 0) & np.isfinite(x1) & np.isfinite(x2) & np.isfinite(y)
    if mask.sum() < 8:
        return None
    X = np.column_stack([np.ones(mask.sum()), np.log(x1[mask]), np.log(x2[mask])])
    beta, *_ = np.linalg.lstsq(X, np.log(y[mask]), rcond=None)
    log_a, b, c = beta
    a = math.exp(log_a)
    yhat = np.where(mask,
                     a * np.power(np.where(x1 > 0, x1, 1), b) *
                         np.power(np.where(x2 > 0, x2, 1), c),
                     np.nan)
    eq = f"y = {a:.4g}·{feat1}^{b:.3g}·{feat2}^{c:.3g}"
    r = _build_result(
        "allometric_2var", "y = a·x₁^b·x₂^c", eq,
        {"a": float(a), "b": float(b), "c": float(c)},
        y, yhat, [feat1, feat2], k=3,
    )
    if abs(b - 2.0) < 0.3 and abs(c - 0.5) < 0.3:
        return _attach_known(r, "Pierson-Moskowitz form (wave height ∝ wind² · √fetch)")
    if abs(b - 0.75) < 0.15 and abs(c) < 0.2:
        return _attach_known(r, "Kleiber's law form (metabolism ∝ mass^¾)")
    return r


def _fit_bilinear(x1: np.ndarray, x2: np.ndarray, y: np.ndarray,
                  feat1: str, feat2: str) -> Optional[FitResult]:
    """y = a + b·x1 + c·x2 + d·x1·x2 (interaction model)."""
    if x1.size < 8:
        return None
    X = np.column_stack([np.ones_like(x1, dtype=float), x1, x2, x1 * x2])
    mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
    if mask.sum() < 8:
        return None
    beta, *_ = np.linalg.lstsq(X[mask], y[mask], rcond=None)
    a, b, c, d = (float(v) for v in beta)
    yhat = X @ beta
    eq = f"y = {a:.4g} + {b:.4g}·{feat1} + {c:.4g}·{feat2} + {d:.4g}·{feat1}·{feat2}"
    return _build_result(
        "bilinear", "y = a + b·x₁ + c·x₂ + d·x₁·x₂", eq,
        {"a": a, "b": b, "c": c, "d": d}, y, yhat, [feat1, feat2], k=4,
    )


def _fit_ratio_law(x1: np.ndarray, x2: np.ndarray, y: np.ndarray,
                    feat1: str, feat2: str) -> Optional[FitResult]:
    """y = a · x1 / x2."""
    mask = (np.abs(x2) > 1e-9) & np.isfinite(x1) & np.isfinite(x2) & np.isfinite(y)
    if mask.sum() < 5:
        return None
    ratio = x1[mask] / x2[mask]
    a = float(np.sum(y[mask] * ratio) / np.sum(ratio * ratio))
    yhat = np.where(np.abs(x2) > 1e-9,
                     a * x1 / np.where(np.abs(x2) > 1e-9, x2, 1), np.nan)
    eq = f"y = {a:.4g}·{feat1}/{feat2}"
    r = _build_result(
        "ratio_law", "y = a·x₁/x₂", eq,
        {"a": a}, y, yhat, [feat1, feat2], k=1,
    )
    # Little's Law signature: y = λ·W ↔ L (queue length) = arrival_rate · wait_time
    return r


# ----------------------------------------------------------------------
# Extended 1-feature library (v2.0)
# ----------------------------------------------------------------------

def _fit_gaussian(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    """y = a·exp(-((x-μ)/σ)²) — bell curve, spectral line, normal density."""
    if not _HAS_SCIPY or x.size < 8:
        return None
    a0 = float(np.nanmax(y) - np.nanmin(y)) or 1.0
    mu0 = float(x[np.nanargmax(y)]) if np.isfinite(np.nanmax(y)) else float(np.nanmean(x))
    sig0 = float(np.nanstd(x)) or 1.0
    popt, ok = _safe_curve_fit(
        lambda xx, a, mu, sigma: a * np.exp(-((xx - mu) / max(sigma, 1e-9)) ** 2),
        x, y, p0=[a0, mu0, sig0],
    )
    if not ok:
        return None
    a, mu, sig = (float(p) for p in popt)
    yhat = a * np.exp(-((x - mu) / max(sig, 1e-9)) ** 2)
    eq = f"y = {a:.4g}·exp(−(({feat}−{mu:.3g})/{sig:.3g})²)"
    return _build_result(
        "gaussian", "y = a·exp(−((x−μ)/σ)²)", eq,
        {"a": a, "mu": mu, "sigma": sig}, y, yhat, [feat], k=3,
    )


def _fit_lorentzian(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    """y = a / (1 + ((x-μ)/γ)²) — resonance peak, spectral line."""
    if not _HAS_SCIPY or x.size < 8:
        return None
    a0 = float(np.nanmax(y)) or 1.0
    mu0 = float(x[np.nanargmax(y)]) if np.isfinite(np.nanmax(y)) else 0.0
    g0 = float(np.nanstd(x)) or 1.0
    popt, ok = _safe_curve_fit(
        lambda xx, a, mu, g: a / (1.0 + ((xx - mu) / max(g, 1e-9)) ** 2),
        x, y, p0=[a0, mu0, g0],
    )
    if not ok:
        return None
    a, mu, g = (float(p) for p in popt)
    yhat = a / (1.0 + ((x - mu) / max(g, 1e-9)) ** 2)
    eq = f"y = {a:.4g} / (1 + (({feat}−{mu:.3g})/{g:.3g})²)"
    return _build_result(
        "lorentzian", "y = a / (1 + ((x−μ)/γ)²)", eq,
        {"a": a, "mu": mu, "gamma": g}, y, yhat, [feat], k=3,
    )


def _fit_inverse_square(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    """y = a/x² + b — Newton's gravity, Coulomb's law (1/r²)."""
    mask = (np.abs(x) > 1e-9) & np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 5:
        return None
    inv2 = 1.0 / (x[mask] ** 2)
    a, b = np.polyfit(inv2, y[mask], 1)
    yhat = np.where(np.abs(x) > 1e-9,
                     a / np.where(np.abs(x) > 1e-9, x ** 2, 1) + b, np.nan)
    eq = f"y = {a:.4g}/{feat}² + {b:.4g}"
    r = _build_result(
        "inverse_square", "y = a/x² + b", eq,
        {"a": float(a), "b": float(b)}, y, yhat, [feat], k=2,
    )
    return _attach_known(r, "Inverse-square law (gravity / Coulomb / radiation)")


def _fit_sqrt(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    """y = a·√x + b — diffusion distance, Brownian motion RMS."""
    mask = (x >= 0) & np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 5:
        return None
    sqx = np.sqrt(x[mask])
    a, b = np.polyfit(sqx, y[mask], 1)
    yhat = np.where(x >= 0,
                     a * np.sqrt(np.where(x >= 0, x, 0)) + b, np.nan)
    eq = f"y = {a:.4g}·√{feat} + {b:.4g}"
    r = _build_result(
        "sqrt", "y = a·√x + b",
        eq, {"a": float(a), "b": float(b)},
        y, yhat, [feat], k=2,
    )
    return _attach_known(r, "Square-root law (Brownian / diffusive)")


def _fit_arrhenius(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    """y = a · exp(−b/x) — Arrhenius equation (chemical kinetics, thermal activation)."""
    if not _HAS_SCIPY:
        return None
    mask = (x > 1e-9) & (y > 0) & np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 8:
        return None
    a0 = float(np.nanmax(y)) or 1.0
    b0 = float(np.nanmean(x[mask])) or 1.0
    popt, ok = _safe_curve_fit(
        lambda xx, a, b: a * np.exp(-b / np.maximum(xx, 1e-9)),
        x[mask], y[mask], p0=[a0, b0],
    )
    if not ok:
        return None
    a, b = (float(p) for p in popt)
    yhat = np.where(x > 1e-9,
                     a * np.exp(-b / np.where(x > 1e-9, x, 1)), np.nan)
    eq = f"y = {a:.4g}·exp(−{b:.4g}/{feat})"
    r = _build_result(
        "arrhenius", "y = a·exp(−b/x)", eq,
        {"a": a, "b": b}, y, yhat, [feat], k=2,
    )
    return _attach_known(r, "Arrhenius form (chemical kinetics / thermal activation)")


def _fit_weibull(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    """y = 1 − exp(−(x/λ)^k) — reliability / time-to-failure / wind speeds."""
    if not _HAS_SCIPY:
        return None
    mask = (x >= 0) & np.isfinite(x) & np.isfinite(y) & (y >= 0)
    if mask.sum() < 8:
        return None
    lam0 = float(np.nanmedian(x[mask])) or 1.0
    k0 = 1.5
    popt, ok = _safe_curve_fit(
        lambda xx, lam, k: 1.0 - np.exp(-((xx / max(lam, 1e-9)) ** k)),
        x[mask], y[mask], p0=[lam0, k0],
        bounds=([1e-9, 0.1], [np.inf, 20.0]),
    )
    if not ok:
        return None
    lam, k = (float(p) for p in popt)
    yhat = np.where(x >= 0, 1.0 - np.exp(-((np.where(x >= 0, x, 0) / max(lam, 1e-9)) ** k)), np.nan)
    eq = f"y = 1 − exp(−({feat}/{lam:.3g})^{k:.3g})"
    r = _build_result(
        "weibull", "y = 1 − exp(−(x/λ)^k)", eq,
        {"lambda": lam, "k": k}, y, yhat, [feat], k=2,
    )
    return _attach_known(r, "Weibull form (reliability / failure / wind speeds)")


def _fit_gompertz(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    """y = a · exp(−b · exp(−c·x)) — Gompertz growth (tumours, demography, mortality)."""
    if not _HAS_SCIPY:
        return None
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 10:
        return None
    a0 = float(np.nanmax(y)) or 1.0
    popt, ok = _safe_curve_fit(
        lambda xx, a, b, c: a * np.exp(-b * np.exp(-c * xx)),
        x[mask], y[mask], p0=[a0, 1.0, 0.1],
        bounds=([0, 0, 0], [np.inf, np.inf, np.inf]),
    )
    if not ok:
        return None
    a, b, c = (float(p) for p in popt)
    yhat = a * np.exp(-b * np.exp(-c * x))
    eq = f"y = {a:.4g}·exp(−{b:.3g}·exp(−{c:.3g}·{feat}))"
    r = _build_result(
        "gompertz", "y = a·exp(−b·exp(−c·x))", eq,
        {"a": a, "b": b, "c": c}, y, yhat, [feat], k=3,
    )
    return _attach_known(r, "Gompertz form (tumour growth / demography)")


def _fit_double_exponential(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    """y = a·exp(b·x) + c·exp(d·x) — biexponential decay (multi-compartment kinetics)."""
    if not _HAS_SCIPY:
        return None
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 12:
        return None
    span = float(np.nanmax(x) - np.nanmin(x)) or 1.0
    xnorm = (x[mask] - np.nanmin(x)) / span
    a0 = float(np.nanmax(y)) - float(np.nanmin(y)) or 1.0
    popt, ok = _safe_curve_fit(
        lambda xx, a, b, c, d: a * np.exp(b * xx) + c * np.exp(d * xx),
        xnorm, y[mask], p0=[a0 * 0.5, -1.0, a0 * 0.5, -0.1],
        maxfev=4000,
    )
    if not ok:
        return None
    a, b, c, d = (float(p) for p in popt)
    xnorm_full = (x - np.nanmin(x)) / span
    yhat = a * np.exp(b * xnorm_full) + c * np.exp(d * xnorm_full)
    eq = f"y = {a:.3g}·exp({b:.3g}·t) + {c:.3g}·exp({d:.3g}·t)  (t = ({feat}−min)/span)"
    return _build_result(
        "double_exponential", "y = a·exp(b·x) + c·exp(d·x)", eq,
        {"a": a, "b": b, "c": c, "d": d, "span": span}, y, yhat, [feat], k=4,
    )


def _fit_polynomial_4(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    """y = a·x⁴ + b·x³ + c·x² + d·x + e — quartic. Recovers Stefan-Boltzmann directly."""
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 8:
        return None
    a, b, c, d, e = np.polyfit(x[mask], y[mask], 4)
    yhat = a*x**4 + b*x**3 + c*x**2 + d*x + e
    eq = f"y = {a:.3g}·{feat}⁴ + {b:.3g}·{feat}³ + {c:.3g}·{feat}² + {d:.3g}·{feat} + {e:.3g}"
    r = _build_result(
        "polynomial_4", "y = a·x⁴ + b·x³ + c·x² + d·x + e", eq,
        {"a": float(a), "b": float(b), "c": float(c), "d": float(d), "e": float(e)},
        y, yhat, [feat], k=5,
    )
    # If the cubic+quartic dominate and lower terms are tiny → quartic-only.
    spread = max(abs(a), abs(b), abs(c), abs(d), abs(e)) or 1.0
    if abs(a) / spread > 0.5 and abs(b) / spread < 0.05 and abs(c) / spread < 0.05:
        return _attach_known(r, "Quartic-dominant form (Stefan-Boltzmann T⁴-like)")
    return r


def _fit_sine(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    """y = a·sin(b·x + c) + d — pure sinusoid (tides, diurnal cycle, AC voltage).

    Uses an FFT-derived initial frequency so the fit converges even when the
    signal contains many cycles (which would defeat the naive 2π/span guess).
    """
    if not _HAS_SCIPY or x.size < 12:
        return None
    a0 = (float(np.nanmax(y)) - float(np.nanmin(y))) / 2.0 or 1.0
    d0 = float(np.nanmean(y))
    # FFT-based frequency seed: pick the dominant non-DC component.
    try:
        dt = float(np.median(np.diff(x))) or 1.0
        y_centered = y - np.nanmean(y)
        spectrum = np.abs(np.fft.rfft(y_centered))
        if spectrum.size > 1:
            peak = int(np.argmax(spectrum[1:]) + 1)
            f_hz = peak / (x.size * dt)
            b0 = 2.0 * np.pi * f_hz if f_hz > 0 else 2.0 * np.pi / max(float(np.nanmax(x) - np.nanmin(x)), 1e-9)
        else:
            b0 = 2.0 * np.pi / max(float(np.nanmax(x) - np.nanmin(x)), 1e-9)
    except Exception:
        b0 = 2.0 * np.pi / max(float(np.nanmax(x) - np.nanmin(x)), 1e-9)
    popt, ok = _safe_curve_fit(
        lambda xx, a, b, c, d: a * np.sin(b * xx + c) + d,
        x, y, p0=[a0, b0, 0.0, d0],
    )
    if not ok:
        return None
    a, b, c, d = (float(p) for p in popt)
    yhat = a * np.sin(b * x + c) + d
    eq = f"y = {a:.3g}·sin({b:.3g}·{feat} + {c:.3g}) + {d:.3g}"
    return _build_result(
        "sine", "y = a·sin(b·x + c) + d", eq,
        {"a": a, "b": b, "c": c, "d": d}, y, yhat, [feat], k=4,
    )


def _fit_damped_sine(x: np.ndarray, y: np.ndarray, feat: str) -> Optional[FitResult]:
    """y = a·exp(−c·x)·sin(b·x + φ) + d — damped oscillation (RLC ringing)."""
    if not _HAS_SCIPY or x.size < 14:
        return None
    a0 = (float(np.nanmax(y)) - float(np.nanmin(y))) / 2.0 or 1.0
    d0 = float(np.nanmean(y))
    span = float(np.nanmax(x) - np.nanmin(x)) or 1.0
    b0 = 2.0 * np.pi * 3 / span
    c0 = 1.0 / span
    popt, ok = _safe_curve_fit(
        lambda xx, a, b, c, phi, d: a * np.exp(-c * xx) * np.sin(b * xx + phi) + d,
        x, y, p0=[a0, b0, c0, 0.0, d0],
        maxfev=4000,
    )
    if not ok:
        return None
    a, b, c, phi, d = (float(p) for p in popt)
    yhat = a * np.exp(-c * x) * np.sin(b * x + phi) + d
    eq = f"y = {a:.3g}·exp(−{c:.3g}·{feat})·sin({b:.3g}·{feat} + {phi:.3g}) + {d:.3g}"
    r = _build_result(
        "damped_sine", "y = a·exp(−c·x)·sin(b·x + φ) + d", eq,
        {"a": a, "b": b, "c": c, "phi": phi, "d": d}, y, yhat, [feat], k=5,
    )
    return _attach_known(r, "Damped-sinusoid form (RLC / mechanical ringing)")


def _fit_polynomial_quad_2var(x1: np.ndarray, x2: np.ndarray, y: np.ndarray,
                               feat1: str, feat2: str) -> Optional[FitResult]:
    """Full quadratic surface: y = a + b·x₁ + c·x₂ + d·x₁² + e·x₂² + f·x₁·x₂."""
    if x1.size < 12:
        return None
    X = np.column_stack([np.ones_like(x1, dtype=float), x1, x2,
                          x1 * x1, x2 * x2, x1 * x2])
    mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
    if mask.sum() < 12:
        return None
    beta, *_ = np.linalg.lstsq(X[mask], y[mask], rcond=None)
    a, b, c, d, e, f = (float(v) for v in beta)
    yhat = X @ beta
    eq = (f"y = {a:.3g} + {b:.3g}·{feat1} + {c:.3g}·{feat2} + "
          f"{d:.3g}·{feat1}² + {e:.3g}·{feat2}² + {f:.3g}·{feat1}·{feat2}")
    return _build_result(
        "polynomial_quad_2var", "y = a + b·x₁ + c·x₂ + d·x₁² + e·x₂² + f·x₁·x₂", eq,
        {"a": a, "b": b, "c": c, "d": d, "e": e, "f": f},
        y, yhat, [feat1, feat2], k=6,
    )


# ----------------------------------------------------------------------
# Feature selection
# ----------------------------------------------------------------------

def _top_correlated_features(df, target_col: str, max_features: int = 2) -> List[str]:
    try:
        cols = [c for c in df.columns if c != target_col and
                np.issubdtype(df[c].dtype, np.number)]
        ys = df[target_col].astype(float).values
        scores: List[Tuple[str, float]] = []
        for c in cols:
            xs = df[c].astype(float).values
            mask = np.isfinite(xs) & np.isfinite(ys)
            if mask.sum() < 10:
                continue
            r = np.corrcoef(xs[mask], ys[mask])[0, 1]
            if not math.isfinite(r):
                continue
            scores.append((c, abs(r)))
        scores.sort(key=lambda t: t[1], reverse=True)
        return [c for c, _ in scores[:max_features]]
    except Exception:
        return []


# ----------------------------------------------------------------------
# Public entry
# ----------------------------------------------------------------------

def discover_relation(df, target_col: str,
                      feature_cols: Optional[List[str]] = None,
                      max_features: int = 2,
                      top_k: int = 5) -> List[Dict[str, Any]]:
    """Run all curated forms and return the top-k by AIC.

    Each result is a dict (FitResult.to_dict).
    """
    if target_col not in df.columns:
        return []

    y = df[target_col].astype(float).values
    if feature_cols is None:
        feature_cols = _top_correlated_features(df, target_col, max_features)
    feature_cols = list(feature_cols)[:max_features]

    results: List[FitResult] = []

    # 1-feature fits
    if feature_cols:
        feat1 = feature_cols[0]
        x1 = df[feat1].astype(float).values
        for fitter in (
            # Original 9 forms
            _fit_linear, _fit_quadratic, _fit_cubic,
            _fit_power_law, _fit_exponential, _fit_log,
            _fit_inverse, _fit_sigmoid, _fit_saturation,
            # v2.0 additions (11 more) — peaks, kinetics, oscillation
            _fit_gaussian, _fit_lorentzian, _fit_inverse_square,
            _fit_sqrt, _fit_arrhenius, _fit_weibull, _fit_gompertz,
            _fit_double_exponential, _fit_polynomial_4,
            _fit_sine, _fit_damped_sine,
        ):
            try:
                r = fitter(x1, y, feat1)
                if r is not None and math.isfinite(r.aic):
                    results.append(r)
            except Exception:
                pass

    # 2-feature fits
    if len(feature_cols) >= 2:
        feat1, feat2 = feature_cols[0], feature_cols[1]
        x1 = df[feat1].astype(float).values
        x2 = df[feat2].astype(float).values
        for fitter in (
            _fit_allometric_2var, _fit_bilinear, _fit_ratio_law,
            _fit_polynomial_quad_2var,    # v2.0 addition: full response surface
        ):
            try:
                r = fitter(x1, x2, y, feat1, feat2)
                if r is not None and math.isfinite(r.aic):
                    results.append(r)
            except Exception:
                pass

    results.sort(key=lambda r: r.aic)
    return [r.to_dict() for r in results[:top_k]]
