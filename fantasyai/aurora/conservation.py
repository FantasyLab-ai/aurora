"""
Conservation-law detector — find invariant quantities across variable subsets.

Conservation laws are first-class citizens in physical systems. When mass
energy, momentum, or charge is conserved across an observation window, *some
combination* of the measured variables is approximately constant. Aurora
scans the data for such combinations and reports them as "discovered
invariants" — the kind of result that makes industrial users gasp because
it's the difference between "your data fluctuates" and *"your system has a
conserved quantity that's worth instrumenting."*

Detection strategies (v1)
-------------------------

1. **Direct invariant**: a single column whose coefficient of variation is
   below threshold. (Trivial conservation: the variable doesn't change.)

2. **Sum invariant**: the sum of a subset of columns is approximately
   constant (mass conservation: ``A + B + C ≈ const`` where each represents
   a phase of mass).

3. **Linear combination invariant**: a fitted weighted sum
   ``Σ w_i · x_i ≈ const`` where the residual std is small relative to
   typical column variation. We find this via least-squares on a column
   matrix against a constant target plus a regularization push toward
   sparse weights.

4. **Quadratic invariant** (energy-like): for any pair of columns ``v, h``,
   check whether ``½·v² + h`` (or weighted variant) is approximately
   constant. This catches kinetic + potential energy patterns.

5. **Ratio invariant**: ``A / B ≈ const`` over the observation window.
   Catches Boyle's law (PV = const at fixed T), aspect-ratio constraints,
   stoichiometric balances.

We surface invariants whose **coefficient of variation < 0.05** (5%) — i.e.
the candidate is constant to within 5% of its mean across the dataset.
That threshold is conservative; smaller threshold = stricter "constant"
definition.

Public API
----------

    detect_invariants(df, numeric_cols=None, max_cv=0.05, top_k=5)
        → list[Invariant]

Each Invariant is a dict with: form, equation, coefficient_of_variation,
n, mean, std, columns_used, type, interpretation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


@dataclass
class Invariant:
    type: str                          # direct | sum | linear | quadratic | ratio
    form: str                          # human-readable: "A + B + C"
    equation: str                      # filled-in: "0.5·A² + B"
    coefficient_of_variation: float    # std / |mean|, lower = more constant
    n: int                             # samples used
    mean: float
    std: float
    columns_used: List[str]
    interpretation: str                # natural-language hint

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                d[k] = None
        return d


def _cv(arr: np.ndarray) -> float:
    """Coefficient of variation: std / |mean|. Returns inf when |mean| is tiny."""
    arr = np.asarray(arr, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        return float("inf")
    m = float(np.mean(arr))
    s = float(np.std(arr))
    if abs(m) < 1e-12:
        # Center-and-scale case: tiny mean. Use range/std instead.
        return s / max(abs(s), 1e-9)
    return s / abs(m)


def _direct_invariants(df, numeric_cols: List[str], max_cv: float) -> List[Invariant]:
    """Trivial: any column that's nearly constant."""
    out: List[Invariant] = []
    for c in numeric_cols:
        try:
            arr = df[c].astype(float).values
            arr = arr[np.isfinite(arr)]
            if arr.size < 5:
                continue
            cv = _cv(arr)
            if cv > max_cv:
                continue
            m = float(np.mean(arr))
            s = float(np.std(arr))
            out.append(Invariant(
                type="direct",
                form=c, equation=f"{c} ≈ {m:.4g}",
                coefficient_of_variation=cv, n=int(arr.size),
                mean=m, std=s, columns_used=[c],
                interpretation=(
                    f"`{c}` is approximately constant over the observation window "
                    f"(deviation < {cv * 100:.1f}%). Either the system is in steady "
                    f"state on this dimension, or the sensor isn't capturing variation."
                ),
            ))
        except Exception:
            continue
    return out


