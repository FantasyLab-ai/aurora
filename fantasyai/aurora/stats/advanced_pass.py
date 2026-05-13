"""
Advanced-methods orchestrator (Brief 3).

Runs Brief-3 methods in order of cost when their shape requirements are
met, emitting findings the synthesis engine + frontend can pick up.

Shape gating:
  * SINDy            — needs ≥ 2 numeric columns + ≥ 50 evenly-sampled rows.
  * HMM              — needs ≥ 1 numeric column + ≥ 50 rows.
  * Mutual info      — needs ≥ 2 numeric columns.
  * Granger          — needs ≥ 2 numeric columns + ≥ 30 rows.
  * Wavelet          — needs ≥ 1 numeric column + ≥ 32 evenly-sampled rows.
  * Lomb-Scargle     — needs an unevenly-sampled time axis.
  * GP               — needs ≥ 1 numeric col + ≥ 5 rows; cheap.
  * Topology         — needs ≥ 2 numeric cols + ≥ 4 rows.
  * Power analysis   — runs unconditionally (lightweight).
  * Compositional    — only when is_compositional() returns True.

Each method's output goes onto its own ``state.<name>`` block AND emits
a finding so the synthesis matcher can route to a method-specific
template.

Glass-box: every step records what it did (and what it skipped, with
reason) under ``state.advanced_methods``.
"""
from __future__ import annotations

import logging
import math
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from typing import Any, Callable, Dict, List, Optional

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


_log = logging.getLogger("aurora.advanced_pass")


# Pre-launch v1.1: every method in apply_advanced_pass is capped at
# this many seconds. The cap is the never-hang floor — combined with
# per-method sampling for HMM/wavelet, the worst-case full-pipeline
# time stays bounded even on pathological 17-column inputs.
PER_METHOD_TIMEOUT_SECONDS = 90


