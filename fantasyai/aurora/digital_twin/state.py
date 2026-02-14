from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any, Dict, Optional, List
import numpy as np
import pandas as pd


@dataclass
class AuroraState:
    """
    Digital-twin state object (generic).
    - x: latent state vector (k,)
    - P: uncertainty / covariance (k,k) (or diagonal if you want)
    - regime_id: optional discrete regime
    - constraints_ok: whether invariants were satisfied on last update
    - last_update_ts: last observed timestamp (if time-based)
    """
    x: np.ndarray
    P: np.ndarray
    regime_id: Optional[int] = None
    constraints_ok: bool = True
    last_update_ts: Optional[str] = None

    # Useful metadata for traceability
    target_col: Optional[str] = None
    time_col: Optional[str] = None
    feature_cols: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> Dict[str, Any]:
        d = asdict(self)
        d["x"] = self.x.tolist() if isinstance(self.x, np.ndarray) else self.x
        d["P"] = self.P.tolist() if isinstance(self.P, np.ndarray) else self.P
        return d

    @staticmethod
    def from_jsonable(d: Dict[str, Any]) -> "AuroraState":
        x = np.asarray(d.get("x", []), dtype=float)
        P = np.asarray(d.get("P", []), dtype=float)
        return AuroraState(
            x=x,
            P=P,
            regime_id=d.get("regime_id"),
            constraints_ok=bool(d.get("constraints_ok", True)),
            last_update_ts=d.get("last_update_ts"),
            target_col=d.get("target_col"),
            time_col=d.get("time_col"),
            feature_cols=list(d.get("feature_cols", [])),
            meta=dict(d.get("meta", {})),
        )


def build_initial_state_from_series(
    y: pd.Series,
    time_index: Optional[pd.Series] = None,
    k: int = 2,
    init_var: float = 1.0,
    target_col: Optional[str] = None,
    time_col: Optional[str] = None,
) -> AuroraState:
    """
    Builds a minimal state from a numeric target series.
    Default k=2: [level, velocity] style.
    """
    yv = pd.to_numeric(y, errors="coerce").dropna()
    if len(yv) == 0:
        # fallback: zero state
        x = np.zeros((k,), dtype=float)
    else:
        level = float(yv.iloc[-1])
        vel = 0.0
        if len(yv) >= 2:
            vel = float(yv.iloc[-1] - yv.iloc[-2])
        x = np.array([level, vel] + [0.0] * max(0, k - 2), dtype=float)

    P = np.eye(k, dtype=float) * float(init_var)

    last_ts = None
    if time_index is not None:
        try:
            last_ts = str(pd.to_datetime(time_index.iloc[-1]))
        except Exception:
            last_ts = str(time_index.iloc[-1])

    return AuroraState(
        x=x,
        P=P,
        last_update_ts=last_ts,
        target_col=target_col,
        time_col=time_col,
    )
