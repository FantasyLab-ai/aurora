# fantasyai/aurora/backtesting.py
"""
Backtesting helpers.

Two layers:

  1. **Anomaly-threshold sweep** (the original v1.1 helper) — count
     how many anomalies fire across a range of robust-Z thresholds.
     Useful for tuning detector sensitivity on labelled history.

  2. **Strategy backtest** (v0.10.1) — given a price series + a
     signal-generator function, simulate position changes over time
     and compute the standard trading metrics: equity curve, total
     return, Sharpe, max drawdown, win rate, average trade. Built
     for the SDK's `aurora.run(...).methods.backtest(...)` accessor.

Both are honest about their assumptions. The strategy backtester is
**vectorised** (no event-loop overhead) but explicitly NOT designed
for high-frequency / microstructure work — it assumes bar-close
execution at the next bar's open with optional configurable slippage
+ per-trade commission. For tick-level work, plug your own simulator
into the same return shape.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import (
    Any, Callable, Dict, Iterable, List, Optional, Sequence, Union,
)

import math
import numpy as np

# stat_models pulls in sklearn for IsolationForest. The new
# backtest_strategy() doesn't need it; only the legacy threshold_sweep
# does. Import lazily so the strategy backtester works in lean envs.


# ----------------------------------------------------------------------
# v1.1 — anomaly threshold sweep (unchanged)
# ----------------------------------------------------------------------

@dataclass
class ThresholdSweepResult:
    threshold: float
    num_anomalies: int
    coverage_fraction: float  # fraction of time steps flagged


def threshold_sweep(
    times: Sequence[str],
    values: Sequence[float],
    thresholds: List[float],
    min_points: int = 10,
) -> List[ThresholdSweepResult]:
    """
    Simple backtest: for many thresholds, count anomalies and coverage.
    """
    from .stat_models import robust_zscore_anomalies, TimeSeriesAnomaly
    T = len(values)
    results: List[ThresholdSweepResult] = []

    for th in thresholds:
        anoms: List[TimeSeriesAnomaly] = robust_zscore_anomalies(
            times=times,
            values=values,
            threshold=th,
            min_points=min_points,
        )
        idxs = {a.index for a in anoms}
        coverage = len(idxs) / max(1, T)
        results.append(
            ThresholdSweepResult(
                threshold=th,
                num_anomalies=len(anoms),
                coverage_fraction=coverage,
            )
        )
    return results


def summarize_anomalies_by_month(anoms: List[Any]) -> Dict[str, int]:
    """
    Group anomaly counts by YYYY-MM month string.
    """
    from datetime import datetime

    counts: Dict[str, int] = {}
    for a in anoms:
        try:
            dt = datetime.fromisoformat(str(a.time))
            key = dt.strftime("%Y-%m")
        except Exception:
            key = "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


# ----------------------------------------------------------------------
# v0.10.1 — strategy backtest
# ----------------------------------------------------------------------

@dataclass
class TradeRecord:
    """One closed trade (entry + exit)."""
    entry_idx:   int
    exit_idx:    int
    entry_price: float
    exit_price:  float
    direction:   int     # +1 long, -1 short
    pnl:         float   # absolute PnL on 1 unit
    pnl_pct:     float   # PnL as fraction of entry price
    bars_held:   int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BacktestResult:
    """Outcome of a strategy backtest.

    `equity_curve` is the cumulative-return series; multiply by your
    starting capital for dollar-equivalents. Sharpe is annualised
    using the supplied `bars_per_year`.
    """
    n_bars:           int
    n_trades:         int
    n_wins:           int
    n_losses:         int
    win_rate:         float
    total_return:     float           # final equity / initial equity − 1
    cagr:             Optional[float] # None when timespan < 1 year
    sharpe:           Optional[float]
    sortino:          Optional[float]
    max_drawdown:     float           # most-negative trough
    max_drawdown_pct: float           # same, as a fraction of peak
    avg_trade_pnl:    Optional[float]
    avg_trade_pct:    Optional[float]
    best_trade:       Optional[Dict[str, Any]]
    worst_trade:      Optional[Dict[str, Any]]
    equity_curve:     List[float]
    bar_returns:      List[float]
    trades:           List[Dict[str, Any]]
    config:           Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# A signal-generator returns an integer position in {-1, 0, +1} for each
# bar. -1 = short, 0 = flat, +1 = long. Multi-leg / fractional sizing
# is not supported in this MVP; for that, wire your own backtester
# and return the same BacktestResult shape.
SignalFn = Callable[[Any], Sequence[int]]


def backtest_strategy(
    prices: Union[Sequence[float], "np.ndarray", Any],
    signal: Union[SignalFn, Sequence[int], "np.ndarray"],
    *,
    initial_capital:   float = 10_000.0,
    commission_per_trade: float = 0.0,
    slippage_bps:      float = 0.0,
    bars_per_year:     int = 252,
    risk_free_rate:    float = 0.0,
) -> BacktestResult:
    """Vectorised strategy backtest.

    Args:
        prices:   sequence of bar-close prices.
        signal:   either a callable that takes the price array and
                  returns a sequence of {-1, 0, +1} positions OR a
                  pre-computed sequence of the same.
        initial_capital:    starting equity (default 10,000).
        commission_per_trade:   absolute cost per trade (each side).
        slippage_bps:       cost per fill in basis points (one side).
        bars_per_year:      annualisation factor for Sharpe.
                            Daily = 252, hourly = 252*24, minute =
                            252*24*60 etc.
        risk_free_rate:     annualised, used in Sharpe + Sortino.

    Returns BacktestResult.

    Conventions:
        * Position changes execute at the NEXT bar's close.
        * No look-ahead: position[t] is applied to ret[t+1] = (p[t+1]-p[t])/p[t].
        * Commission + slippage are subtracted from the trade's PnL
          on entry AND exit.
        * Equity is the cumulative product of (1 + bar_return).
    """
    px = np.asarray(prices, dtype=float)
    if px.ndim != 1:
        raise ValueError("prices must be 1-D")
    n = px.size
    if n < 4:
        raise ValueError(f"need at least 4 bars to backtest; got {n}")

    # Resolve signal.
    if callable(signal):
        raw = signal(px)
    else:
        raw = signal
    pos = np.asarray(raw, dtype=float).flatten()
    if pos.size != n:
        raise ValueError(
            f"signal length {pos.size} != prices length {n}"
        )
    # Clip to {-1, 0, +1} so callers can't accidentally lever up.
    pos = np.clip(pos, -1, 1)

    # Bar-to-bar simple returns; ret[0] is 0 (no previous bar).
    ret = np.zeros(n, dtype=float)
    ret[1:] = (px[1:] - px[:-1]) / np.maximum(px[:-1], 1e-12)

    # Position[t] is the SIGNAL on close of bar t, applied to ret[t+1].
    # Shift so position[t+1] earns ret[t+1].
    pos_eff = np.zeros(n, dtype=float)
    pos_eff[1:] = pos[:-1]

    # Commission + slippage applied when position changes.
    pos_changes = np.abs(np.diff(np.concatenate([[0.0], pos_eff])))
    # Cost per change in fraction-of-equity terms; we treat it as a hit
    # to that bar's return.
    cost_per_change = (
        commission_per_trade / max(initial_capital, 1e-6)
        + (slippage_bps / 10000.0)
    )
    cost = pos_changes * cost_per_change

    bar_ret = pos_eff * ret - cost

    equity = np.empty(n, dtype=float)
    equity[0] = initial_capital
    for i in range(1, n):
        equity[i] = equity[i - 1] * (1.0 + bar_ret[i])

    # Drawdown.
    running_max = np.maximum.accumulate(equity)
    drawdown = equity - running_max
    drawdown_pct = drawdown / np.maximum(running_max, 1e-12)
    max_dd = float(drawdown.min()) if n else 0.0
    max_dd_pct = float(drawdown_pct.min()) if n else 0.0

    # Trade extraction — every contiguous run of non-zero position is one trade.
    trades: List[TradeRecord] = []
    i = 0
    while i < n:
        if pos_eff[i] == 0:
            i += 1
            continue
        direction = int(np.sign(pos_eff[i]))
        entry = i
        while i < n and np.sign(pos_eff[i]) == direction:
            i += 1
        exit_idx = i - 1
        entry_price = float(px[entry])
        exit_price  = float(px[exit_idx])
        pnl = direction * (exit_price - entry_price)
        pnl_pct = pnl / max(entry_price, 1e-12)
        trades.append(TradeRecord(
            entry_idx=int(entry), exit_idx=int(exit_idx),
            entry_price=entry_price, exit_price=exit_price,
            direction=direction,
            pnl=float(pnl), pnl_pct=float(pnl_pct),
            bars_held=int(exit_idx - entry + 1),
        ))

    n_trades = len(trades)
    pnls = np.array([t.pnl for t in trades], dtype=float) if trades else np.zeros(0)
    pcts = np.array([t.pnl_pct for t in trades], dtype=float) if trades else np.zeros(0)
    n_wins = int((pnls > 0).sum()) if trades else 0
    n_losses = int((pnls < 0).sum()) if trades else 0
    win_rate = (n_wins / n_trades) if n_trades else 0.0

    # Sharpe + Sortino (annualised).
    rf_per_bar = risk_free_rate / max(bars_per_year, 1)
    excess = bar_ret - rf_per_bar
    std = float(excess.std()) if n > 1 else 0.0
    sharpe = float(excess.mean() / std * math.sqrt(bars_per_year)) \
              if std > 1e-12 else None
    downside = excess[excess < 0]
    dstd = float(downside.std()) if downside.size > 1 else 0.0
    sortino = float(excess.mean() / dstd * math.sqrt(bars_per_year)) \
               if dstd > 1e-12 else None

    total_return = float(equity[-1] / equity[0] - 1.0)
    timespan_years = n / max(bars_per_year, 1)
    cagr = (
        float((equity[-1] / equity[0]) ** (1.0 / timespan_years) - 1.0)
        if timespan_years > 0.95
        else None
    )

    best = max(trades, key=lambda t: t.pnl).to_dict() if trades else None
    worst = min(trades, key=lambda t: t.pnl).to_dict() if trades else None

    return BacktestResult(
        n_bars=int(n),
        n_trades=int(n_trades),
        n_wins=int(n_wins),
        n_losses=int(n_losses),
        win_rate=float(win_rate),
        total_return=float(total_return),
        cagr=cagr,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        avg_trade_pnl=float(pnls.mean()) if trades else None,
        avg_trade_pct=float(pcts.mean()) if trades else None,
        best_trade=best,
        worst_trade=worst,
        equity_curve=[float(v) for v in equity],
        bar_returns=[float(v) for v in bar_ret],
        trades=[t.to_dict() for t in trades],
        config={
            "initial_capital": initial_capital,
            "commission_per_trade": commission_per_trade,
            "slippage_bps": slippage_bps,
            "bars_per_year": bars_per_year,
            "risk_free_rate": risk_free_rate,
        },
    )


# ----------------------------------------------------------------------
# v0.10.1 — convenience: build a signal from a column predicate
# ----------------------------------------------------------------------

def signal_from_threshold(
    series: Union[Sequence[float], "np.ndarray"],
    *,
    enter_long_above:  Optional[float] = None,
    exit_long_below:   Optional[float] = None,
    enter_short_below: Optional[float] = None,
    exit_short_above:  Optional[float] = None,
) -> List[int]:
    """Build a position-array from per-bar predicates.

    Useful for testing simple Aurora-driven rules like
    "go long when robust z-score < -2, exit when z > 0".
    """
    s = np.asarray(series, dtype=float)
    n = s.size
    pos = [0] * n
    cur = 0
    for i, v in enumerate(s):
        if cur == 0:
            if enter_long_above is not None and v > enter_long_above:
                cur = 1
            elif enter_short_below is not None and v < enter_short_below:
                cur = -1
        elif cur == 1:
            if exit_long_below is not None and v < exit_long_below:
                cur = 0
        elif cur == -1:
            if exit_short_above is not None and v > exit_short_above:
                cur = 0
        pos[i] = cur
    return pos
