from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def _write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _safe_get(d: Dict[str, Any], *keys, default=None):
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _clamp_list(x: Any) -> List[float]:
    if not isinstance(x, list):
        return []
    out: List[float] = []
    for v in x:
        try:
            out.append(float(v))
        except Exception:
            out.append(float("nan"))
    return out


def _infer_numeric_cols(deep: Dict[str, Any]) -> List[str]:
    cols = deep.get("numeric_cols_used")
    if isinstance(cols, list) and all(isinstance(c, str) for c in cols):
        return cols
    # fallback: infer from corr bootstrap pairs
    top_corr = _safe_get(deep, "uncertainty", "top_corr_bootstrap", default=[]) or []
    s = set()
    for it in top_corr:
        a = it.get("a")
        b = it.get("b")
        if isinstance(a, str):
            s.add(a)
        if isinstance(b, str):
            s.add(b)
    return sorted(s)


def _build_sparse_corr_heatmap(
    top_corr: List[Dict[str, Any]],
    max_cols: int = 14,
) -> Optional[Dict[str, Any]]:
    """
    We may not have a full NxN matrix. Build a sparse heatmap using the columns
    that appear in the top correlations list.
    """
    if not top_corr:
        return None

    # pick cols by frequency, then limit
    freq: Dict[str, int] = {}
    for it in top_corr:
        a = it.get("a")
        b = it.get("b")
        if isinstance(a, str):
            freq[a] = freq.get(a, 0) + 1
        if isinstance(b, str):
            freq[b] = freq.get(b, 0) + 1

    cols = sorted(freq.keys(), key=lambda c: (-freq[c], c))[:max_cols]
    if len(cols) < 3:
        return None

    idx = {c: i for i, c in enumerate(cols)}
    n = len(cols)

    # initialize matrix with None; diagonal 1.0
    mat: List[List[Optional[float]]] = [[None for _ in range(n)] for _ in range(n)]
    for i in range(n):
        mat[i][i] = 1.0

    for it in top_corr:
        a = it.get("a")
        b = it.get("b")
        r = it.get("pearson_r")
        if not (isinstance(a, str) and isinstance(b, str)):
            continue
        if a not in idx or b not in idx:
            continue
        try:
            rv = float(r)
        except Exception:
            continue
        ia, ib = idx[a], idx[b]
        mat[ia][ib] = rv
        mat[ib][ia] = rv

    return {
        "kind": "heatmap_sparse",
        "labels": cols,
        "matrix": mat,
        "meta": {
            "note": "Sparse heatmap from top correlated pairs (missing cells are null).",
        },
    }


