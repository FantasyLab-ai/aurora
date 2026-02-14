# fantasyai/aurora/system_id.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Any, Optional, Tuple

import numpy as np
from scipy.optimize import curve_fit


@dataclass
class FitResult:
    success: bool
    params: Dict[str, float]
    covariance: Optional[np.ndarray]
    diagnostics: Dict[str, Any]


# -------------------------------
# Basic model families
# -------------------------------

def _poly_model(x, *coeffs):
    """Polynomial: a0 + a1 x + a2 x^2 + ..."""
    y = np.zeros_like(x, dtype=float)
    for i, c in enumerate(coeffs):
        y = y + c * x**i
    return y


def _logistic_model(x, L, k, x0):
    """
    Logistic curve: L / (1 + exp(-k (x - x0)))
    """
    return L / (1.0 + np.exp(-k * (x - x0)))


# -------------------------------
# Public APIs
# -------------------------------

def fit_polynomial(
    x: np.ndarray,
    y: np.ndarray,
    degree: int = 2,
) -> FitResult:
    """
    Fit a polynomial of given degree to data (x, y).
    """
    p0 = np.ones(degree + 1, dtype=float)
    try:
        coeffs, cov = curve_fit(_poly_model, x, y, p0=p0)
        success = True
    except Exception as e:
        coeffs = np.zeros(degree + 1, dtype=float)
        cov = None
        success = False
        diag_err = str(e)
    else:
        diag_err = None

    params = {f"a{i}": float(c) for i, c in enumerate(coeffs)}
    diagnostics = {
        "degree": degree,
        "error": diag_err,
    }

    return FitResult(
        success=success,
        params=params,
        covariance=cov,
        diagnostics=diagnostics,
    )


def fit_logistic(
    x: np.ndarray,
    y: np.ndarray,
    *,
    p0: Tuple[float, float, float] = (1.0, 1.0, 0.0),
) -> FitResult:
    """
    Fit a logistic curve to data (x, y).
    """
    try:
        params, cov = curve_fit(_logistic_model, x, y, p0=p0, maxfev=20000)
        success = True
        L, k, x0 = params
        param_dict = {"L": float(L), "k": float(k), "x0": float(x0)}
        diag_err = None
    except Exception as e:
        success = False
        cov = None
        param_dict = {}
        diag_err = str(e)

    diagnostics = {
        "initial_guess": list(p0),
        "error": diag_err,
    }

    return FitResult(
        success=success,
        params=param_dict,
        covariance=cov,
        diagnostics=diagnostics,
    )
