"""
Null generators for the calibration engine.

Every generator produces data with known ground truth: NOTHING TO FIND.
No changepoints, no regime shifts, no anomalous structure beyond what the
named null process itself implies. The calibration harness runs Aurora's
production detectors on these series and records how often they fire
anyway — that firing rate is the empirical false discovery rate for data
of that shape.

Reproducibility contract:

  * Same (generator, params, n, trial) always yields the same series, on
    every machine. Seeding goes through ``numpy.random.SeedSequence`` with
    a fixed entropy (``BASE_SEED``) and a spawn key derived from a SHA-256
    of the cell descriptor — no Python ``hash()`` (which is salted per
    process) anywhere in the path.
  * Every generator declares a ``version``. Changing a generator's output
    for the same seed is a breaking change: bump the version, rebuild the
    corpus. The corpus records the generator version it was built with,
    and the test suite pins the current versions.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import numpy as np

# The single root seed for corpus v1. Recorded in the corpus manifest.
BASE_SEED = 20260815


def _stable_key(*parts: Any) -> Tuple[int, ...]:
    """Derive a deterministic spawn key from arbitrary JSON-able parts.

    SHA-256 of the canonical JSON, split into four 32-bit ints. Stable
    across machines, Python builds, and process restarts — unlike
    ``hash()``, which is salted.
    """
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(blob).digest()
    return tuple(
        int.from_bytes(digest[i * 4:(i + 1) * 4], "big") for i in range(4)
    )


def make_rng(*key_parts: Any) -> np.random.Generator:
    """A seeded, reproducible Generator for the given key parts."""
    seq = np.random.SeedSequence(entropy=BASE_SEED, spawn_key=_stable_key(*key_parts))
    return np.random.Generator(np.random.PCG64(seq))


@dataclass(frozen=True)
class GeneratedSeries:
    """One null draw. ``timestamps`` is None for regularly-sampled series
    (detectors index by row); irregular generators supply real gaps."""
    values: np.ndarray
    timestamps: Optional[np.ndarray] = None


@dataclass(frozen=True)
class NullGenerator:
    """Base for all null generators.

    ``varies`` names the fingerprint dimensions this generator can move —
    the harness uses it to document coverage, and the docs use it to be
    explicit about what the corpus does NOT cover.
    """
    name: str = "base"
    version: str = "0.0.0"
    varies: Tuple[str, ...] = ()

    def generate(self, n: int, rng: np.random.Generator, **params: Any) -> GeneratedSeries:
        raise NotImplementedError


class IIDNormal(NullGenerator):
    """Independent standard normal draws. The baseline null."""

    def __init__(self) -> None:
        object.__setattr__(self, "name", "IIDNormal")
        object.__setattr__(self, "version", "1.0.0")
        object.__setattr__(self, "varies", ("n",))

    def generate(self, n: int, rng: np.random.Generator, **params: Any) -> GeneratedSeries:
        return GeneratedSeries(values=rng.normal(0.0, 1.0, n))


class AR1(NullGenerator):
    """Stationary AR(1): x_t = phi * x_{t-1} + e_t, e ~ N(0, sigma).

    The first observation is drawn from the stationary marginal so the
    series has no startup transient (a transient would be a *real*
    structural feature and the ground truth must stay 'nothing to find').
    Output is rescaled to unit marginal variance so phi is the only thing
    that changes between cells.
    """

    def __init__(self) -> None:
        object.__setattr__(self, "name", "AR1")
        object.__setattr__(self, "version", "1.0.0")
        object.__setattr__(self, "varies", ("n", "phi_hat"))

    def generate(self, n: int, rng: np.random.Generator, *, phi: float = 0.6,
                 **params: Any) -> GeneratedSeries:
        if not -0.999 < phi < 0.999:
            raise ValueError(f"AR1 phi must be in (-1, 1); got {phi}")
        marg = 1.0 / np.sqrt(max(1.0 - phi ** 2, 1e-9))
        x = np.empty(n)
        x[0] = rng.normal(0.0, marg)
        eps = rng.normal(0.0, 1.0, n)
        for t in range(1, n):
            x[t] = phi * x[t - 1] + eps[t]
        return GeneratedSeries(values=x / marg)


class ARMA(NullGenerator):
    """Stationary ARMA(1,1) — broader dependence than AR(1).

    x_t = phi*x_{t-1} + e_t + theta*e_{t-1}. A 200-step burn-in removes
    the startup transient; output is rescaled to unit marginal variance
    using the closed-form ARMA(1,1) variance.
    """

    def __init__(self) -> None:
        object.__setattr__(self, "name", "ARMA")
        object.__setattr__(self, "version", "1.0.0")
        object.__setattr__(self, "varies", ("n", "phi_hat"))

    def generate(self, n: int, rng: np.random.Generator, *, phi: float = 0.5,
                 theta: float = 0.3, **params: Any) -> GeneratedSeries:
        if not -0.999 < phi < 0.999:
            raise ValueError(f"ARMA phi must be in (-1, 1); got {phi}")
        burn = 200
        eps = rng.normal(0.0, 1.0, n + burn)
        x = np.zeros(n + burn)
        for t in range(1, n + burn):
            x[t] = phi * x[t - 1] + eps[t] + theta * eps[t - 1]
        # Var(x) for ARMA(1,1) with unit innovation variance.
        marg_var = (1.0 + 2.0 * phi * theta + theta ** 2) / (1.0 - phi ** 2)
        return GeneratedSeries(values=x[burn:] / np.sqrt(marg_var))


class SeasonalStationary(NullGenerator):
    """A fixed seasonal cycle plus iid noise. Seasonal, but stationary —
    the cycle never shifts, so there is still no changepoint to find."""

    def __init__(self) -> None:
        object.__setattr__(self, "name", "SeasonalStationary")
        object.__setattr__(self, "version", "1.0.0")
        object.__setattr__(self, "varies", ("n", "seasonal_period"))

    def generate(self, n: int, rng: np.random.Generator, *, period: int = 7,
                 amplitude: float = 1.0, **params: Any) -> GeneratedSeries:
        if period < 2:
            raise ValueError(f"seasonal period must be >= 2; got {period}")
        t = np.arange(n)
        cycle = amplitude * np.sin(2.0 * np.pi * t / period)
        return GeneratedSeries(values=cycle + rng.normal(0.0, 1.0, n))


class HeavyTailed(NullGenerator):
    """IID Student-t innovations — heavy tails, no structure. Scaled to
    unit variance (df must be > 2 for the variance to exist)."""

    def __init__(self) -> None:
        object.__setattr__(self, "name", "HeavyTailed")
        object.__setattr__(self, "version", "1.0.0")
        object.__setattr__(self, "varies", ("n", "excess_kurtosis"))

    def generate(self, n: int, rng: np.random.Generator, *, df: float = 5.0,
                 **params: Any) -> GeneratedSeries:
        if df <= 2.0:
            raise ValueError(f"HeavyTailed df must be > 2; got {df}")
        scale = np.sqrt((df - 2.0) / df)
        return GeneratedSeries(values=rng.standard_t(df, n) * scale)


class IrregularSampled(NullGenerator):
    """IID normal values observed at exponentially-spaced timestamps.

    Aurora's changepoint detectors consume the value stream and ignore
    timestamps, so the measured rates match IIDNormal — the point of
    shipping these cells is that ``sampling_regular=False`` is *inside*
    the calibrated envelope with a measured number, rather than a guess.
    """

    def __init__(self) -> None:
        object.__setattr__(self, "name", "IrregularSampled")
        object.__setattr__(self, "version", "1.0.0")
        object.__setattr__(self, "varies", ("n", "sampling_regular"))

    def generate(self, n: int, rng: np.random.Generator, *, mean_gap: float = 1.0,
                 **params: Any) -> GeneratedSeries:
        gaps = rng.exponential(mean_gap, n)
        return GeneratedSeries(
            values=rng.normal(0.0, 1.0, n),
            timestamps=np.cumsum(gaps),
        )


# Registry, keyed by generator name as it appears in corpus entries.
GENERATORS: Dict[str, NullGenerator] = {
    g.name: g
    for g in (
        IIDNormal(), AR1(), ARMA(), SeasonalStationary(),
        HeavyTailed(), IrregularSampled(),
    )
}


def generate_cell_trial(generator: str, params: Dict[str, Any], n: int,
                        trial: int) -> GeneratedSeries:
    """The one true way to draw trial ``trial`` of a corpus cell.

    Both the corpus build and any later audit re-run go through this
    function, so 'same seed, same series, forever' holds by construction.
    """
    gen = GENERATORS.get(generator)
    if gen is None:
        raise KeyError(f"unknown generator {generator!r}; have {sorted(GENERATORS)}")
    rng = make_rng(gen.name, gen.version, dict(params), int(n), int(trial))
    return gen.generate(n, rng, **params)
