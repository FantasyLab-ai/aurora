"""
Runtime calibration lookup — the part that decides whether calibration is
a real feature or a decoration.

Non-negotiable rules, enforced here:

  * Never silently interpolate. Between grid points the status is
    ``interpolated`` and the interpolation error is reported, or the
    answer is unavailable. There is no third path.
  * Never extrapolate beyond the calibrated envelope. Outside it, the
    status is ``unavailable`` with the offending dimension named.
  * Always report the distance between the observed regime and the
    matched grid point.
  * Uncalibrated methods return ``not_yet_calibrated`` — never a default
    rate, never an assumed nominal alpha, never silence.

Status vocabulary (exactly four):
    calibrated | interpolated | unavailable | not_yet_calibrated
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from .fingerprint import RegimeFingerprint


@dataclass(frozen=True)
class CalibrationConfig:
    """Runtime knobs. Every value that can change a verdict is recorded
    in the emitted calibration block — a verdict that depends on a
    threshold must disclose the threshold."""
    # A positive finding whose empirical FDR exceeds this ceiling is
    # downgraded to not-identifiable (see attach.py).
    fdr_ceiling: float = 0.25
    # Target rate used for the consecutive-confirmations remediation.
    confirmation_target: float = 0.05
    # Snap tolerances: an observed regime within these of a grid point is
    # scored against that point directly (status 'calibrated', distance
    # reported). Between points, we interpolate and say so.
    snap_phi: float = 0.05
    snap_log_n_ratio: float = math.log(1.20)   # n within +/-20%
    # Envelope margins: beyond these past the outermost grid values, the
    # answer is 'unavailable', never a guess.
    margin_phi: float = 0.05
    margin_log_n_ratio: float = math.log(1.25)
    margin_kurtosis: float = 2.5
    margin_skewness: float = 1.0
    margin_missingness: float = 0.02
    corpus_version: str = "v1"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _entries_for(corpus: Dict[str, Any], method: str) -> List[Dict[str, Any]]:
    return [e for e in (corpus.get("entries") or [])
            if e.get("method") == method]


def _iid_baseline(corpus: Dict[str, Any], method: str, n: int
                  ) -> Optional[Dict[str, Any]]:
    """The measured IID cell at the nearest calibrated n — the honest
    'inflation' denominator (there is no nominal alpha to inflate)."""
    cells = [e for e in _entries_for(corpus, method)
             if e.get("generator") == "IIDNormal"]
    if not cells:
        return None
    return min(cells, key=lambda e: abs(math.log(max(e["n"], 1) / max(n, 1))))


def lookup_calibration(
    fp: RegimeFingerprint,
    method: str,
    corpus: Dict[str, Any],
    config: Optional[CalibrationConfig] = None,
) -> Dict[str, Any]:
    """Match an observed regime against the corpus for one method.

    Returns the calibration block (finding-schema shape, §calibration in
    docs/CALIBRATION.md). Pure lookup — no side effects, no defaults.
    """
    cfg = config or CalibrationConfig()
    corpus_sha = str(corpus.get("_sha256") or "")
    version = str(corpus.get("corpus_version") or cfg.corpus_version)

    base = {
        "corpus_version": version,
        "corpus_sha256": corpus_sha,
        "observed_regime": fp.to_dict(),
        "fdr_ceiling": cfg.fdr_ceiling,
    }

    entries = _entries_for(corpus, method)
    if not entries:
        return {
            **base,
            "status": "not_yet_calibrated",
            "note": (
                f"No calibration corpus covers method {method!r} yet. "
                "This is a statement of missing evidence, not of safety."
            ),
        }

    # ---- Envelope checks: refuse to extrapolate ------------------------
    def _unavailable(dim: str, detail: str) -> Dict[str, Any]:
        return {
            **base,
            "status": "unavailable",
            "outside_envelope_dimension": dim,
            "note": (
                f"Observed regime is outside the calibrated envelope on "
                f"{dim!r}: {detail}. No rate is reported because none was "
                "measured for data of this shape."
            ),
        }

    regs = [e["regime_measured"] for e in entries]
    ns = sorted({int(e["n"]) for e in entries})
    phis = sorted({float(r["phi_hat"]) for r in regs})
    kurts = [float(r["excess_kurtosis"]) for r in regs]
    skews = [float(r["skewness"]) for r in regs]

    if fp.n < ns[0] / math.exp(cfg.margin_log_n_ratio) or \
       fp.n > ns[-1] * math.exp(cfg.margin_log_n_ratio):
        return _unavailable("n", f"n={fp.n}, calibrated n in [{ns[0]}, {ns[-1]}]")

    if fp.phi_hat < phis[0] - cfg.margin_phi or fp.phi_hat > phis[-1] + cfg.margin_phi:
        return _unavailable(
            "phi_hat",
            f"phi_hat={fp.phi_hat}, calibrated range [{phis[0]}, {phis[-1]}]",
        )

    if fp.missingness > cfg.margin_missingness:
        return _unavailable(
            "missingness",
            f"missingness={fp.missingness:.1%}; corpus v1 cells were built "
            "on complete series",
        )

    if fp.excess_kurtosis < min(kurts) - cfg.margin_kurtosis or \
       fp.excess_kurtosis > max(kurts) + cfg.margin_kurtosis:
        return _unavailable(
            "excess_kurtosis",
            f"excess_kurtosis={fp.excess_kurtosis}, calibrated range "
            f"[{min(kurts):.2f}, {max(kurts):.2f}]",
        )

    if fp.skewness < min(skews) - cfg.margin_skewness or \
       fp.skewness > max(skews) + cfg.margin_skewness:
        return _unavailable(
            "skewness",
            f"skewness={fp.skewness}, calibrated range "
            f"[{min(skews):.2f}, {max(skews):.2f}]",
        )

    if not fp.sampling_regular and not any(
        r.get("sampling_regular") is False for r in regs
    ):
        return _unavailable(
            "sampling_regular",
            "series is irregularly sampled and the corpus for this method "
            "has no irregular cells",
        )

    if fp.seasonal_period is not None:
        seasonal_cells = [
            e for e in entries
            if e["regime_measured"].get("seasonal_period") is not None
        ]
        if not seasonal_cells:
            return _unavailable(
                "seasonal_period",
                f"detected period {fp.seasonal_period} but the corpus for "
                "this method has no seasonal cells",
            )

    # ---- Candidate matching -------------------------------------------
    # Structural dims first: seasonal series match seasonal cells,
    # irregular series match irregular cells. Then nearest by (phi, n).
    def _structural_pool() -> List[Dict[str, Any]]:
        if fp.seasonal_period is not None:
            pool = [e for e in entries
                    if e["regime_measured"].get("seasonal_period") is not None]
        else:
            pool = [e for e in entries
                    if e["regime_measured"].get("seasonal_period") is None]
        if not fp.sampling_regular:
            irr = [e for e in pool
                   if e["regime_measured"].get("sampling_regular") is False]
            if irr:
                pool = irr
        return pool or entries

    pool = _structural_pool()

    def _dist(e: Dict[str, Any]) -> float:
        r = e["regime_measured"]
        d_phi = abs(fp.phi_hat - float(r["phi_hat"])) / max(cfg.snap_phi, 1e-9)
        d_n = abs(math.log(max(fp.n, 1) / max(int(e["n"]), 1))) / \
            max(cfg.snap_log_n_ratio, 1e-9)
        d_k = abs(fp.excess_kurtosis - float(r["excess_kurtosis"])) / 3.0
        return math.sqrt(d_phi ** 2 + d_n ** 2 + d_k ** 2)

    nearest = min(pool, key=_dist)
    near_r = nearest["regime_measured"]
    d_phi_abs = abs(fp.phi_hat - float(near_r["phi_hat"]))
    d_n_log = abs(math.log(max(fp.n, 1) / max(int(nearest["n"]), 1)))

    distance_block = {
        "phi_hat": round(d_phi_abs, 4),
        "n_log_ratio": round(d_n_log, 4),
        "scalar": round(_dist(nearest), 4),
    }

    def _result(entry: Dict[str, Any], status: str, extra: Dict[str, Any]
                ) -> Dict[str, Any]:
        iid = _iid_baseline(corpus, method, fp.n)
        fdr = float(extra.get("empirical_fdr", entry["fdr"]))
        out = {
            **base,
            "status": status,
            "empirical_fdr": round(fdr, 4),
            "ci95": extra.get("ci95", entry["ci95"]),
            # None, deliberately: BOCPD/CUSUM/PELT make no alpha-level
            # claim, so there is no nominal rate to report. See corpus
            # MANIFEST for the discussion.
            "nominal_alpha": entry.get("nominal_alpha"),
            "matched_regime": {
                "generator": entry["generator"],
                "params": entry.get("params", {}),
                "n": entry["n"],
                "phi_hat": entry["regime_measured"]["phi_hat"],
            },
            "regime_distance": distance_block,
            "trials": entry.get("eligible", entry.get("trials")),
            "method_version": entry.get("method_version"),
            "generator_version": entry.get("generator_version"),
        }
        if iid is not None:
            out["iid_baseline_fdr"] = iid["fdr"]
            out["iid_baseline_ci95"] = iid["ci95"]
            out["iid_baseline_n"] = iid["n"]
            if iid["fdr"] > 0:
                out["fdr_vs_iid"] = round(fdr / iid["fdr"], 1)
            else:
                # Baseline never fired in its trials; a ratio would be a
                # made-up number. Report the bound instead.
                out["fdr_vs_iid"] = None
                out["fdr_vs_iid_note"] = (
                    f"IID baseline fired 0/{iid.get('eligible', iid.get('trials'))} "
                    f"times (95% CI upper bound {iid['ci95'][1]:.2%}); "
                    "ratio not reported to avoid dividing by an unmeasured zero."
                )
        out.update({k: v for k, v in extra.items()
                    if k not in ("empirical_fdr", "ci95")})
        return out

    # Snap: close enough to a grid point on every matched dim.
    if d_phi_abs <= cfg.snap_phi and d_n_log <= cfg.snap_log_n_ratio:
        return _result(nearest, "calibrated", {
            "note": _note_text(method, nearest["fdr"], nearest["ci95"],
                               near_r["phi_hat"], nearest["n"]),
        })

    # Between phi grid points at a matched n: interpolate AND SAY SO.
    same_n = [e for e in pool
              if abs(math.log(max(fp.n, 1) / max(int(e["n"]), 1)))
              <= cfg.snap_log_n_ratio]
    below = [e for e in same_n
             if float(e["regime_measured"]["phi_hat"]) <= fp.phi_hat]
    above = [e for e in same_n
             if float(e["regime_measured"]["phi_hat"]) >= fp.phi_hat]
    if below and above:
        lo = max(below, key=lambda e: float(e["regime_measured"]["phi_hat"]))
        hi = min(above, key=lambda e: float(e["regime_measured"]["phi_hat"]))
        p_lo = float(lo["regime_measured"]["phi_hat"])
        p_hi = float(hi["regime_measured"]["phi_hat"])
        span = max(p_hi - p_lo, 1e-9)
        w = (fp.phi_hat - p_lo) / span
        fdr = (1 - w) * float(lo["fdr"]) + w * float(hi["fdr"])
        ci = [
            round((1 - w) * lo["ci95"][0] + w * hi["ci95"][0], 6),
            round((1 - w) * lo["ci95"][1] + w * hi["ci95"][1], 6),
        ]
        interp_err = abs(float(hi["fdr"]) - float(lo["fdr"])) / 2.0
        return _result(hi if w >= 0.5 else lo, "interpolated", {
            "empirical_fdr": round(fdr, 4),
            "ci95": ci,
            "interpolation": {
                "dimension": "phi_hat",
                "between": [
                    {"phi_hat": p_lo, "fdr": lo["fdr"], "n": lo["n"]},
                    {"phi_hat": p_hi, "fdr": hi["fdr"], "n": hi["n"]},
                ],
                "weight": round(w, 3),
                # Half the FDR span between the bracketing cells: linear
                # interpolation can be off by at most the span, and the
                # midpoint by half of it. Disclosed, not hidden.
                "max_error": round(interp_err, 4),
            },
            "note": (
                f"Interpolated between calibrated points phi={p_lo:.2f} "
                f"({lo['fdr']:.1%}) and phi={p_hi:.2f} ({hi['fdr']:.1%}); "
                f"interpolation error is at most ±{interp_err:.1%}."
            ),
        })

    # Inside the envelope but no bracketing pair and no snap — refuse to
    # guess rather than stretch a distant cell.
    return {
        **base,
        "status": "unavailable",
        "outside_envelope_dimension": None,
        "nearest_regime": {
            "generator": nearest["generator"],
            "n": nearest["n"],
            "phi_hat": near_r["phi_hat"],
        },
        "regime_distance": distance_block,
        "note": (
            "No calibrated cell is close enough to this regime to score "
            "against, and no bracketing pair exists to interpolate "
            "honestly. The nearest cell and the distance are reported "
            "above; a rate is not."
        ),
    }


def _note_text(method: str, fdr: float, ci95: List[float], phi: float, n: int
               ) -> str:
    return (
        f"Detector '{method}' fires on {fdr:.1%} of stationary null series "
        f"with these properties (phi_hat≈{phi:.2f}, n={n}; 95% CI "
        f"{ci95[0]:.1%}–{ci95[1]:.1%}), measured on seeded synthetic nulls."
    )


def derive_remediation(
    block: Dict[str, Any],
    fp: RegimeFingerprint,
    method: str,
    corpus: Dict[str, Any],
    config: Optional[CalibrationConfig] = None,
) -> List[Dict[str, str]]:
    """Concrete next steps — only where the math supports them.

    Every item states its own assumption. Nothing generic is emitted; if
    no remediation is derivable from the corpus, the list is empty.
    """
    cfg = config or CalibrationConfig()
    out: List[Dict[str, str]] = []
    fdr = block.get("empirical_fdr")
    if not isinstance(fdr, (int, float)) or not (0.0 < fdr < 1.0):
        return out

    # Consecutive independent confirmations to reach the target rate.
    if fdr > cfg.confirmation_target:
        k = math.ceil(math.log(cfg.confirmation_target) / math.log(fdr))
        if k >= 2:
            out.append({
                "action": "require_consecutive_confirmations",
                "detail": (
                    f"Require {k} consecutive independent detections before "
                    f"acting: {fdr:.1%}^{k} ≤ {cfg.confirmation_target:.0%}. "
                    "Assumes confirmations on fresh, non-overlapping data — "
                    "re-testing the same window is not independent."
                ),
            })

    # Pre-whitening: derivable when the corpus shows the IID cell is far
    # cleaner than the matched autocorrelated cell.
    iid_fdr = block.get("iid_baseline_fdr")
    if fp.phi_hat >= 0.15 and isinstance(iid_fdr, (int, float)) and iid_fdr < fdr / 4:
        out.append({
            "action": "pre_whiten",
            "detail": (
                f"Remove the AR(1) component (estimated phi_hat="
                f"{fp.phi_hat:.2f}) before testing: the measured false-fire "
                f"rate on independent data of this length is "
                f"{iid_fdr:.1%} vs {fdr:.1%} here."
            ),
        })

    # Minimum series length: only if the corpus actually contains a
    # larger-n cell at the same regime with an acceptable rate.
    entries = [
        e for e in _entries_for(corpus, method)
        if e["generator"] == block.get("matched_regime", {}).get("generator")
        and e.get("params") == block.get("matched_regime", {}).get("params")
        and e["n"] > fp.n and e["fdr"] <= cfg.confirmation_target
    ]
    if entries:
        best = min(entries, key=lambda e: e["n"])
        out.append({
            "action": "extend_series",
            "detail": (
                f"At n={best['n']} the measured rate for this regime drops "
                f"to {best['fdr']:.1%} — collect "
                f"{best['n'] - fp.n} more observations before re-testing."
            ),
        })
    return out
