"""
Aurora SDK — typed accessors for the analytical methods that fire on
every run.

Until now, the SDK has been a thin wrapper around findings + bundle
integrity. Method outputs were buried inside ``Finding.evidence`` dicts
with no typing or autocomplete. That's fine for human inspection but
clumsy for programmatic consumers — especially quantitative trading
engines that need to read VAR coefficients, BOCPD change-points,
Kalman noise-reduction, or Matrix Profile motifs from code without
parsing untyped dicts.

This module ships a typed `MethodsView` namespace exposed at
``RunResult.methods``. Each accessor returns a frozen dataclass when
the underlying method fit successfully, or ``None`` when it skipped
(data shape didn't qualify) or failed (the run recorded the error).

The 12 first-class accessors:

    r.methods.var()              VAR(p) multivariate forecast
    r.methods.dtw()              Dynamic-time-warping pair similarity
    r.methods.bocpd()            Bayesian online change-point detection
    r.methods.robust_pca()       Low-rank + sparse decomposition
    r.methods.emd()              Empirical-mode decomposition (IMFs)
    r.methods.kalman()           State-space smoothing + forecast
    r.methods.spectral_entropy() Spectral concentration / regime class
    r.methods.matrix_profile()   Motifs + discords (v0.10.1; newly wired)
    r.methods.granger()          Granger-causality pairs (when available)
    r.methods.mutual_info()      Mutual-information matrix
    r.methods.hmm()              HMM latent regimes
    r.methods.wavelet()          Morlet CWT period / stationarity

Plus the trading-specific helper:

    r.methods.backtest(prices, signal_fn)   Strategy P&L + Sharpe + DD

And the calibration escape hatch (v0.10.0):

    r.methods.calibration("bocpd")   measured false-fire rate block
                                     attached by the calibration engine

Design choices:
    * Every accessor returns ``None`` when the method didn't fire —
      no exceptions, no half-fits. Callers do ``if not (var := r.methods.var()): return``.
    * Dataclasses are frozen so they don't accidentally mutate after
      construction. Use ``.to_dict()`` if you need a plain dict.
    * No work happens at construction time — the parse is lazy per
      accessor call. Multiple calls re-parse (cheap; <1 ms).
    * The structured raw evidence is always available via
      ``r.methods.evidence("method_name")`` for fields we haven't
      typed yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import (
    Any, Callable, Dict, List, Optional, Sequence, Tuple, Union,
)


# ----------------------------------------------------------------------
# Typed dataclasses — one per first-class method.
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class CrossCoupling:
    """One pair of variables coupled via VAR(p)."""
    from_col: str
    to_col:   str
    coefficient_lag1: Optional[float]

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class VarFit:
    """Output of fit_var()."""
    chosen_lag: int
    n_obs:      int
    n_vars:     int
    target_cols: List[str]
    top_coupling: Optional[CrossCoupling]
    forecast_horizon: Optional[int]
    forecast_summary: Optional[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.top_coupling is not None:
            d["top_coupling"] = self.top_coupling.to_dict()
        return d


@dataclass(frozen=True)
class DtwPair:
    col_a: str
    col_b: str
    distance: float

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class DtwFit:
    n_obs: int
    window: int
    most_similar:    List[DtwPair]
    most_dissimilar: List[DtwPair]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_obs": self.n_obs, "window": self.window,
            "most_similar":    [p.to_dict() for p in self.most_similar],
            "most_dissimilar": [p.to_dict() for p in self.most_dissimilar],
        }


@dataclass(frozen=True)
class ChangePoint:
    row_idx:           int
    posterior_at_r0:   float

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class BocpdFit:
    target_col: str
    n_obs:      int
    threshold:  float
    hazard:     float
    change_points: List[ChangePoint]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_col": self.target_col, "n_obs": self.n_obs,
            "threshold":  self.threshold, "hazard": self.hazard,
            "change_points": [c.to_dict() for c in self.change_points],
        }


@dataclass(frozen=True)
class OutlierRow:
    row_idx: int
    score:   float

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class RobustPcaFit:
    n_obs:    int
    n_vars:   int
    low_rank_estimate: int
    n_outlier_rows:    int
    lam_used: float
    iterations: int
    converged: bool
    top_outlier_rows: List[OutlierRow]

    def to_dict(self) -> Dict[str, Any]:
        return {
            **{k: getattr(self, k) for k in
                ("n_obs", "n_vars", "low_rank_estimate", "n_outlier_rows",
                 "lam_used", "iterations", "converged")},
            "top_outlier_rows": [o.to_dict() for o in self.top_outlier_rows],
        }


@dataclass(frozen=True)
class ImfStat:
    imf_index: int
    energy:    float
    estimated_period_samples: Optional[float]
    zero_crossings:           Optional[int]

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class EmdFit:
    target_col: str
    n_obs:      int
    n_imfs:     int
    imf_stats:  List[ImfStat]
    sift_iterations: List[int]
    residual_range:  Tuple[float, float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_col": self.target_col, "n_obs": self.n_obs,
            "n_imfs": self.n_imfs,
            "imf_stats": [s.to_dict() for s in self.imf_stats],
            "sift_iterations": list(self.sift_iterations),
            "residual_range":  list(self.residual_range),
        }


@dataclass(frozen=True)
class KalmanForecastStep:
    step:    int
    estimate: float
    ci_low:   float
    ci_high:  float

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class KalmanFit:
    target_col: str
    n_obs:      int
    noise_reduction_fraction: float
    forecast_horizon:         int
    forecast:                 List[KalmanForecastStep]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_col": self.target_col, "n_obs": self.n_obs,
            "noise_reduction_fraction": self.noise_reduction_fraction,
            "forecast_horizon": self.forecast_horizon,
            "forecast": [s.to_dict() for s in self.forecast],
        }


@dataclass(frozen=True)
class SpectralEntropyWindow:
    window_idx: int
    entropy:    float
    dominant_period_samples: Optional[float]

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class SpectralEntropyFit:
    target_col: str
    n_obs:      int
    global_spectral_entropy: float
    regime_class: str       # "low_entropy" / "moderate" / "high_entropy"
    window_results: List[SpectralEntropyWindow]
    biggest_window_jump: Optional[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_col": self.target_col, "n_obs": self.n_obs,
            "global_spectral_entropy": self.global_spectral_entropy,
            "regime_class": self.regime_class,
            "window_results": [w.to_dict() for w in self.window_results],
            "biggest_window_jump": dict(self.biggest_window_jump or {}),
        }


@dataclass(frozen=True)
class Motif:
    rank:    int
    start_a: int
    start_b: int
    distance: float
    window:   int

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class Discord:
    rank:     int
    start:    int
    distance: float
    window:   int

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class MatrixProfileFit:
    target_col: str
    n_obs:      int
    window:     int
    motifs:     List[Motif]
    discords:   List[Discord]
    profile_stats: Dict[str, Optional[float]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_col": self.target_col, "n_obs": self.n_obs,
            "window": self.window,
            "motifs":   [m.to_dict() for m in self.motifs],
            "discords": [d.to_dict() for d in self.discords],
            "profile_stats": dict(self.profile_stats or {}),
        }


@dataclass(frozen=True)
class GrangerPair:
    col_i: str
    col_j: str
    forward_p:  Optional[float]
    reverse_p:  Optional[float]
    verdict:    str   # "no_evidence" | "i_causes_j" | "j_causes_i" | "bidirectional"

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class GrangerFit:
    pairs: List[GrangerPair]

    def to_dict(self) -> Dict[str, Any]:
        return {"pairs": [p.to_dict() for p in self.pairs]}


@dataclass(frozen=True)
class MutualInfoPair:
    col_i: str
    col_j: str
    mi:    float

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class MutualInfoFit:
    n_features: int
    estimator:  str
    nonlinear_pairs: List[MutualInfoPair]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_features": self.n_features, "estimator": self.estimator,
            "nonlinear_pairs": [p.to_dict() for p in self.nonlinear_pairs],
        }


@dataclass(frozen=True)
class HmmFit:
    best_K:      int
    current_state: int
    current_state_posterior: List[float]
    means_sorted: List[float]
    expected_dwell_time: List[float]

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class WaveletFit:
    verdict: str            # "stationary" | "switching" | "drifting"
    period_first_quartile: float
    period_last_quartile:  float
    relative_drift:        float

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


# ----------------------------------------------------------------------
# The view itself.
# ----------------------------------------------------------------------

class MethodsView:
    """Typed accessors over a bundle's findings.

    Construct via ``RunResult.methods`` — don't instantiate directly.
    Each accessor parses the relevant finding's evidence block into a
    typed dataclass, returning ``None`` when the method didn't fire
    (skipped or failed) so callers can branch on truthiness.
    """

    def __init__(self, bundle_or_state: Any):
        # Accept either a Bundle or a raw state dict. The bundle's
        # .doc has the findings + extended_methods blocks we need.
        self._source = bundle_or_state
        self._findings_cache: Optional[List[Dict[str, Any]]] = None
        self._state_cache: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Internal: lazy access to findings + extended_methods block.
    # ------------------------------------------------------------------

    def _state(self) -> Dict[str, Any]:
        if self._state_cache is not None:
            return self._state_cache
        s = self._source
        # Bundle objects carry .doc with the full state.
        if hasattr(s, "doc"):
            s = s.doc
        self._state_cache = s if isinstance(s, dict) else {}
        return self._state_cache

    def _findings(self) -> List[Dict[str, Any]]:
        if self._findings_cache is not None:
            return self._findings_cache
        st = self._state()
        ext = (st.get("extended_methods") or {}).get("per_method") or {}
        # Prefer the extended_methods.per_method block (already keyed by
        # method name with parsed evidence). Fall back to findings list.
        out: List[Dict[str, Any]] = []
        if isinstance(ext, dict) and ext:
            for name, tile in ext.items():
                if not isinstance(tile, dict):
                    continue
                out.append({
                    "method": name,
                    "title": tile.get("title"),
                    "description": tile.get("description"),
                    "severity": tile.get("severity"),
                    "evidence": tile.get("evidence") or {},
                    "calibration": tile.get("calibration"),
                })
        # Also walk top-level findings so methods that don't appear in
        # extended_methods.per_method (granger, hmm, wavelet, etc.) are
        # discoverable.
        top_findings = st.get("findings") or []
        if isinstance(top_findings, list):
            out.extend([f for f in top_findings if isinstance(f, dict)])
        self._findings_cache = out
        return out

    def _find(self, method_name: str) -> Optional[Dict[str, Any]]:
        """Return the first 'fit'-status finding for the given method."""
        m = str(method_name).lower()
        candidates: List[Dict[str, Any]] = []
        for f in self._findings():
            fm = str(f.get("method") or "").lower()
            if m in fm:
                candidates.append(f)
        if not candidates:
            return None
        # Prefer fit > skipped > failed.
        def _rank(f: Dict[str, Any]) -> int:
            st = str(((f.get("evidence") or {}).get("status") or "")).lower()
            return {"fit": 0, "skipped": 1, "failed": 2}.get(st, 3)
        candidates.sort(key=_rank)
        return candidates[0]

    def evidence(self, method_name: str) -> Optional[Dict[str, Any]]:
        """Raw evidence dict for any method — escape hatch for fields
        the typed accessors don't expose yet."""
        f = self._find(method_name)
        return (f or {}).get("evidence")

    def calibration(self, method_name: str) -> Optional[Dict[str, Any]]:
        """The calibration block attached to a method's finding, or None.

        Keys (when status is 'calibrated' or 'interpolated'):
        ``status``, ``empirical_fdr``, ``ci95``, ``matched_regime``,
        ``observed_regime``, ``regime_distance``, ``fdr_ceiling``,
        ``corpus_version``, ``corpus_sha256``, and optionally
        ``remediation`` + ``verdict_downgraded``. Statuses
        'unavailable' and 'not_yet_calibrated' carry a ``note``
        explaining why no rate is reported — the absence of a number
        is itself the calibrated answer in those states.
        """
        # Prefer whichever candidate actually carries the block — the
        # tile projection and the raw finding both may match the name.
        m = str(method_name).lower()
        for f in self._findings():
            if m in str(f.get("method") or "").lower():
                cal = f.get("calibration")
                if isinstance(cal, dict):
                    return cal
        return None

    def fit_status(self, method_name: str) -> Optional[str]:
        """Return 'fit' / 'skipped' / 'failed' / None."""
        ev = self.evidence(method_name)
        if ev is None:
            return None
        st = str(ev.get("status") or "").lower()
        return st or None

    def list_methods(self) -> List[str]:
        """All methods this bundle has a finding for, in order."""
        seen: List[str] = []
        seen_set: set = set()
        for f in self._findings():
            m = str(f.get("method") or "")
            if m and m not in seen_set:
                seen.append(m); seen_set.add(m)
        return seen

    # ==================================================================
    # First-class accessors
    # ==================================================================

    def var(self) -> Optional[VarFit]:
        ev = self._fit_evidence("var")
        if not ev:
            return None
        tc = ev.get("top_cross_coupling") or {}
        return VarFit(
            chosen_lag=int(ev.get("chosen_lag") or 0),
            n_obs=int(ev.get("n_obs") or 0),
            n_vars=int(ev.get("n_vars") or 0),
            target_cols=list(ev.get("target_cols") or []),
            top_coupling=(
                CrossCoupling(
                    from_col=str(tc.get("from_col") or ""),
                    to_col=str(tc.get("to_col") or ""),
                    coefficient_lag1=_safe_float(tc.get("coefficient_lag1")),
                ) if tc else None
            ),
            forecast_horizon=_safe_int(ev.get("forecast_horizon")),
            forecast_summary=ev.get("forecast_summary"),
        )

    def dtw(self) -> Optional[DtwFit]:
        ev = self._fit_evidence("dtw")
        if not ev:
            return None
        def pairs(key: str) -> List[DtwPair]:
            return [
                DtwPair(col_a=str(p.get("col_a") or ""),
                        col_b=str(p.get("col_b") or ""),
                        distance=float(p.get("distance") or 0.0))
                for p in (ev.get(key) or [])
                if isinstance(p, dict)
            ]
        return DtwFit(
            n_obs=int(ev.get("n_obs") or 0),
            window=int(ev.get("window") or 0),
            most_similar=pairs("most_similar"),
            most_dissimilar=pairs("most_dissimilar"),
        )

    def bocpd(self) -> Optional[BocpdFit]:
        ev = self._fit_evidence("bocpd")
        if not ev:
            return None
        cps = [
            ChangePoint(
                row_idx=int(c.get("row_idx") or 0),
                posterior_at_r0=float(c.get("posterior_at_r0") or 0.0),
            )
            for c in (ev.get("change_points") or [])
            if isinstance(c, dict)
        ]
        return BocpdFit(
            target_col=str(ev.get("target_col") or ""),
            n_obs=int(ev.get("n_obs") or 0),
            threshold=float(ev.get("threshold") or 0.0),
            hazard=float(ev.get("hazard") or 0.0),
            change_points=cps,
        )

    def robust_pca(self) -> Optional[RobustPcaFit]:
        ev = self._fit_evidence("robust_pca")
        if not ev:
            return None
        outliers = [
            OutlierRow(row_idx=int(o.get("row_idx") or 0),
                        score=float(o.get("score") or 0.0))
            for o in (ev.get("top_outlier_rows") or [])
            if isinstance(o, dict)
        ]
        return RobustPcaFit(
            n_obs=int(ev.get("n_obs") or 0),
            n_vars=int(ev.get("n_vars") or 0),
            low_rank_estimate=int(ev.get("low_rank_estimate") or 0),
            n_outlier_rows=int(ev.get("n_outlier_rows") or 0),
            lam_used=float(ev.get("lam_used") or 0.0),
            iterations=int(ev.get("iterations") or 0),
            converged=bool(ev.get("converged")),
            top_outlier_rows=outliers,
        )

    def emd(self) -> Optional[EmdFit]:
        ev = self._fit_evidence("emd")
        if not ev:
            return None
        imf_stats = [
            ImfStat(
                imf_index=int(s.get("imf_index") or 0),
                energy=float(s.get("energy") or 0.0),
                estimated_period_samples=_safe_float(s.get("estimated_period_samples")),
                zero_crossings=_safe_int(s.get("zero_crossings")),
            )
            for s in (ev.get("imf_stats") or [])
            if isinstance(s, dict)
        ]
        rr = ev.get("residual_range") or [0.0, 0.0]
        residual_range = (
            float(rr[0]) if len(rr) > 0 else 0.0,
            float(rr[1]) if len(rr) > 1 else 0.0,
        )
        return EmdFit(
            target_col=str(ev.get("target_col") or ""),
            n_obs=int(ev.get("n_obs") or 0),
            n_imfs=int(ev.get("n_imfs") or 0),
            imf_stats=imf_stats,
            sift_iterations=[int(s) for s in (ev.get("sift_iterations") or [])],
            residual_range=residual_range,
        )

    def kalman(self) -> Optional[KalmanFit]:
        ev = self._fit_evidence("kalman")
        if not ev:
            return None
        fc = ev.get("forecast") or []
        steps = [
            KalmanForecastStep(
                step=int(s.get("step") or 0),
                estimate=float(s.get("estimate") or 0.0),
                ci_low=float(s.get("ci_low") or 0.0),
                ci_high=float(s.get("ci_high") or 0.0),
            )
            for s in fc if isinstance(s, dict)
        ]
        return KalmanFit(
            target_col=str(ev.get("target_col") or ""),
            n_obs=int(ev.get("n_obs") or 0),
            noise_reduction_fraction=float(ev.get("noise_reduction_fraction") or 0.0),
            forecast_horizon=int(ev.get("forecast_horizon") or 0),
            forecast=steps,
        )

    def spectral_entropy(self) -> Optional[SpectralEntropyFit]:
        ev = self._fit_evidence("spectral_entropy")
        if not ev:
            return None
        windows = [
            SpectralEntropyWindow(
                window_idx=int(w.get("window_idx") or i),
                entropy=float(w.get("entropy") or 0.0),
                dominant_period_samples=_safe_float(w.get("dominant_period_samples")),
            )
            for i, w in enumerate(ev.get("window_results") or [])
            if isinstance(w, dict)
        ]
        return SpectralEntropyFit(
            target_col=str(ev.get("target_col") or ""),
            n_obs=int(ev.get("n_obs") or 0),
            global_spectral_entropy=float(ev.get("global_spectral_entropy") or 0.0),
            regime_class=str(ev.get("regime_class") or "unknown"),
            window_results=windows,
            biggest_window_jump=ev.get("biggest_window_jump") or None,
        )

    def matrix_profile(self) -> Optional[MatrixProfileFit]:
        ev = self._fit_evidence("matrix_profile")
        if not ev:
            return None
        motifs = [
            Motif(rank=int(m.get("rank") or i+1),
                   start_a=int(m.get("start_a") or 0),
                   start_b=int(m.get("start_b") or 0),
                   distance=float(m.get("distance") or 0.0),
                   window=int(m.get("window") or 0))
            for i, m in enumerate(ev.get("motifs") or [])
            if isinstance(m, dict)
        ]
        discords = [
            Discord(rank=int(d.get("rank") or i+1),
                     start=int(d.get("start") or 0),
                     distance=float(d.get("distance") or 0.0),
                     window=int(d.get("window") or 0))
            for i, d in enumerate(ev.get("discords") or [])
            if isinstance(d, dict)
        ]
        stats = ev.get("profile_stats") or {}
        return MatrixProfileFit(
            target_col=str(ev.get("target_col") or ""),
            n_obs=int(ev.get("n_obs") or 0),
            window=int(ev.get("window") or 0),
            motifs=motifs, discords=discords,
            profile_stats={k: _safe_float(v) for k, v in stats.items()},
        )

    def granger(self) -> Optional[GrangerFit]:
        # Granger lives in state["granger_causality"]["pairs"] in v1.1
        # bundles. Read directly when present; fall back to findings.
        st = self._state()
        gc = st.get("granger_causality") or {}
        pairs_raw: List[Dict[str, Any]] = []
        if isinstance(gc, dict) and gc.get("pairs"):
            pairs_raw = [p for p in gc["pairs"] if isinstance(p, dict)]
        if not pairs_raw:
            # Fall back to findings.evidence
            ev = self._fit_evidence("granger")
            if not ev:
                return None
            pairs_raw = ev.get("pairs") or []
        pairs = [
            GrangerPair(
                col_i=str(p.get("col_i") or ""),
                col_j=str(p.get("col_j") or ""),
                forward_p=_safe_float(p.get("forward_p")),
                reverse_p=_safe_float(p.get("reverse_p")),
                verdict=str(p.get("verdict") or "no_evidence"),
            )
            for p in pairs_raw if isinstance(p, dict)
        ]
        if not pairs:
            return None
        return GrangerFit(pairs=pairs)

    def mutual_info(self) -> Optional[MutualInfoFit]:
        # Lives in state["mutual_info"] in v1.1 bundles.
        st = self._state()
        mi = st.get("mutual_info") or {}
        ev = None
        if isinstance(mi, dict) and mi.get("available"):
            ev = mi
        if ev is None:
            ev = self._fit_evidence("mutual_info")
        if not ev:
            return None
        pairs = [
            MutualInfoPair(col_i=str(p.get("col_i") or ""),
                            col_j=str(p.get("col_j") or ""),
                            mi=float(p.get("mi") or 0.0))
            for p in (ev.get("nonlinear_pairs") or [])
            if isinstance(p, dict)
        ]
        return MutualInfoFit(
            n_features=int(ev.get("n_features") or 0),
            estimator=str(ev.get("estimator") or ""),
            nonlinear_pairs=pairs,
        )

    def hmm(self) -> Optional[HmmFit]:
        st = self._state()
        hmm = st.get("hmm") or {}
        ev = hmm if (isinstance(hmm, dict) and hmm.get("available")) else None
        if ev is None:
            ev = self._fit_evidence("hmm")
        if not ev:
            return None
        return HmmFit(
            best_K=int(ev.get("best_K") or 0),
            current_state=int(ev.get("current_state") or 0),
            current_state_posterior=[float(x) for x in
                                       (ev.get("current_state_posterior") or [])],
            means_sorted=[float(m) for m in (ev.get("means_sorted") or [])],
            expected_dwell_time=[float(d) for d in
                                   (ev.get("expected_dwell_time") or [])],
        )

    def wavelet(self) -> Optional[WaveletFit]:
        st = self._state()
        wv = st.get("wavelet_period") or {}
        ev = wv if (isinstance(wv, dict) and wv.get("available")) else None
        if ev is None:
            ev = self._fit_evidence("wavelet")
        if not ev:
            return None
        return WaveletFit(
            verdict=str(ev.get("verdict") or "unknown"),
            period_first_quartile=float(ev.get("period_first_quartile") or 0.0),
            period_last_quartile=float(ev.get("period_last_quartile") or 0.0),
            relative_drift=float(ev.get("relative_drift") or 0.0),
        )

    # ==================================================================
    # Trading-specific: backtest a strategy
    # ==================================================================

    @staticmethod
    def backtest(
        prices: Union[Sequence[float], Any],
        signal: Union[Callable[..., Sequence[int]], Sequence[int]],
        *,
        initial_capital:      float = 10_000.0,
        commission_per_trade: float = 0.0,
        slippage_bps:         float = 0.0,
        bars_per_year:        int = 252,
        risk_free_rate:       float = 0.0,
    ):
        """Run a strategy backtest. Returns a ``BacktestResult``.

        Convenience pass-through to ``fantasyai.aurora.backtesting.
        backtest_strategy`` so trading-focused SDK users don't have
        to know about the fantasyai package layout.
        """
        from fantasyai.aurora.backtesting import backtest_strategy
        return backtest_strategy(
            prices, signal,
            initial_capital=initial_capital,
            commission_per_trade=commission_per_trade,
            slippage_bps=slippage_bps,
            bars_per_year=bars_per_year,
            risk_free_rate=risk_free_rate,
        )

    @staticmethod
    def signal_from_threshold(
        series: Union[Sequence[float], Any],
        *,
        enter_long_above:  Optional[float] = None,
        exit_long_below:   Optional[float] = None,
        enter_short_below: Optional[float] = None,
        exit_short_above:  Optional[float] = None,
    ) -> List[int]:
        """Convenience: build a position-array from per-bar thresholds.

        Useful for quick rules like:
            sig = MethodsView.signal_from_threshold(
                z_scores, enter_long_above=2.0, exit_long_below=0.0
            )
        """
        from fantasyai.aurora.backtesting import signal_from_threshold
        return signal_from_threshold(
            series,
            enter_long_above=enter_long_above,
            exit_long_below=exit_long_below,
            enter_short_below=enter_short_below,
            exit_short_above=exit_short_above,
        )

    # ------------------------------------------------------------------
    # Internal: pull evidence only for a "fit"-status finding.
    # ------------------------------------------------------------------

    def _fit_evidence(self, method_name: str) -> Optional[Dict[str, Any]]:
        f = self._find(method_name)
        if not f:
            return None
        ev = f.get("evidence") or {}
        if str(ev.get("status") or "").lower() != "fit":
            return None
        return ev


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------

def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        import math as _math
        v = float(x)
        if _math.isnan(v) or _math.isinf(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _safe_int(x: Any) -> Optional[int]:
    if x is None:
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None