def build_plot_pack_v2(run_dir: Path) -> Dict[str, Any]:
    run_dir = Path(run_dir)
    if not run_dir.exists():
        raise FileNotFoundError(f"run_dir not found: {run_dir}")

    deep_path = run_dir / "deep_math.json"
    if not deep_path.exists():
        raise FileNotFoundError(f"deep_math.json not found in run_dir: {deep_path}")

    deep = _read_json(deep_path)

    plots_dir = run_dir / "plots"
    plots_data_dir = run_dir / "plots_data"
    plots_data_dir.mkdir(parents=True, exist_ok=True)

    numeric_cols = _infer_numeric_cols(deep)
    numeric_n = deep.get("numeric_cols_n")
    try:
        numeric_n = int(numeric_n) if numeric_n is not None else len(numeric_cols)
    except Exception:
        numeric_n = len(numeric_cols)

    # ---------- 0) Summary cards (always attempt) ----------
    cards: List[Dict[str, Any]] = []

    # forecasting card
    forecasting = _safe_get(deep, "forecasting", default={}) or {}
    f = _clamp_list(forecasting.get("forecast"))
    if f:
        trend = "flat"
        if f[-1] > f[0]:
            trend = "up"
        elif f[-1] < f[0]:
            trend = "down"
        cards.append(
            {
                "title": "Forecast trend",
                "value": trend,
                "detail": f"{f[0]:.2f} → {f[-1]:.2f} ({len(f)} steps)",
            }
        )

    # anomalies / tail risk card
    tail = _safe_get(deep, "tail_events", default={}) or {}
    tail_cnt = tail.get("count")
    if tail_cnt is not None:
        cards.append({"title": "Tail events", "value": str(tail_cnt), "detail": "Extreme deviations flagged"})

    # regimes card
    regimes = _safe_get(deep, "regimes", default={}) or {}
    rc = regimes.get("regime_count")
    if rc is not None:
        cards.append({"title": "Regimes", "value": str(rc), "detail": "Change points detected"})

    # top correlation card
    top_corr = _safe_get(deep, "uncertainty", "top_corr_bootstrap", default=[]) or []
    if top_corr:
        it0 = top_corr[0]
        pair = f"{it0.get('a')} vs {it0.get('b')}"
        r0 = it0.get("pearson_r")
        ci0 = it0.get("pearson_ci95") or [None, None]
        try:
            r0f = float(r0)
            if isinstance(ci0, list) and len(ci0) == 2 and all(c is not None for c in ci0):
                cards.append(
                    {
                        "title": "Top correlation",
                        "value": f"{r0f:.3f}",
                        "detail": f"{pair} (95% CI {float(ci0[0]):.3f} to {float(ci0[1]):.3f})",
                    }
                )
            else:
                cards.append({"title": "Top correlation", "value": f"{r0f:.3f}", "detail": pair})
        except Exception:
            pass

    _write_json(plots_data_dir / "summary_cards.json", {"kind": "cards", "cards": cards})

    # ---------- 1) Forecast with CI (domain agnostic) ----------
    horizon = forecasting.get("horizon")
    try:
        horizon = int(horizon) if horizon is not None else None
    except Exception:
        horizon = None

    lower = _clamp_list(forecasting.get("lower"))
    upper = _clamp_list(forecasting.get("upper"))
    if f and lower and upper:
        n = min(len(f), len(lower), len(upper))
        xs = list(range(1, n + 1))
        _write_json(
            plots_data_dir / "forecast_ci.json",
            {
                "kind": "line_ci",
                "x": xs,
                "series": {"yhat": f[:n], "y_lo": lower[:n], "y_hi": upper[:n]},
                "labels": {"x": "Horizon step", "y": "Value"},
                "meta": {
                    "method": forecasting.get("method"),
                    "alpha": forecasting.get("alpha"),
                },
            },
        )

    # ---------- 2) Risk surface bands (domain agnostic; looks NFL-ish) ----------
    rs = _safe_get(deep, "risk_surface", default={}) or {}
    bands = rs.get("bands") or {}
    p05 = _clamp_list(bands.get("p05"))
    p50 = _clamp_list(bands.get("p50"))
    p95 = _clamp_list(bands.get("p95"))
    if p05 and p50 and p95:
        n = min(len(p05), len(p50), len(p95))
        _write_json(
            plots_data_dir / "risk_surface_bands.json",
            {
                "kind": "bands",
                "x": list(range(1, n + 1)),
                "series": {"p05": p05[:n], "p50": p50[:n], "p95": p95[:n]},
                "labels": {"x": "Horizon step", "y": "Simulated value"},
                "meta": {
                    "method": rs.get("method"),
                    "n_sims": rs.get("n_sims"),
                    "terminal": rs.get("terminal"),
                },
            },
        )

    # ---------- 3) Correlation outputs (adaptive: 1 / 2 / many cols) ----------
    # Always produce a "top pairs" table with CI if present
    if top_corr:
        rows = []
        for it in top_corr:
            rows.append(
                {
                    "a": it.get("a"),
                    "b": it.get("b"),
                    "n": it.get("n"),
                    "pearson_r": it.get("pearson_r"),
                    "ci95": it.get("pearson_ci95"),
                    "bootstrap": it.get("bootstrap"),
                }
            )
        _write_json(
            plots_data_dir / "correlation_pairs_ci.json",
            {
                "kind": "pairs_ci",
                "rows": rows,
                "labels": {"x": "Pearson r", "y": "Pair"},
                "meta": {"numeric_cols_n": numeric_n},
            },
        )

    # If we have 3+ numeric cols, also produce a sparse heatmap (looks good in UI)
    if numeric_n >= 3 and top_corr:
        hm = _build_sparse_corr_heatmap(top_corr, max_cols=14)
        if hm:
            _write_json(plots_data_dir / "correlation_heatmap.json", hm)

    # ---------- 4) Regimes / change points ----------
    if regimes:
        _write_json(
            plots_data_dir / "regimes.json",
            {
                "kind": "regimes",
                "change_points": regimes.get("change_points", []),
                "regime_count": regimes.get("regime_count"),
                "stats": regimes.get("stats", []),
                "meta": {
                    "method": regimes.get("method"),
                    "penalty": regimes.get("penalty"),
                    "target_col": regimes.get("target_col"),
                },
            },
        )

    # ---------- 5) Data quality / missingness (if present) ----------
    miss = _safe_get(deep, "missingness", default={}) or {}
    if miss:
        _write_json(plots_data_dir / "missingness.json", {"kind": "missingness", **miss})

    # ---------- Update _PLOTS_INDEX.json (backward compatible) ----------
    plots_index_path = run_dir / "_PLOTS_INDEX.json"
    plots_index = _read_json(plots_index_path) if plots_index_path.exists() else {}

    # preserve existing fields
    plots_index.setdefault("plots_dir", str(plots_dir))
    plots_index.setdefault("files", [])

    plots_index["plots_data_dir"] = str(plots_data_dir)
    plots_index["plot_pack_version"] = "v2"

    plots_v2: List[Dict[str, Any]] = [
        {
            "id": "summary_cards",
            "title": "Summary",
            "kind": "cards",
            "data_file": "plots_data/summary_cards.json",
            "thumb_file": None,
        },
        {
            "id": "forecast_ci",
            "title": "Forecast with uncertainty",
            "kind": "line_ci",
            "data_file": "plots_data/forecast_ci.json",
            "thumb_file": "plots/forecast.png" if (plots_dir / "forecast.png").exists() else None,
        },
        {
            "id": "risk_surface_bands",
            "title": "Risk surface bands (p05 / p50 / p95)",
            "kind": "bands",
            "data_file": "plots_data/risk_surface_bands.json",
            "thumb_file": None,
        },
        {
            "id": "correlation_pairs_ci",
            "title": "Top correlation pairs (with CI)",
            "kind": "pairs_ci",
            "data_file": "plots_data/correlation_pairs_ci.json",
            "thumb_file": "plots/correlations.png" if (plots_dir / "correlations.png").exists() else None,
        },
        {
            "id": "correlation_heatmap",
            "title": "Correlation structure (sparse heatmap)",
            "kind": "heatmap_sparse",
            "data_file": "plots_data/correlation_heatmap.json",
            "thumb_file": None,
            "condition": {"min_numeric_cols": 3},
        },
        {
            "id": "regimes",
            "title": "Regimes / change points",
            "kind": "regimes",
            "data_file": "plots_data/regimes.json",
            "thumb_file": None,
        },
        {
            "id": "missingness",
            "title": "Missingness",
            "kind": "missingness",
            "data_file": "plots_data/missingness.json",
            "thumb_file": "plots/missingness.png" if (plots_dir / "missingness.png").exists() else None,
        },
    ]

    plots_index["plots_v2"] = plots_v2
    plots_index["plots_v2_meta"] = {
        "numeric_cols_used": numeric_cols,
        "numeric_cols_n": numeric_n,
    }

    _write_json(plots_index_path, plots_index)

    written = [p.name for p in sorted(plots_data_dir.glob("*.json"))]
    return {
        "ok": True,
        "run_dir": str(run_dir),
        "plots_index": str(plots_index_path),
        "plots_data_dir": str(plots_data_dir),
        "numeric_cols_n": numeric_n,
        "written": written,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()
    out = build_plot_pack_v2(Path(args.run_dir))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