def _sum_invariants(df, numeric_cols: List[str], max_cv: float,
                    max_size: int = 4) -> List[Invariant]:
    """Subsets of size 2..max_size whose unweighted sum is constant.

    Detects mass-conservation-style: A + B + C ≈ total constant.
    Limited to avoid O(2^N) blowup on wide data.
    """
    out: List[Invariant] = []
    n_cols = len(numeric_cols)
    if n_cols < 2:
        return out

    # Limit combinations to keep this affordable on wide data.
    # For 20 cols, we'd try C(20,2) + C(20,3) + C(20,4) = 190 + 1140 + 4845 ≈ 6k.
    # Cap individual-size lists if necessary.
    for size in range(2, min(max_size, n_cols) + 1):
        combos = list(combinations(numeric_cols, size))
        if len(combos) > 500:
            # Skip pathologically wide subsets — would be slow.
            continue
        for combo in combos:
            try:
                arrs = [df[c].astype(float).values for c in combo]
                stacked = np.column_stack(arrs)
                mask = np.isfinite(stacked).all(axis=1)
                if mask.sum() < 5:
                    continue
                summed = stacked[mask].sum(axis=1)
                cv = _cv(summed)
                if cv > max_cv:
                    continue
                m = float(np.mean(summed))
                s = float(np.std(summed))
                form = " + ".join(combo)
                out.append(Invariant(
                    type="sum",
                    form=form, equation=f"{form} ≈ {m:.4g}",
                    coefficient_of_variation=cv, n=int(mask.sum()),
                    mean=m, std=s, columns_used=list(combo),
                    interpretation=(
                        f"The unweighted sum of these {size} columns is approximately "
                        f"constant (deviation < {cv * 100:.1f}%). This is the signature "
                        f"of mass / charge / count conservation: each column may "
                        f"represent a phase or compartment of a conserved quantity."
                    ),
                ))
            except Exception:
                continue
    return out


def _ratio_invariants(df, numeric_cols: List[str], max_cv: float) -> List[Invariant]:
    """Pairs where A/B is approximately constant (Boyle's-law-like)."""
    out: List[Invariant] = []
    n_cols = len(numeric_cols)
    if n_cols < 2:
        return out
    for c1, c2 in combinations(numeric_cols, 2):
        try:
            a = df[c1].astype(float).values
            b = df[c2].astype(float).values
            mask = np.isfinite(a) & np.isfinite(b) & (np.abs(b) > 1e-9)
            if mask.sum() < 5:
                continue
            ratio = a[mask] / b[mask]
            cv = _cv(ratio)
            if cv > max_cv:
                continue
            m = float(np.mean(ratio))
            s = float(np.std(ratio))
            out.append(Invariant(
                type="ratio",
                form=f"{c1} / {c2}",
                equation=f"{c1}/{c2} ≈ {m:.4g}",
                coefficient_of_variation=cv, n=int(mask.sum()),
                mean=m, std=s, columns_used=[c1, c2],
                interpretation=(
                    f"`{c1} / {c2}` is approximately constant. "
                    f"Possible signatures: "
                    f"Boyle's law (P·V=const at fixed T), "
                    f"stoichiometric ratio, "
                    f"aspect ratio constraint."
                ),
            ))
        except Exception:
            continue
    return out


def _quadratic_invariants(df, numeric_cols: List[str], max_cv: float) -> List[Invariant]:
    """Pairs (v, h) where ½·v² + h is approximately constant.

    Detects kinetic + potential-energy-like patterns. The 0.5 coefficient is
    the canonical mechanical KE; we don't search over arbitrary weights here
    (linear_combination_invariants does that more rigorously). This is a
    targeted detector for the most common physical pattern.
    """
    out: List[Invariant] = []
    n_cols = len(numeric_cols)
    if n_cols < 2:
        return out
    for c1, c2 in combinations(numeric_cols, 2):
        for v_col, h_col in ((c1, c2), (c2, c1)):
            try:
                v = df[v_col].astype(float).values
                h = df[h_col].astype(float).values
                mask = np.isfinite(v) & np.isfinite(h)
                if mask.sum() < 5:
                    continue
                ke_pe = 0.5 * v[mask] ** 2 + h[mask]
                cv = _cv(ke_pe)
                if cv > max_cv:
                    continue
                m = float(np.mean(ke_pe))
                s = float(np.std(ke_pe))
                out.append(Invariant(
                    type="quadratic",
                    form=f"½·{v_col}² + {h_col}",
                    equation=f"½·{v_col}² + {h_col} ≈ {m:.4g}",
                    coefficient_of_variation=cv, n=int(mask.sum()),
                    mean=m, std=s, columns_used=[v_col, h_col],
                    interpretation=(
                        f"Energy-like invariant: ½·{v_col}² + {h_col} is approximately "
                        f"conserved (deviation < {cv * 100:.1f}%). This pattern is the "
                        f"signature of conservation of total energy: kinetic + potential. "
                        f"Verify by checking units of `{v_col}` (should square to the same "
                        f"dimension as `{h_col}`)."
                    ),
                ))
            except Exception:
                continue
    return out