def _exc_skip(label: str, e: BaseException) -> Dict[str, Any]:
    """Workstream-2 fix S-4: every except in this module logs
    ``traceback.format_exc()`` to ``aurora.advanced_pass`` and returns
    a clean structured skip. Brief constraint: no silent failures."""
    _log.error("advanced-pass step %s raised %s\n%s",
                 label, type(e).__name__, traceback.format_exc())
    return {"applied": False,
             "reason": f"{label}_exception: {type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Pre-launch v1.1 — stratified time-preserving sampler
# ---------------------------------------------------------------------------

def _stratified_time_preserving_sample(
    arr: "np.ndarray",
    *,
    target_n: int,
    outlier_threshold: float = 4.0,
) -> "np.ndarray":
    """Return sorted indices that down-sample a 1-D series to ~``target_n``
    rows while preserving the time axis and retaining extreme outliers.

    Properties:

    * **Time-axis preserving.** Returned indices are sorted ascending,
      so the sampled series remains in temporal order. Critical for
      sequential methods (HMM, wavelet CWT) — random uniform sampling
      would destroy the sequential structure.
    * **Outlier-preserving.** Indices where the input's robust z-score
      ``|z| >= outlier_threshold`` are always kept, so legitimate
      signal is not suppressed by the sampling. Self-contained: the
      robust z is computed on ``arr`` itself, with no dependency on
      downstream anomaly detection or the order in which advanced-pass
      methods run.
    * **Deterministic.** Given the same ``arr``, returns the same
      indices. No random draws — even-interval picks via
      ``np.linspace`` fill whatever budget remains after outliers.
    * **Honest on edge cases.** If ``len(arr) <= target_n`` the full
      ``arange`` is returned (no-op). If the outlier set already
      exceeds ``target_n`` the function returns ALL outliers in order
      — never dropping legitimate signal, even at the cost of running
      the downstream method on more than ``target_n`` rows.

    Used by ``_maybe_hmm`` (target_n=2000) and ``_maybe_wavelet``
    (target_n=3000) to cap input length T before invoking the
    expensive inner loops. Replaces the prior "run on full series"
    behaviour that produced 7.6-min HMM and 47-s wavelet runtimes at
    T=100K (per the v1.0 benchmark harness).
    """
    if np is None:
        raise RuntimeError("numpy required for stratified sampling")
    n = int(arr.shape[0])
    if n <= target_n:
        return np.arange(n)
    # Robust z on the input series itself. MAD = 0 means the series is
    # constant — no outliers possible; even-interval pick covers the
    # whole budget.
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    if mad <= 0.0:
        outlier_idx = np.array([], dtype=int)
    else:
        z = np.abs(arr - med) / (1.4826 * mad)
        outlier_idx = np.where(z >= outlier_threshold)[0]
    if len(outlier_idx) >= target_n:
        # All outliers exceed the budget. Keep them all — the brief
        # explicitly says do not suppress real signal. The downstream
        # method runs on more than target_n rows but that's the honest
        # call; runtime degrades gracefully rather than dropping data.
        return outlier_idx.copy()
    n_remaining = target_n - len(outlier_idx)
    # Even-interval picks across the FULL [0, n-1] index range.
    even_idx = np.linspace(0, n - 1, num=n_remaining, dtype=int)
    # Union + dedupe + sort ascending so time order is preserved.
    return np.unique(np.concatenate([outlier_idx, even_idx]))


# ---------------------------------------------------------------------------
# Per-method runners
# ---------------------------------------------------------------------------

def _maybe_sindy(state: Dict[str, Any]) -> Dict[str, Any]:
    matrix = state.get("dataset_matrix")
    if matrix is None:
        return {"applied": False, "reason": "no_dataset_matrix"}
    if np is None:
        return {"applied": False, "reason": "numpy_missing"}
    # Workstream-2 fix S-1: SINDy is methodologically meaningful ONLY for
    # time-series data. Cross-sectional rows have no temporal ordering;
    # running SINDy on row-index pseudo-time is a numerical-stability
    # disaster (blows up LAPACK with degenerate / NaN-rich features —
    # the maintainer's "DLASCLS parameter 4 had an illegal value" on
    # diabetic_data was exactly this). Skip honestly.
    structure = state.get("structure") or {}
    has_time_axis = bool(structure.get("time_axis")
                            or structure.get("has_time_axis")
                            or structure.get("time_col"))
    if not has_time_axis:
        return {"applied": False,
                "reason": "cross_sectional_no_time_axis"}
    arr = np.asarray(matrix, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 2 or arr.shape[0] < 50:
        return {"applied": False,
                "reason": f"shape_too_small: {arr.shape}"}
    cols = state.get("dataset_matrix_columns") or [
        f"x{i}" for i in range(arr.shape[1])
    ]
    dt = state.get("dataset_dt", 1.0)
    try:
        from .sindy import sindy_discover
        res = sindy_discover(arr, dt, column_names=cols, poly_degree=2,
                                threshold=0.1)
    except Exception as e:
        return _exc_skip("sindy", e)
    if not res.get("available"):
        return {"applied": False,
                "reason": res.get("skipped_reason", "sindy_unavailable")}
    state["sindy"] = res
    findings = state.setdefault("findings", [])
    eq_str = "; ".join(res["equations"])
    findings.append({
        "severity": "ok",
        "confidence": min(0.95, 1.0 - min(1.0, res["rmse_total"])),
        "title": f"Discovered governing equations ({res['n_active_terms']} active terms)",
        "description": eq_str[:200],
        "method": "sindy",
        "actions": ["VIEW EVIDENCE", "EXPLAIN"],
        "kind_hint": "discovered_dynamics",
        "headline": True,
        "headline_kind": "sindy",
        "symbolic_equations": res["equations"],
        "rmse": res["rmse_total"],
        "sparsity": res["sparsity"],
    })
    return {"applied": True, "n_active_terms": res["n_active_terms"],
            "rmse": res["rmse_total"]}


_HMM_MAX_ROWS = 2000


def _maybe_hmm(state: Dict[str, Any]) -> Dict[str, Any]:
    series = state.get("primary_series")
    if series is None:
        return {"applied": False, "reason": "no_primary_series"}
    if np is None:
        return {"applied": False, "reason": "numpy_missing"}
    arr = np.asarray(series, dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.shape[0] < 50:
        return {"applied": False,
                "reason": f"too_few_obs: {arr.shape[0]}"}

    # ---- v1.1: cap input length at 2000 rows. HMM is univariate
    # Gaussian (1-D y); cost is O(T·K²) per EM iter, with 50 iters × 3
    # K values via ``fit_hmm_select_k``. At T=100K this took 7.6 min on
    # the v1.0 bench. Sampling to T=2000 keeps it under ~5s on the same
    # hardware. Time-axis order is preserved (HMM is sequential);
    # extreme outliers (|robust_z| >= 4) are always retained so the
    # model still sees legitimate signal, even off the even-interval
    # grid. Metadata fields (``n_train_used``, ``n_train_total``,
    # ``downsampled``, ``sample_strategy``) are stamped on the result
    # so the synthesis layer and frontend can disclose honestly.
    n_train_total = int(arr.shape[0])
    if n_train_total > _HMM_MAX_ROWS:
        try:
            idx = _stratified_time_preserving_sample(
                arr, target_n=_HMM_MAX_ROWS, outlier_threshold=4.0,
            )
            arr = arr[idx]
            downsampled = True
            sample_strategy = "stratified_time_preserving"
        except Exception as e:
            # Sampling MUST NOT crash HMM — fall through to running on
            # the full series and let the timeout backstop (Fix 3)
            # catch the runtime. Log per Workstream-2 contract.
            _log.error("hmm input sampling raised %s\n%s",
                          type(e).__name__, traceback.format_exc())
            downsampled = False
            sample_strategy = "sampling_failed_used_full"
    else:
        downsampled = False
        sample_strategy = "full"
    n_train_used = int(arr.shape[0])

    try:
        from .hmm import fit_hmm_select_k
        res = fit_hmm_select_k(arr, k_range=(2, 3, 4), seed=42)
    except Exception as e:
        return _exc_skip("hmm", e)
    # Stamp sampling metadata on every result (available OR not) so
    # downstream readers see consistent fields.
    res["n_train_used"]    = n_train_used
    res["n_train_total"]   = n_train_total
    res["downsampled"]     = downsampled
    res["sample_strategy"] = sample_strategy
    # ``dataset_rows_full`` is the row count of the ORIGINAL CSV before
    # state_builder's pipeline-level 5000-row cap (set in
    # ``state_builder._maybe_attach_brief2_inputs``). When set, the
    # synthesis disclosure uses it to chain the honest reporting:
    # "HMM analyzed 2000 of the 5000-row input window, from a 101,766-row
    # dataset." When absent (small CSVs), it equals n_train_total.
    drf = state.get("dataset_rows_full")
    if isinstance(drf, int) and drf > 0:
        res["dataset_rows_full"] = drf
    if not res.get("available"):
        state["hmm"] = res
        return {"applied": False,
                "reason": res.get("skipped_reason", "hmm_unavailable")}
    state["hmm"] = res
    findings = state.setdefault("findings", [])
    cur_state = res["current_state"]
    cur_prob = res["current_state_posterior"][cur_state]
    findings.append({
        "severity": "info",
        "confidence": float(cur_prob),
        "title": (f"Latent regimes: {res['best_K']} hidden states; "
                   f"currently in state {cur_state}"),
        "description": (
            f"BIC-selected K={res['best_K']}. Means: "
            f"{[round(m, 3) for m in res['means_sorted']]}. "
            f"Expected dwell: "
            f"{[round(d, 1) for d in res['expected_dwell_time']]}."
        ),
        "method": "hmm_baum_welch",
        "actions": ["VIEW EVIDENCE"],
        "kind_hint": "latent_regime_structure",
        "n_states": res["best_K"],
    })
    return {"applied": True, "best_K": res["best_K"]}


def _maybe_mutual_info(state: Dict[str, Any]) -> Dict[str, Any]:
    matrix = state.get("dataset_matrix")
    if matrix is None or np is None:
        return {"applied": False, "reason": "no_dataset_matrix"}
    arr = np.asarray(matrix, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 2 or arr.shape[0] < 20:
        return {"applied": False,
                "reason": f"shape_unsuitable: {arr.shape}"}
    cols = state.get("dataset_matrix_columns")
    try:
        from .mutual_info import mi_matrix
        # KSG estimator is slow — bin if n_obs > 500 or n_features > 8.
        estimator = "ksg" if arr.shape[0] <= 500 and arr.shape[1] <= 8 \
                     else "binning"
        res = mi_matrix(arr, columns=cols, estimator=estimator)
    except Exception as e:
        return _exc_skip("mi", e)
    if not res.get("available"):
        return {"applied": False,
                "reason": res.get("skipped_reason")}
    state["mutual_info"] = res
    if res["nonlinear_pairs"]:
        findings = state.setdefault("findings", [])
        for pair in res["nonlinear_pairs"][:3]:
            findings.append({
                "severity": "warn",
                "confidence": 0.7,
                "title": (f"Nonlinear dependence: {pair['col_i']} ↔ "
                           f"{pair['col_j']} (MI={pair['mi']:.3f}, "
                           f"|r|={abs(pair['pearson_r']):.2f})"),
                "description": (
                    f"Mutual information exceeds the Gaussian baseline "
                    f"by {pair['excess_above_linear']:.3f} nats while "
                    f"linear correlation is weak — relationship is "
                    f"nonlinear."
                ),
                "method": "mutual_information_ksg",
                "actions": ["VIEW EVIDENCE", "EXPLAIN"],
                "kind_hint": "nonlinear_dependence",
            })
    return {"applied": True,
             "n_nonlinear_pairs": len(res["nonlinear_pairs"])}


def _maybe_granger(state: Dict[str, Any]) -> Dict[str, Any]:
    matrix = state.get("dataset_matrix")
    if matrix is None or np is None:
        return {"applied": False, "reason": "no_dataset_matrix"}
    arr = np.asarray(matrix, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 2 or arr.shape[0] < 30:
        return {"applied": False,
                "reason": f"shape_unsuitable: {arr.shape}"}
    cols = (state.get("dataset_matrix_columns")
             or [f"x{i}" for i in range(arr.shape[1])])
    # Run pairwise Granger on the first 4 columns to keep cost bounded.
    try:
        from .granger import bivariate_granger
        results: List[Dict[str, Any]] = []
        for i in range(min(4, arr.shape[1])):
            for j in range(i + 1, min(4, arr.shape[1])):
                xi = arr[:, i]; xj = arr[:, j]
                mask = ~(np.isnan(xi) | np.isnan(xj))
                if mask.sum() < 30:
                    continue
                gc = bivariate_granger(xi[mask], xj[mask],
                                          max_lag=3,
                                          var_x_label=cols[i],
                                          var_y_label=cols[j])
                if gc.get("available"):
                    results.append({"i": i, "j": j,
                                     "col_i": cols[i], "col_j": cols[j],
                                     **gc})
    except Exception as e:
        return _exc_skip("granger", e)
    if not results:
        return {"applied": False, "reason": "no_pairs_succeeded"}
    state["granger_causality"] = {"pairs": results}
    # Emit a finding for every directional/feedback verdict.
    findings = state.setdefault("findings", [])
    for r in results:
        if r["verdict"] in ("no_evidence",):
            continue
        findings.append({
            "severity": "info",
            "confidence": 0.7,
            "title": (f"Granger causality: {r['col_i']} ↔ {r['col_j']} → "
                       f"{r['verdict'].replace('_', ' ')}"),
            "description": (
                f"Forward p={r['forward']['p']:.3g}, reverse "
                f"p={r['reverse']['p']:.3g} at lag {r['max_lag']}."
            ),
            "method": "granger",
            "actions": ["VIEW EVIDENCE"],
            "kind_hint": "causal_direction",
        })
    return {"applied": True, "n_pairs": len(results)}


_WAVELET_MAX_ROWS = 3000


def _maybe_wavelet(state: Dict[str, Any]) -> Dict[str, Any]:
    series = state.get("primary_series")
    if series is None or np is None:
        return {"applied": False, "reason": "no_primary_series"}
    arr = np.asarray(series, dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.shape[0] < 32:
        return {"applied": False, "reason": "too_few_obs"}

    # ---- v1.1: cap input length at 3000 rows. Wavelet is univariate
    # Morlet CWT on the single primary series — cost is roughly
    # O(scales × T) convolutions. At T=100K the v1.0 bench measured
    # 47s/run. Sampling to T=3000 keeps it under ~1s. Same stratified
    # time-preserving + extreme-outlier-keeping sampler as HMM (Fix 1).
    n_train_total = int(arr.shape[0])
    if n_train_total > _WAVELET_MAX_ROWS:
        try:
            idx = _stratified_time_preserving_sample(
                arr, target_n=_WAVELET_MAX_ROWS, outlier_threshold=4.0,
            )
            arr = arr[idx]
            downsampled = True
            sample_strategy = "stratified_time_preserving"
        except Exception as e:
            _log.error("wavelet input sampling raised %s\n%s",
                          type(e).__name__, traceback.format_exc())
            downsampled = False
            sample_strategy = "sampling_failed_used_full"
    else:
        downsampled = False
        sample_strategy = "full"
    n_train_used = int(arr.shape[0])

    try:
        from .spectral import detect_nonstationary_period
        res = detect_nonstationary_period(arr, dt=state.get("dataset_dt", 1.0))
    except Exception as e:
        return _exc_skip("wavelet", e)
    # Trim heavy arrays (per-t dominant-period sequence) — keep only the
    # summary fields the frontend / synthesis layer reads.
    summary: Dict[str, Any] = {
        k: v for k, v in res.items() if k != "dominant_period_per_t"
    }
    # Stamp sampling metadata uniformly (available OR not).
    summary["n_train_used"]    = n_train_used
    summary["n_train_total"]   = n_train_total
    summary["downsampled"]     = downsampled
    summary["sample_strategy"] = sample_strategy
    drf = state.get("dataset_rows_full")
    if isinstance(drf, int) and drf > 0:
        summary["dataset_rows_full"] = drf
    if not res.get("available"):
        state["wavelet_period"] = summary
        return {"applied": False,
                "reason": res.get("skipped_reason")}
    state["wavelet_period"] = summary
    if res["verdict"] in ("drifting", "switching"):
        findings = state.setdefault("findings", [])
        findings.append({
            "severity": "info",
            "confidence": 0.65,
            "title": f"Non-stationary oscillation: period {res['verdict']}",
            "description": (
                f"Period changes from ≈{res['period_first_quartile']:.3g} "
                f"to ≈{res['period_last_quartile']:.3g} time units "
                f"across the series (relative drift "
                f"{res['relative_drift']:.2f})."
            ),
            "method": "morlet_cwt",
            "actions": ["VIEW EVIDENCE"],
            "kind_hint": "nonstationary_oscillation",
        })
    return {"applied": True, "verdict": res["verdict"]}


def _maybe_lomb_scargle(state: Dict[str, Any]) -> Dict[str, Any]:
    times = state.get("primary_times")
    values = state.get("primary_series")
    if times is None or values is None or np is None:
        return {"applied": False, "reason": "no_irregular_time_data"}
    t = np.asarray(times, dtype=float); y = np.asarray(values, dtype=float)
    if t.shape[0] < 16:
        return {"applied": False, "reason": "too_few_obs"}
    diffs = np.diff(np.sort(t))
    if diffs.shape[0] == 0:
        return {"applied": False, "reason": "single_time"}
    cv = float(np.std(diffs) / max(1e-9, np.mean(diffs)))
    if cv < 0.05:  # essentially evenly sampled
        return {"applied": False, "reason": "evenly_sampled"}
    try:
        from .spectral import lomb_scargle
        res = lomb_scargle(t, y, n_freq=200)
    except Exception as e:
        return _exc_skip("lomb_scargle", e)
    if not res.get("available"):
        return {"applied": False,
                "reason": res.get("skipped_reason")}
    state["lomb_scargle"] = res
    if res["false_alarm_probability"] < 0.05:
        findings = state.setdefault("findings", [])
        findings.append({
            "severity": "info",
            "confidence": 1.0 - res["false_alarm_probability"],
            "title": (f"Period detected (uneven sampling): "
                       f"P ≈ {res['dominant_period']:.3g}"),
            "description": (
                f"Lomb-Scargle false-alarm probability "
                f"{res['false_alarm_probability']:.4g}; max power "
                f"{res['max_power']:.3g}."
            ),
            "method": "lomb_scargle",
            "actions": ["VIEW EVIDENCE"],
            "kind_hint": "uneven_sampling_period",
        })
    return {"applied": True, "fap": res["false_alarm_probability"]}


def _maybe_gp(state: Dict[str, Any]) -> Dict[str, Any]:
    times = state.get("primary_times")
    values = state.get("primary_series")
    if times is None or values is None or np is None:
        return {"applied": False, "reason": "no_time_series"}
    t = np.asarray(times, dtype=float); y = np.asarray(values, dtype=float)
    mask = ~(np.isnan(t) | np.isnan(y))
    t = t[mask]; y = y[mask]
    if t.shape[0] < 8:
        return {"applied": False, "reason": "fewer_than_8_obs"}
    # Workstream-2 fix S-5: GP cubic-time scaling makes >500 training
    # points painful. Previously we skipped at n>500 entirely, which
    # meant AUTO mode on any large dataset (the maintainer's case)
    # never saw a GP fit. Now we deterministically subsample to
    # 400 evenly-spaced points and stamp the original n on the result
    # so reviewers can audit. Skip only for n<8.
    n_total = int(t.shape[0])
    n_train_used = n_total
    if n_total > 500:
        # Even-spaced index pick — preserves time-axis coverage rather
        # than random sampling, which is more defensible for a smooth-
        # function estimator.
        idx = np.linspace(0, n_total - 1, 400).round().astype(int)
        idx = np.unique(idx)
        t = t[idx]
        y = y[idx]
        n_train_used = int(t.shape[0])
    try:
        from .gaussian_process import fit_gp, predict_gp
        fit = fit_gp(t, y, kernel="rbf")
        if not fit.get("available"):
            return {"applied": False,
                    "reason": fit.get("skipped_reason")}
        # Predict on a small grid that extends past the training window
        # so we can quote out-of-range uncertainty growth.
        t_min, t_max = float(t.min()), float(t.max())
        pad = (t_max - t_min) * 0.2
        grid = np.linspace(t_min, t_max + pad, 30)
        pred = predict_gp(fit, grid)
    except Exception as e:
        return _exc_skip("gp", e)
    if not pred.get("available"):
        return {"applied": False, "reason": "predict_failed"}
    sig_in_range = float(np.mean([s for s, x in zip(pred["sigma"], grid)
                                       if x <= t_max]))
    sig_out_range = float(np.mean([s for s, x in zip(pred["sigma"], grid)
                                        if x > t_max])) \
                     if any(x > t_max for x in grid) else sig_in_range
    state["gaussian_process"] = {
        "available": True,
        "kernel": fit["kernel"],
        "log_marginal_likelihood": fit["log_marginal_likelihood"],
        "hyperparameters": fit["hyperparameters"],
        "sigma_in_range": sig_in_range,
        "sigma_out_of_range": sig_out_range,
        "extrapolation_uncertainty_ratio": (
            sig_out_range / max(1e-12, sig_in_range)
        ),
        "n_train": fit["n_obs"],
        # S-5 audit fields: subsampling is honest about what was used
        # vs what was on offer.
        "n_train_used": n_train_used,
        "n_train_total": n_total,
        "downsampled": n_train_used < n_total,
    }
    return {"applied": True,
             "kernel": fit["kernel"],
             "lml": fit["log_marginal_likelihood"],
             "n_train_used": n_train_used,
             "n_train_total": n_total}


def _maybe_topology(state: Dict[str, Any]) -> Dict[str, Any]:
    matrix = state.get("dataset_matrix")
    if matrix is None or np is None:
        return {"applied": False, "reason": "no_dataset_matrix"}
    arr = np.asarray(matrix, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 2 or arr.shape[0] < 4:
        return {"applied": False, "reason": "shape_unsuitable"}
    # Cap rows for tractability.
    if arr.shape[0] > 200:
        # Subsample.
        rng = np.random.default_rng(42)
        idx = rng.choice(arr.shape[0], 200, replace=False)
        arr = arr[idx]
    try:
        from .topology import persistent_homology, topological_summary
        ph = persistent_homology(arr, max_edges=4000)
        if not ph.get("available"):
            return {"applied": False,
                    "reason": ph.get("skipped_reason")}
        summary = topological_summary(ph)
    except Exception as e:
        return _exc_skip("topology", e)
    # Trim heavy arrays (full H₀ diagram + every H₁ birth) — the frontend
    # / synthesis layer only needs the counts + verdict.
    light = {k: v for k, v in ph.items()
              if k not in ("h0_diagram", "h1_births")}
    light["h1_births_count"] = len(ph.get("h1_births") or [])
    state["topology"] = {**light, "summary": summary}
    if summary.get("verdict") not in (None, "single_blob"):
        findings = state.setdefault("findings", [])
        findings.append({
            "severity": "info",
            "confidence": 0.6,
            "title": f"Topological structure: {summary['verdict'].replace('_', ' ')}",
            "description": (
                f"H₀ significant components: {summary['h0_significant_count']}; "
                f"H₁ births: {summary['n_h1_births']}."
            ),
            "method": "vietoris_rips_h0_h1",
            "actions": ["VIEW EVIDENCE"],
            "kind_hint": "topological_structure",
        })
    return {"applied": True, "verdict": summary.get("verdict")}


def _maybe_compositional(state: Dict[str, Any]) -> Dict[str, Any]:
    matrix = state.get("dataset_matrix")
    if matrix is None or np is None:
        return {"applied": False, "reason": "no_dataset_matrix"}
    arr = np.asarray(matrix, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 2:
        return {"applied": False, "reason": "shape_unsuitable"}
    try:
        from .compositional import is_compositional, compositional_correlation
        det = is_compositional(arr)
        if not det.get("compositional"):
            state["compositional"] = {"compositional": False,
                                          "reason": det.get("reason")}
            return {"applied": False, "reason": det.get("reason")}
        cc = compositional_correlation(arr)
    except Exception as e:
        return _exc_skip("compositional", e)
    state["compositional"] = {
        "compositional": True,
        "row_sum_cv": det["row_sum_cv"],
        "n_substantial_corr_changes": cc.get("n_substantial", 0),
        "substantial_changes": cc.get("substantial_differences", []),
        "method": "clr_pearson",
    }
    if cc.get("n_substantial", 0) > 0:
        findings = state.setdefault("findings", [])
        findings.append({
            "severity": "info",
            "confidence": 0.85,
            "title": (f"Compositional data: "
                       f"{cc['n_substantial']} correlation"
                       f"{'s' if cc['n_substantial'] != 1 else ''} "
                       f"changed substantially in CLR space"),
            "description": (
                "Naive Pearson correlation on proportions is biased "
                "by the closure constraint. CLR transformation removes "
                "the bias; the changes flagged here are the spurious "
                "correlations in the raw view."
            ),
            "method": "compositional_clr",
            "actions": ["VIEW EVIDENCE", "EXPLAIN"],
            "kind_hint": "compositional_correction",
        })
    return {"applied": True}


def _maybe_power(state: Dict[str, Any]) -> Dict[str, Any]:
    """Power analysis depends on the causal layer.

    Workstream-2 fix S-7 — honest framing: this method is NOT
    "always-applicable". It only fires when the causal pass produced
    an effect with summary statistics, which itself only fires on
    time-series data with a treatment-like discontinuity. Renamed the
    skip reasons accordingly so users see the dependency rather than
    a vague "no_effect_size_to_target".

    Looks for the largest causal Cohen's d in state and reports the
    sample size needed to detect a half-as-large effect at 80% power.
    Triggers an experimental_design_recommendation finding.
    """
    cw = state.get("causal") or {}
    items = cw.get("items") or []
    target = None
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("effect_size"), dict):
            target = it
            break
    if not target:
        return {"applied": False, "reason": "awaiting_causal_effect"}
    d = target["effect_size"].get("value")
    if d is None or abs(d) < 0.05:
        return {"applied": False,
                "reason": "causal_effect_too_small_to_target"}
    smaller = abs(d) / 2.0
    try:
        from .power_analysis import power_t_test
        pa = power_t_test(smaller)
    except Exception as e:
        return _exc_skip("power", e)
    state["power_analysis"] = pa
    findings = state.setdefault("findings", [])
    findings.append({
        "severity": "info",
        "confidence": 0.9,
        "title": f"Experimental design: detect d={smaller:.2f} → n={pa['n_per_group']} per group",
        "description": (
            f"To detect an effect HALF the size of the observed "
            f"d={d:.2f} (medium-to-small), you'd need ~{pa['n_per_group']} "
            f"observations per group at {pa['power']*100:.0f}% power, "
            f"α={pa['alpha']}."
        ),
        "method": "power_t_test",
        "actions": [],
        "kind_hint": "experimental_design_recommendation",
    })
    return {"applied": True, "n_per_group": pa["n_per_group"]}


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def _maybe_cluster_stability(state: Dict[str, Any]) -> Dict[str, Any]:
    """Wave C — k-means + bootstrap stability across k=2..6.

    Runs on any numeric matrix with ≥ 30 rows. Reports the best k by
    silhouette × stability composite, plus a verdict (strong / moderate
    / weak) so the synthesis layer can say 'no clear clustering' when
    that's the honest finding.
    """
    matrix = state.get("dataset_matrix")
    if matrix is None or np is None:
        return {"applied": False, "reason": "no_dataset_matrix"}
    arr = np.asarray(matrix, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 2 or arr.shape[0] < 30:
        return {"applied": False, "reason": f"shape: {arr.shape}"}
    # Subsample for tractability — k-means + bootstrap stability scales
    # poorly above ~500 rows.
    if arr.shape[0] > 400:
        rng = np.random.default_rng(42)
        idx = rng.choice(arr.shape[0], 400, replace=False)
        arr = arr[idx]
    try:
        from .clustering import select_best_k
        res = select_best_k(arr, k_range=(2, 3, 4, 5, 6), seed=42)
    except Exception as e:
        return _exc_skip("cluster", e)
    if not res.get("available"):
        return {"applied": False,
                "reason": res.get("skipped_reason", "select_best_k_failed")}
    state["cluster_stability"] = res
    if res["best_verdict"] in ("strong", "moderate"):
        findings = state.setdefault("findings", [])
        findings.append({
            "severity": "info",
            "confidence": float(res["best_stability"]),
            "title": (f"Cluster structure: k={res['best_k']} "
                       f"({res['best_verdict']} stability)"),
            "description": (
                f"Bootstrap stability {res['best_stability']:.2f}; "
                f"silhouette {res['best_silhouette']:.2f}."
            ),
            "method": "kmeans_bootstrap_stability",
            "actions": ["VIEW EVIDENCE"],
            "kind_hint": "cluster_structure",
        })
    return {"applied": True, "best_k": res["best_k"],
             "verdict": res["best_verdict"]}


def _maybe_bayesian(state: Dict[str, Any]) -> Dict[str, Any]:
    """Wave G — Normal-Inverse-Gamma posterior on the primary series.

    Always-applicable: any primary numeric series with ≥ 5 obs gets a
    full Bayesian posterior on (μ, σ²) with conjugate priors. Replaces
    the point estimate with credible intervals reviewers can audit.
    """
    series = state.get("primary_series")
    if series is None or np is None:
        return {"applied": False, "reason": "no_primary_series"}
    arr = np.asarray(series, dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.shape[0] < 5:
        return {"applied": False, "reason": "fewer_than_5_obs"}
    try:
        from .bayesian import normal_inverse_gamma
        # Cap at 2000 obs — NIG is closed-form but we're sampling 5000
        # MC draws which gets noisy on absurd n.
        if arr.shape[0] > 2000:
            arr = arr[-2000:]
        res = normal_inverse_gamma(arr.tolist())
    except Exception as e:
        return _exc_skip("bayes", e)
    if not res.get("available"):
        return {"applied": False, "reason": res.get("skipped_reason")}
    state["bayesian_target"] = res
    return {"applied": True,
             "mu_mean": res["posterior_mu_mean"],
             "sigma2_mean": res["posterior_sigma2_mean"]}


def _maybe_panel(state: Dict[str, Any]) -> Dict[str, Any]:
    """Wave C — two-way fixed-effects variance decomposition.

    Triggers when the dataset has a low-cardinality string column
    (entity ID) alongside a time axis + a numeric outcome. The runner's
    structure contract gives us the column roles.
    """
    structure = state.get("structure") or {}
    cols = structure.get("columns") or []
    time_col = structure.get("time_col")
    if not time_col:
        return {"applied": False, "reason": "no_time_axis"}
    if np is None:
        return {"applied": False, "reason": "numpy_missing"}
    # Look for a categorical column with 2-50 unique values (an entity id).
    matrix_cols = state.get("dataset_matrix_columns") or []
    matrix = state.get("dataset_matrix")
    primary_name = state.get("primary_series_name")
    if matrix is None or not primary_name:
        return {"applied": False, "reason": "no_matrix_or_primary"}
    # In the runner-emitted columns list we look for "cat" / "categorical"
    # roles. We don't have entity IDs in the dataset_matrix (it's
    # numeric-only). So this gate is intentionally narrow — only fires
    # on explicit panel data.
    entity_col_name = None
    for c in cols:
        if not isinstance(c, dict):
            continue
        kind = (c.get("kind") or c.get("role") or "").lower()
        unique_est = c.get("unique_est")
        if kind in ("cat", "categorical", "string") and unique_est:
            try:
                u = int(unique_est)
                if 2 <= u <= 50:
                    entity_col_name = c.get("name")
                    break
            except Exception:
                continue
    if not entity_col_name:
        return {"applied": False, "reason": "no_entity_column_detected"}
    # Re-read the source CSV for the entity + time + value triple — the
    # numeric matrix doesn't carry the categorical entity column.
    src = state.get("dataset", {}) or {}
    csv_path = (src.get("path") or
                  (state.get("run_meta") or {}).get("dataset_path"))
    if not csv_path:
        return {"applied": False, "reason": "no_csv_path_for_entity_lookup"}
    try:
        import pandas as pd
        from pathlib import Path
        if not Path(csv_path).exists():
            return {"applied": False, "reason": "csv_path_missing"}
        df = pd.read_csv(csv_path, low_memory=False)
        if entity_col_name not in df.columns or primary_name not in df.columns:
            return {"applied": False,
                    "reason": "entity_or_target_col_missing"}
        # Cap rows.
        if len(df) > 5000:
            df = df.iloc[-5000:].reset_index(drop=True)
        ents = df[entity_col_name].astype(str).tolist()
        times = df[time_col].astype(str).tolist() if time_col in df.columns \
                 else list(range(len(df)))
        vals = pd.to_numeric(df[primary_name], errors="coerce").tolist()
        from .panel import two_way_fixed_effects
        res = two_way_fixed_effects(ents, times, vals, bootstrap=False)
    except Exception as e:
        return _exc_skip("panel", e)
    if not res.get("available"):
        return {"applied": False, "reason": res.get("skipped_reason")}
    state["panel_decomposition"] = res
    findings = state.setdefault("findings", [])
    findings.append({
        "severity": "info",
        "confidence": 0.7,
        "title": (f"Panel decomposition: {res['dominant']} variance "
                   f"dominates ({res['n_entities']} entities × "
                   f"{res['n_times']} times)"),
        "description": (
            f"Variance shares — entity: {res['share_entity']:.0%}, "
            f"time: {res['share_time']:.0%}, "
            f"residual: {res['share_residual']:.0%}."
        ),
        "method": "two_way_fixed_effects",
        "actions": ["VIEW EVIDENCE"],
        "kind_hint": "panel_decomposition",
    })
    return {"applied": True, "dominant": res["dominant"],
             "n_entities": res["n_entities"]}


def _maybe_survival(state: Dict[str, Any]) -> Dict[str, Any]:
    """Wave D — Kaplan-Meier + Cox PH when the columns look like
    survival data (a duration column + an event-indicator column)."""
    structure = state.get("structure") or {}
    cols = structure.get("columns") or []
    duration_col = None
    event_col = None
    for c in cols:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").lower()
        if not duration_col and any(k in name for k in
                                       ("duration", "lifetime", "survival_time",
                                        "time_to_event", "tte", "follow_up")):
            duration_col = c.get("name")
        if not event_col and any(k in name for k in
                                    ("event", "death", "failed", "churn",
                                     "censor", "status")):
            event_col = c.get("name")
    if not (duration_col and event_col):
        return {"applied": False,
                "reason": "no_duration_event_columns_detected"}
    src = state.get("dataset", {}) or {}
    csv_path = (src.get("path") or
                  (state.get("run_meta") or {}).get("dataset_path"))
    if not csv_path:
        return {"applied": False, "reason": "no_csv_path"}
    try:
        import pandas as pd
        from pathlib import Path
        if not Path(csv_path).exists():
            return {"applied": False, "reason": "csv_path_missing"}
        df = pd.read_csv(csv_path, low_memory=False)
        if len(df) > 5000:
            df = df.iloc[-5000:].reset_index(drop=True)
        d = pd.to_numeric(df[duration_col], errors="coerce").dropna().tolist()
        e = pd.to_numeric(df[event_col], errors="coerce").fillna(0).astype(int).tolist()
        if len(d) < 10 or len(e) < 10:
            return {"applied": False, "reason": "fewer_than_10_obs"}
        from .survival import kaplan_meier
        res = kaplan_meier(d, e[:len(d)])
    except Exception as e:
        return _exc_skip("survival", e)
    if not res.get("available"):
        return {"applied": False, "reason": res.get("skipped_reason")}
    # Trim heavy arrays for API.
    light = {k: v for k, v in res.items()
             if k not in ("times", "n_at_risk", "n_events",
                            "survival", "ci_low", "ci_high")}
    state["survival"] = light
    findings = state.setdefault("findings", [])
    median = res.get("median_survival")
    findings.append({
        "severity": "info",
        "confidence": 0.7,
        "title": (f"Survival curve: {res['n_subjects']} subjects, "
                   f"{res['n_events_total']} events"
                   + (f" · median = {median:.2f}" if median else "")),
        "description": (
            f"Kaplan-Meier estimate with Greenwood-formula CIs. "
            f"{res['n_censored']} subjects censored."
        ),
        "method": "kaplan_meier",
        "actions": ["VIEW EVIDENCE"],
        "kind_hint": "survival_curve",
    })
    return {"applied": True, "n_subjects": res["n_subjects"],
             "median_survival": median}


def _maybe_spatial(state: Dict[str, Any]) -> Dict[str, Any]:
    """Wave E — Moran's I + Geary's C when lat/lon columns are
    detected by name."""
    structure = state.get("structure") or {}
    cols = structure.get("columns") or []
    lat_col = None; lon_col = None
    for c in cols:
        if not isinstance(c, dict): continue
        name = (c.get("name") or "").lower()
        if not lat_col and any(k in name for k in
                                  ("latitude", "lat", "y_coord", "northing")):
            lat_col = c.get("name")
        if not lon_col and any(k in name for k in
                                  ("longitude", "lon", "lng", "x_coord", "easting")):
            lon_col = c.get("name")
    if not (lat_col and lon_col):
        return {"applied": False, "reason": "no_lat_lon_columns_detected"}
    primary_name = state.get("primary_series_name")
    if not primary_name:
        return {"applied": False, "reason": "no_primary_value_to_test"}
    src = state.get("dataset", {}) or {}
    csv_path = (src.get("path") or
                  (state.get("run_meta") or {}).get("dataset_path"))
    if not csv_path:
        return {"applied": False, "reason": "no_csv_path"}
    try:
        import pandas as pd
        from pathlib import Path
        if not Path(csv_path).exists():
            return {"applied": False, "reason": "csv_path_missing"}
        df = pd.read_csv(csv_path, low_memory=False)
        if len(df) > 500:
            df = df.iloc[-500:].reset_index(drop=True)  # Moran's I is O(n²)
        lats = pd.to_numeric(df[lat_col], errors="coerce")
        lons = pd.to_numeric(df[lon_col], errors="coerce")
        vals = pd.to_numeric(df[primary_name], errors="coerce")
        mask = lats.notna() & lons.notna() & vals.notna()
        if mask.sum() < 10:
            return {"applied": False, "reason": "fewer_than_10_geo_obs"}
        coords = list(zip(lats[mask].tolist(), lons[mask].tolist()))
        from .spatial import morans_i
        res = morans_i(vals[mask].tolist(), coords, knn=8,
                          n_permutations=200, seed=42)
    except Exception as e:
        return _exc_skip("spatial", e)
    if not res.get("available"):
        return {"applied": False, "reason": res.get("skipped_reason")}
    state["spatial_autocorrelation"] = res
    if res["interpretation"] != "random":
        findings = state.setdefault("findings", [])
        findings.append({
            "severity": "info",
            "confidence": 1.0 - res["p_value"],
            "title": (f"Spatial autocorrelation: {res['interpretation']} "
                       f"(Moran's I = {res['morans_i']:.3f})"),
            "description": (
                f"Permutation test p={res['p_value']:.3g} on "
                f"{res['n']} points — values cluster geographically "
                f"more than chance would predict."
            ),
            "method": "morans_i",
            "actions": ["VIEW EVIDENCE"],
            "kind_hint": "spatial_autocorrelation",
        })
    return {"applied": True, "verdict": res["interpretation"]}


def _maybe_network(state: Dict[str, Any]) -> Dict[str, Any]:
    """Wave E — degree / betweenness / Louvain when source+target columns
    are detected (graph-shaped data)."""
    structure = state.get("structure") or {}
    cols = structure.get("columns") or []
    src_col = None; tgt_col = None
    for c in cols:
        if not isinstance(c, dict): continue
        name = (c.get("name") or "").lower()
        if not src_col and any(k in name for k in
                                  ("source", "from", "src", "origin", "u_node")):
            src_col = c.get("name")
        if not tgt_col and any(k in name for k in
                                  ("target", "to", "dst", "destination", "v_node")):
            tgt_col = c.get("name")
    if not (src_col and tgt_col):
        return {"applied": False, "reason": "no_edge_columns_detected"}
    src_csv = state.get("dataset", {}) or {}
    csv_path = (src_csv.get("path") or
                  (state.get("run_meta") or {}).get("dataset_path"))
    if not csv_path:
        return {"applied": False, "reason": "no_csv_path"}
    try:
        import pandas as pd
        from pathlib import Path
        if not Path(csv_path).exists():
            return {"applied": False, "reason": "csv_path_missing"}
        df = pd.read_csv(csv_path, low_memory=False)
        if len(df) > 2000:
            df = df.iloc[:2000].reset_index(drop=True)
        nodes = sorted(set(df[src_col].astype(str).tolist()
                            + df[tgt_col].astype(str).tolist()))
        node_idx = {n: i for i, n in enumerate(nodes)}
        edges = [(node_idx[str(s)], node_idx[str(t)])
                  for s, t in zip(df[src_col], df[tgt_col])
                  if str(s) in node_idx and str(t) in node_idx]
        n = len(nodes)
        if n < 4 or not edges:
            return {"applied": False, "reason": "graph_too_small"}
        from .network import (degree_centrality, betweenness_centrality,
                                 louvain_communities)
        deg = degree_centrality(edges, n)
        bet = betweenness_centrality(edges, n)
        louv = louvain_communities(edges, n)
    except Exception as e:
        return _exc_skip("network", e)
    state["network_analysis"] = {
        "available": True,
        "n_nodes": n,
        "n_edges": len(edges),
        "max_degree_node": int(deg.get("max_node", 0)),
        "max_degree_value": float(deg.get("max_value", 0.0)),
        "max_betweenness_node": int(bet.get("max_node", 0)),
        "max_betweenness_value": float(bet.get("max_value", 0.0)),
        "n_communities": louv.get("n_communities"),
        "modularity": louv.get("modularity"),
        "community_verdict": louv.get("verdict"),
        "method": "centrality_louvain",
    }
    if louv.get("available") and louv.get("verdict") in ("well_separated",
                                                            "moderate"):
        findings = state.setdefault("findings", [])
        findings.append({
            "severity": "info",
            "confidence": 0.7,
            "title": (f"Network has {louv['n_communities']} communities "
                       f"(modularity {louv['modularity']:.2f}, "
                       f"{louv['verdict']})"),
            "description": (
                f"Louvain community detection on {n} nodes / "
                f"{len(edges)} edges. Most-central node: idx "
                f"{bet.get('max_node')} (betweenness "
                f"{bet.get('max_value', 0):.3f})."
            ),
            "method": "louvain",
            "actions": ["VIEW EVIDENCE"],
            "kind_hint": "graph_structure",
        })
    return {"applied": True, "n_nodes": n, "n_edges": len(edges)}


def _maybe_did(state: Dict[str, Any]) -> Dict[str, Any]:
    """Wave F — Difference-in-Differences when panel data carries a
    binary treatment indicator."""
    panel = state.get("panel_decomposition")
    if not panel or not panel.get("available"):
        return {"applied": False,
                "reason": "panel_decomposition_not_available"}
    structure = state.get("structure") or {}
    cols = structure.get("columns") or []
    treat_col = None
    for c in cols:
        if not isinstance(c, dict): continue
        name = (c.get("name") or "").lower()
        if any(k in name for k in ("treatment", "treated", "policy",
                                     "intervention", "post_treat")):
            treat_col = c.get("name")
            break
    if not treat_col:
        return {"applied": False, "reason": "no_treatment_column_detected"}
    src = state.get("dataset", {}) or {}
    csv_path = (src.get("path") or
                  (state.get("run_meta") or {}).get("dataset_path"))
    if not csv_path:
        return {"applied": False, "reason": "no_csv_path"}
    structure_meta = state.get("structure") or {}
    time_col = structure_meta.get("time_col")
    primary_name = state.get("primary_series_name")
    # Find the entity column (same heuristic as panel detector).
    entity_col_name = None
    for c in cols:
        if not isinstance(c, dict): continue
        kind = (c.get("kind") or c.get("role") or "").lower()
        unique_est = c.get("unique_est")
        if kind in ("cat", "categorical", "string") and unique_est:
            try:
                u = int(unique_est)
                if 2 <= u <= 50:
                    entity_col_name = c.get("name"); break
            except Exception:
                continue
    if not (entity_col_name and time_col and primary_name):
        return {"applied": False, "reason": "missing_panel_identifiers"}
    try:
        import pandas as pd
        from pathlib import Path
        if not Path(csv_path).exists():
            return {"applied": False, "reason": "csv_path_missing"}
        df = pd.read_csv(csv_path, low_memory=False)
        if len(df) > 5000:
            df = df.iloc[-5000:].reset_index(drop=True)
        ents = df[entity_col_name].astype(str).tolist()
        times = df[time_col].astype(str).tolist()
        d = pd.to_numeric(df[treat_col], errors="coerce").fillna(0).astype(int).tolist()
        y = pd.to_numeric(df[primary_name], errors="coerce").tolist()
        from .did import difference_in_differences
        res = difference_in_differences(ents, times, d, y,
                                          n_bootstrap=200, seed=42)
    except Exception as e:
        return _exc_skip("did", e)
    if not res.get("available"):
        return {"applied": False, "reason": res.get("skipped_reason")}
    state["did"] = res
    findings = state.setdefault("findings", [])
    findings.append({
        "severity": "info",
        "confidence": 1.0 - min(1.0, res["p_value"]),
        "title": (f"Treatment effect (DiD): {res['treatment_effect']:.3f} "
                   f"(95% CI [{res['ci_low']:.3f}, {res['ci_high']:.3f}])"),
        "description": (
            f"Two-way fixed-effects DiD on {res['n_obs']} observations, "
            f"{res['n_entities']} entities. p={res['p_value']:.3g}. "
            f"Parallel-trends pre-test: "
            f"{'PASSED' if not res['parallel_trends'].get('violated') else 'VIOLATED'}."
        ),
        "method": "did",
        "actions": ["VIEW EVIDENCE", "EXPLAIN"],
        "kind_hint": "treatment_effect_did",
    })
    return {"applied": True, "treatment_effect": res["treatment_effect"]}


_STEPS = [
    ("compositional",      _maybe_compositional),  # cheap, runs first
    ("mutual_info",        _maybe_mutual_info),
    ("granger",            _maybe_granger),
    ("hmm",                _maybe_hmm),
    ("wavelet",            _maybe_wavelet),
    ("lomb_scargle",       _maybe_lomb_scargle),
    ("gaussian_process",   _maybe_gp),
    ("sindy",              _maybe_sindy),
    ("topology",           _maybe_topology),
    ("power_analysis",     _maybe_power),
    # Brief 2 methods that previously had no orchestrator hook.
    ("cluster_stability",  _maybe_cluster_stability),
    ("bayesian_target",    _maybe_bayesian),
    ("panel_decomposition", _maybe_panel),
    ("survival",           _maybe_survival),
    ("spatial",            _maybe_spatial),
    ("network",            _maybe_network),
    ("did",                _maybe_did),  # depends on panel — must run after
]


def _call_with_timeout(
    fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    state: Dict[str, Any],
    label: str,
    seconds: int,
) -> Dict[str, Any]:
    """Invoke ``fn(state)`` under a hard wall-clock timeout.

    Returns either:
      * the function's normal return dict, or
      * a structured timeout-skip dict::

            {"available": False, "applied": False,
             "skipped_reason": "method_timeout",
             "elapsed_seconds": <actual>, "timeout_seconds": <cap>}

    Implementation: ``concurrent.futures.ThreadPoolExecutor`` +
    ``future.result(timeout=...)``. Chosen because:

    * **Cross-platform.** ``signal.alarm`` only works on Unix; the
      product runs on Windows. ThreadPoolExecutor.result(timeout) is
      portable.
    * **Honest about cancellation.** Python threads cannot be killed
      from outside (no PEP-equivalent), so a CPU-bound runaway method
      keeps running in the background until it finishes naturally OR
      the process exits. We accept this per the v1.1 brief: the
      user-facing pipeline does NOT block on the orphan; it records
      the timeout and proceeds. Memory/CPU continue to be consumed by
      the orphan until the runner subprocess terminates.

    The wrapper logs every timeout to ``aurora.advanced_pass`` with
    ``traceback.format_exc()`` (called inside the timeout branch, so
    the stack shows where the wrapper noticed the timeout, not the
    method's own state — that's by design; the worker thread is still
    running and dumping its stack would race).
    """
    t0 = time.perf_counter()
    # max_workers=1 so we don't accidentally fan out — we want one
    # background runner per method, not a thread pool.
    #
    # IMPORTANT: do NOT use ``with ThreadPoolExecutor(...)`` here. The
    # context manager's ``__exit__`` calls ``shutdown(wait=True)`` which
    # would block until the orphan thread finishes — defeating the
    # whole point of the timeout. Instead we hold a manual reference
    # and call ``shutdown(wait=False)`` in the finally block so we
    # return promptly and let the orphan run out the clock.
    ex = ThreadPoolExecutor(max_workers=1,
                              thread_name_prefix=f"adv-{label}")
    fut = ex.submit(fn, state)
    try:
        try:
            result = fut.result(timeout=seconds)
            return result
        except FutTimeout:
            elapsed = time.perf_counter() - t0
            _log.error(
                "advanced-pass step %s exceeded per-method timeout "
                "(%.1fs > %ds budget); worker thread will continue in "
                "background until process exit. Pipeline marks this "
                "method as timed_out and moves on.\n%s",
                label, elapsed, seconds, traceback.format_exc(),
            )
            return {
                "available": False,
                "applied": False,
                "skipped_reason": "method_timeout",
                "elapsed_seconds": float(elapsed),
                # Preserve the caller's value exactly (don't ``int()`` — the
                # test harness uses sub-second caps and would silently
                # round to 0). Production cap is an int (90), JSON will
                # serialize it as an int either way.
                "timeout_seconds": seconds,
            }
        except Exception as e:
            # Non-timeout exception inside the future. The shape mirrors
            # _exc_skip but we don't reuse it directly because we want
            # the elapsed time recorded too — useful for the bench.
            elapsed = time.perf_counter() - t0
            _log.error(
                "advanced-pass step %s raised %s after %.2fs\n%s",
                label, type(e).__name__, elapsed, traceback.format_exc(),
            )
            return {
                "applied": False,
                "reason": f"{label}_exception: {type(e).__name__}: {e}",
                "elapsed_seconds": float(elapsed),
            }
    finally:
        # ``wait=False`` so the orchestrator returns immediately on
        # timeout. The orphan thread keeps running until it finishes
        # naturally OR the process exits — accepted per the v1.1
        # brief ("worker thread runs to completion in the background
        # but the orchestrator marks the result as timed out and
        # moves on"). ``cancel_futures=True`` is a no-op for futures
        # already running (per stdlib docs) but harmless.
        ex.shutdown(wait=False, cancel_futures=True)


def apply_advanced_pass(state: Dict[str, Any]) -> Dict[str, Any]:
    """Run every Brief-3 method that has a usable input shape.

    Each step is best-effort: a failing step records its skip reason
    but never blocks the others. The full audit dict is returned and
    also written to ``state.advanced_methods``.

    v1.1: every step is wrapped in a hard wall-clock timeout
    (``PER_METHOD_TIMEOUT_SECONDS`` = 90s) via ``_call_with_timeout``.
    A method that exceeds the cap has its result block stamped with
    ``skipped_reason: "method_timeout"`` and ``elapsed_seconds`` so
    the synthesis layer and frontend can disclose honestly. The
    worst-case full-pipeline time becomes
    ``(seconds_for_fast_methods) + 90s × (number_of_timeouts)``
    instead of unbounded.
    """
    if not isinstance(state, dict):
        return {"applied": False, "reason": "state_not_dict"}
    out: Dict[str, Any] = {"applied": True, "steps": {}}
    for label, fn in _STEPS:
        out["steps"][label] = _call_with_timeout(
            fn, state, label=label, seconds=PER_METHOD_TIMEOUT_SECONDS,
        )
    state["advanced_methods"] = out
    return out
