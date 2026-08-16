"""
The calibration harness: run Aurora's PRODUCTION detectors on null data
and record how often they fire.

Production fidelity is the whole game. If this harness measured a
reimplementation, the corpus would be fiction — a table about a detector
nobody actually runs. So:

  * BOCPD goes through ``fantasyai.aurora.math.methods.bocpd.fit_bocpd``
    with its production defaults — the exact function
    ``run_extended_methods`` calls on every analysis.
  * CUSUM and PELT go through
    ``fantasyai.aurora.math.regimes.detect_regimes_worldclass`` — the
    production regimes entry point (CUSUM threshold 8.0, ruptures PELT
    rbf with pen=3.0, both set inside that module, not here).
  * ``PRODUCTION_ENTRYPOINTS`` exposes the exact function objects so the
    test suite can assert, by identity, that the harness calls the same
    code the engine calls. If someone reroutes production detection, the
    test fails and the corpus is declared stale.

Eligibility: production code has data gates (CUSUM needs >= 80 obs, PELT
needs >= 120). When a gate blocks a method the trial is recorded as
*ineligible*, not as "did not fire" — conflating the two would deflate
the measured rate, which is the dishonest direction.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from .fingerprint import fingerprint_series
from .generators import GENERATORS, BASE_SEED, generate_cell_trial

# The changepoint family covered by corpus v1.
CHANGEPOINT_FAMILY = ("bocpd", "cusum", "pelt")

# Method implementation versions, starting at 1.0.0 with corpus v1.
# Any change to a production detector's algorithm or defaults MUST bump
# the version here and trigger a corpus rebuild — the guard tests in
# tests/test_calibration.py pin the production defaults for exactly this
# reason.
METHOD_VERSIONS = {
    "bocpd": "1.0.0",
    "cusum": "1.0.0",
    "pelt":  "1.0.0",
}


def _production_entrypoints() -> Dict[str, Callable[..., Any]]:
    from fantasyai.aurora.math.methods.bocpd import fit_bocpd
    from fantasyai.aurora.math.regimes import detect_regimes_worldclass
    return {
        "bocpd": fit_bocpd,
        "cusum": detect_regimes_worldclass,
        "pelt": detect_regimes_worldclass,
    }


# Exposed for the identity test — see module docstring.
PRODUCTION_ENTRYPOINTS = _production_entrypoints


def run_changepoint_family(values: np.ndarray) -> Dict[str, Optional[bool]]:
    """Run every family method on one series via its production path.

    Returns {method: True (fired) | False (ran, silent) | None
    (ineligible — production data gate blocked it)}.
    """
    import pandas as pd
    eps = _production_entrypoints()
    df = pd.DataFrame({"value": np.asarray(values, dtype=float)})
    out: Dict[str, Optional[bool]] = {}

    # BOCPD — production defaults, production function.
    finding = eps["bocpd"](df, target_col="value")
    ev = (finding.get("evidence") or {}) if isinstance(finding, dict) else {}
    if ev.get("status") == "fit":
        out["bocpd"] = bool(ev.get("change_points"))
    else:
        out["bocpd"] = None

    # CUSUM + PELT — one production call computes both.
    reg = eps["cusum"](df, "value")
    cus = reg.get("changepoints_cusum") if isinstance(reg, dict) else None
    out["cusum"] = bool(cus) if isinstance(cus, list) else None
    rup = (reg.get("changepoints_ruptures") or {}) if isinstance(reg, dict) else {}
    if isinstance(rup, dict) and "changepoints" in rup:
        out["pelt"] = bool(rup.get("changepoints"))
    else:
        out["pelt"] = None  # too_few_points / ruptures not installed
    return out


def wilson_ci(fired: int, trials: int, z: float = 1.959964) -> Tuple[float, float]:
    """95% Wilson score interval. Chosen over the normal approximation
    because rates pinned near 0 and 1 are routine in this corpus."""
    if trials <= 0:
        return (0.0, 1.0)
    p = fired / trials
    z2 = z * z
    denom = 1.0 + z2 / trials
    centre = (p + z2 / (2.0 * trials)) / denom
    half = z * np.sqrt(p * (1.0 - p) / trials + z2 / (4.0 * trials * trials)) / denom
    return (max(0.0, round(centre - half, 6)), min(1.0, round(centre + half, 6)))


# ---------------------------------------------------------------------------
# The corpus grid
# ---------------------------------------------------------------------------

# (generator, params, n). Every cell is a null: nothing to find.
DEFAULT_GRID: List[Tuple[str, Dict[str, Any], int]] = (
    [("IIDNormal", {}, n) for n in (90, 120, 180, 365)]
    + [("AR1", {"phi": phi}, n)
       for phi in (0.2, 0.4, 0.6, 0.8)
       for n in (90, 120, 180, 365)]
    + [("ARMA", {"phi": 0.5, "theta": 0.3}, n) for n in (90, 180)]
    + [("SeasonalStationary", {"period": 7}, n) for n in (90, 180)]
    + [("HeavyTailed", {"df": 5.0}, n) for n in (90, 180)]
    + [("IrregularSampled", {}, 90)]
)

DEFAULT_TRIALS = 4000


def build_cell(
    gen_name: str,
    params: Dict[str, Any],
    n: int,
    trials: int,
    methods: Tuple[str, ...] = CHANGEPOINT_FAMILY,
) -> List[Dict[str, Any]]:
    """Build every corpus entry for one grid cell.

    Module-level and argument-pure so it runs identically in-process or
    inside a multiprocessing worker. Determinism does not depend on
    execution order: every trial is seeded from (generator, version,
    params, n, trial) alone.
    """
    gen = GENERATORS[gen_name]
    tallies = {m: {"fired": 0, "eligible": 0} for m in methods}
    fp_sums: Dict[str, float] = {
        "phi_hat": 0.0, "excess_kurtosis": 0.0, "skewness": 0.0,
    }
    fp_n = 0
    seasonal_hits: Dict[Optional[int], int] = {}
    sampling_regular_all = True

    for trial in range(trials):
        series = generate_cell_trial(gen_name, params, n, trial)
        fp = fingerprint_series(series.values, series.timestamps)
        fp_sums["phi_hat"] += fp.phi_hat
        fp_sums["excess_kurtosis"] += fp.excess_kurtosis
        fp_sums["skewness"] += fp.skewness
        seasonal_hits[fp.seasonal_period] = seasonal_hits.get(fp.seasonal_period, 0) + 1
        sampling_regular_all = sampling_regular_all and fp.sampling_regular
        fp_n += 1

        result = run_changepoint_family(series.values)
        for m in methods:
            r = result.get(m)
            if r is None:
                continue
            tallies[m]["eligible"] += 1
            if r:
                tallies[m]["fired"] += 1

    # The measured mean fingerprint of this cell's nulls — what the
    # runtime lookup matches against. Recording the *measured* value
    # (not the generator's nominal parameter) means estimator bias
    # appears identically on both sides of the match.
    modal_period = max(seasonal_hits, key=lambda k: seasonal_hits[k])
    regime_measured = {
        "n": n,
        "phi_hat": round(fp_sums["phi_hat"] / max(fp_n, 1), 4),
        "missingness": 0.0,
        "sampling_regular": sampling_regular_all,
        "excess_kurtosis": round(fp_sums["excess_kurtosis"] / max(fp_n, 1), 4),
        "skewness": round(fp_sums["skewness"] / max(fp_n, 1), 4),
        "seasonal_period": modal_period,
    }

    entries: List[Dict[str, Any]] = []
    for m in methods:
        eligible = tallies[m]["eligible"]
        fired = tallies[m]["fired"]
        if eligible == 0:
            continue  # production gate blocked this method at this n
        fdr = fired / eligible
        lo, hi = wilson_ci(fired, eligible)
        entries.append({
            "method": m,
            "method_version": METHOD_VERSIONS[m],
            "generator": gen_name,
            "generator_version": gen.version,
            "params": dict(params),
            "n": n,
            "regime_measured": regime_measured,
            "trials": trials,
            "eligible": eligible,
            "fired": fired,
            "fdr": round(fdr, 6),
            "ci95": [lo, hi],
            # No nominal alpha: BOCPD (posterior threshold 0.5),
            # CUSUM (threshold 8 sigma-units) and PELT (pen=3.0) make
            # no alpha-level claim, so there is no nominal rate to
            # compare against. Inflation is reported vs the measured
            # IID baseline instead (computed at lookup time).
            "nominal_alpha": None,
            "seed": {"base": BASE_SEED, "scheme": "sha256(cell)+trial"},
        })
    return entries


def _build_cell_star(args: Tuple[Any, ...]) -> Tuple[int, List[Dict[str, Any]]]:
    """Worker shim: (cell_idx, gen, params, n, trials, methods) → entries."""
    cell_idx, gen_name, params, n, trials, methods = args
    return cell_idx, build_cell(gen_name, params, n, trials, methods)


def build_corpus(
    grid: Optional[List[Tuple[str, Dict[str, Any], int]]] = None,
    *,
    trials: int = DEFAULT_TRIALS,
    methods: Tuple[str, ...] = CHANGEPOINT_FAMILY,
    corpus_version: str = "v1",
    progress: Optional[Callable[[str], None]] = None,
    workers: int = 1,
) -> Dict[str, Any]:
    """Build a calibration corpus document (a batch job, not a runtime path).

    For each grid cell: draw ``trials`` seeded null series, fingerprint
    each with the same estimator the runtime uses, run the production
    changepoint family, tally fires per method.

    ``workers > 1`` fans cells out across processes. The output is
    byte-identical to a sequential build: seeding is per (cell, trial)
    and entries are reassembled in grid order, so parallelism can never
    change a measured number — only the wall clock.

    Returns the corpus dict (see store.save_corpus for the on-disk form).
    """
    grid = list(grid if grid is not None else DEFAULT_GRID)
    t_start = time.time()

    def _report(done: int, cell_idx: int, cell_entries: List[Dict[str, Any]]) -> None:
        if not progress:
            return
        gen_name, params, n = grid[cell_idx]
        counts = ", ".join(
            f"{e['method']}:{e['fired']}/{e['eligible']}" for e in cell_entries
        ) or "no eligible methods"
        progress(
            f"[{done}/{len(grid)}] {gen_name}{params} n={n} — {counts} "
            f"({time.time() - t_start:.0f}s elapsed)"
        )

    per_cell: List[Optional[List[Dict[str, Any]]]] = [None] * len(grid)
    if workers <= 1:
        for cell_idx, (gen_name, params, n) in enumerate(grid):
            per_cell[cell_idx] = build_cell(gen_name, params, n, trials, methods)
            _report(cell_idx + 1, cell_idx, per_cell[cell_idx])
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        jobs = [
            (i, gen_name, params, n, trials, tuple(methods))
            for i, (gen_name, params, n) in enumerate(grid)
        ]
        done = 0
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_build_cell_star, j) for j in jobs]
            for fut in as_completed(futures):
                cell_idx, cell_entries = fut.result()  # propagate failures
                per_cell[cell_idx] = cell_entries
                done += 1
                _report(done, cell_idx, cell_entries)

    # Grid order, regardless of completion order.
    entries: List[Dict[str, Any]] = []
    for cell_entries in per_cell:
        entries.extend(cell_entries or [])

    return {
        "corpus_version": corpus_version,
        "format": "aurora.calibration.corpus",
        "base_seed": BASE_SEED,
        "trials_per_cell": trials,
        "methods": list(methods),
        "method_versions": dict(METHOD_VERSIONS),
        "generator_versions": {k: g.version for k, g in GENERATORS.items()},
        # Production defaults the corpus was measured against. The guard
        # tests pin these against the live source; a mismatch means the
        # corpus is stale and must be rebuilt.
        "method_defaults": {
            "bocpd": {"hazard": 1.0 / 250.0, "threshold": 0.5},
            "cusum": {"threshold": 8.0, "min_n": 80},
            "pelt": {"model": "rbf", "pen": 3.0, "min_n": 120},
        },
        "n_cells": len(grid),
        "n_entries": len(entries),
        "build_seconds": round(time.time() - t_start, 1),
        "entries": entries,
    }