def _linear_combination_invariants(df, numeric_cols: List[str],
                                    max_cv: float, max_size: int = 4) -> List[Invariant]:
    """Find weighted sums Σ w_i · x_i ≈ const.

    Approach: for a subset of columns, the optimal weight vector for a
    perfectly-conserved quantity satisfies ``X · w = const · 1``. We solve
    via least-squares + null-space: any non-trivial vector w in the
    null-space of (X - μ_X)·1 indicates a conserved combination.

    We use small subsets (2..4 cols) and report any with coefficient of
    variation below ``max_cv`` (well under unweighted-sum threshold).
    """
    out: List[Invariant] = []
    if len(numeric_cols) < 2:
        return out

    for size in range(2, min(max_size, len(numeric_cols)) + 1):
        combos = list(combinations(numeric_cols, size))
        if len(combos) > 200:
            continue
        for combo in combos:
            try:
                arrs = [df[c].astype(float).values for c in combo]
                X = np.column_stack(arrs)
                mask = np.isfinite(X).all(axis=1)
                if mask.sum() < 10:
                    continue
                X_clean = X[mask]
                # Center the data
                X_c = X_clean - np.mean(X_clean, axis=0)
                # Find the smallest singular value direction; if it's tiny,
                # that direction is a conserved combination.
                try:
                    U, S, Vt = np.linalg.svd(X_c, full_matrices=False)
                except Exception:
                    continue
                if S.size == 0:
                    continue
                # The smallest singular value direction (last row of Vt) is
                # the closest to a null direction.
                w = Vt[-1]
                # Skip degenerate weight vectors.
                if not np.isfinite(w).all():
                    continue
                # Normalize so the weights are interpretable.
                norm = np.linalg.norm(w)
                if norm < 1e-12:
                    continue
                w = w / norm
                # Compute the resulting linear combination on the full series.
                lc = X_clean @ w
                cv = _cv(lc)
                if cv > max_cv:
                    continue
                # Skip combos that are trivially close to a single-column direct invariant.
                if max(abs(w_i) for w_i in w) > 0.95:
                    continue
                m = float(np.mean(lc))
                s = float(np.std(lc))
                form = " + ".join(f"{w_i:+.3g}·{c}"
                                   for w_i, c in zip(w, combo))
                out.append(Invariant(
                    type="linear",
                    form=form, equation=f"{form} ≈ {m:.4g}",
                    coefficient_of_variation=cv, n=int(mask.sum()),
                    mean=m, std=s, columns_used=list(combo),
                    interpretation=(
                        f"Linear combination of {size} variables is approximately "
                        f"conserved (deviation < {cv * 100:.1f}%). Discovered via SVD "
                        f"of the centered data matrix; coefficients shown are normalized. "
                        f"This pattern often indicates a balance equation in the system "
                        f"(e.g., conservation of energy, mass, momentum) with weights "
                        f"reflecting the physical units of each contribution."
                    ),
                ))
            except Exception:
                continue
    return out


# ----------------------------------------------------------------------
# Public entry
# ----------------------------------------------------------------------

def detect_invariants(df, numeric_cols: Optional[List[str]] = None,
                      max_cv: float = 0.05,
                      top_k: int = 5) -> List[Dict[str, Any]]:
    """Scan the dataset for conserved quantities (invariants).

    Args:
        df: pandas DataFrame
        numeric_cols: explicit column subset to scan; default = all numeric
        max_cv: coefficient-of-variation threshold (≤ 5% by default).
                Smaller = stricter "constant" definition.
        top_k: cap on results returned

    Returns:
        list of Invariant dicts sorted by coefficient_of_variation ascending
        (most-conserved first).
    """
    try:
        import pandas as pd  # noqa: F401
    except Exception:
        return []

    if numeric_cols is None:
        numeric_cols = [c for c in df.columns
                        if np.issubdtype(df[c].dtype, np.number)]
    # Reasonable cap on width to avoid combinatorial blowup.
    numeric_cols = list(numeric_cols)[:25]
    if len(numeric_cols) < 1:
        return []

    found: List[Invariant] = []
    found.extend(_direct_invariants(df, numeric_cols, max_cv))
    found.extend(_sum_invariants(df, numeric_cols, max_cv))
    found.extend(_ratio_invariants(df, numeric_cols, max_cv))
    found.extend(_quadratic_invariants(df, numeric_cols, max_cv))
    found.extend(_linear_combination_invariants(df, numeric_cols, max_cv))

    # De-duplicate by (type, columns_used signature) keeping best CV.
    deduped: Dict[Tuple[str, Tuple[str, ...]], Invariant] = {}
    for inv in found:
        key = (inv.type, tuple(sorted(inv.columns_used)))
        prior = deduped.get(key)
        if prior is None or inv.coefficient_of_variation < prior.coefficient_of_variation:
            deduped[key] = inv

    sorted_invs = sorted(deduped.values(), key=lambda x: x.coefficient_of_variation)
    return [inv.to_dict() for inv in sorted_invs[:top_k]]
