from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def _write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _safe_get(d: Dict[str, Any], *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def build_plot_pack(run_dir: Path) -> Dict[str, Any]:
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

    # --- 1) Forecast CI (clean payload) ---
    forecasting = _safe_get(deep, "forecasting", default={}) or {}
    horizon = int(forecasting.get("horizon") or len(forecasting.get("forecast", [])) or 0)
    forecast = forecasting.get("forecast") or []
    lower = forecasting.get("lower") or []
    upper = forecasting.get("upper") or []
    if horizon and forecast and lower and upper:
        _write_json(
            plots_data_dir / "forecast_ci.json",
            {
                "kind": "line_ci",
                "x": list(range(1, horizon + 1)),
                "series": {
                    "yhat": forecast,
                    "y_lo": lower,
                    "y_hi": upper,
                },
                "labels": {"x": "Horizon step", "y": "Value"},
                "meta": {
                    "method": forecasting.get("method"),
                    "alpha": forecasting.get("alpha"),
                    "phi": forecasting.get("phi"),
                    "sigma": forecasting.get("sigma"),
                },
            },
        )

    # --- 2) Risk surface bands (p05/p50/p95 over horizon) ---
    rs = _safe_get(deep, "risk_surface", default={}) or {}
    bands = rs.get("bands") or {}
    if bands.get("p05") and bands.get("p50") and bands.get("p95"):
        n = min(len(bands["p05"]), len(bands["p50"]), len(bands["p95"]))
        _write_json(
            plots_data_dir / "risk_surface_bands.json",
            {
                "kind": "bands",
                "x": list(range(1, n + 1)),
                "series": {
                    "p05": bands["p05"][:n],
                    "p50": bands["p50"][:n],
                    "p95": bands["p95"][:n],
                },
                "labels": {"x": "Horizon step", "y": "Simulated value"},
                "meta": {
                    "method": rs.get("method"),
                    "horizon": rs.get("horizon"),
                    "n_sims": rs.get("n_sims"),
                    "terminal": rs.get("terminal"),
                },
            },
        )

    # --- 3) Correlation w/ CI (works even for 2-column datasets) ---
    top_corr = _safe_get(deep, "uncertainty", "top_corr_bootstrap", default=[]) or []
    if top_corr:
        rows = []
        for item in top_corr:
            rows.append(
                {
                    "a": item.get("a"),
                    "b": item.get("b"),
                    "n": item.get("n"),
                    "pearson_r": item.get("pearson_r"),
                    "ci95": item.get("pearson_ci95"),
                    "bootstrap": item.get("bootstrap"),
                }
            )
        _write_json(
            plots_data_dir / "correlation_ci.json",
            {
                "kind": "table_ci",
                "rows": rows,
                "labels": {"x": "Correlation (Pearson r)", "y": "Pair"},
                "note": "If only one pair exists, the UI should render this as a single ‘estimate + CI’ pill or mini-bar.",
            },
        )

    # --- 4) Regimes (change points + regime stats) ---
    regimes = _safe_get(deep, "regimes", default={}) or {}
    if regimes.get("change_points") is not None:
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

    # --- 5) Summary cards (Scale-style top row) ---
    cards = []
    # forecasting trend
    if forecast:
        trend = "down" if forecast[-1] < forecast[0] else "up" if forecast[-1] > forecast[0] else "flat"
        cards.append({"title": "Forecast trend", "value": trend, "detail": f"{forecast[0]:.2f} → {forecast[-1]:.2f}"})

    # corr
    if top_corr:
        r0 = top_corr[0].get("pearson_r")
        ci0 = top_corr[0].get("pearson_ci95") or [None, None]
        pair = f"{top_corr[0].get('a')} vs {top_corr[0].get('b')}"
        if r0 is not None:
            cards.append(
                {
                    "title": "Top correlation",
                    "value": f"{r0:.3f}",
                    "detail": f"{pair} (95% CI {ci0[0]:.3f} to {ci0[1]:.3f})" if None not in ci0 else pair,
                }
            )

    # causal what-if
    cw = _safe_get(deep, "causal_what_if", default={}) or {}
    if cw.get("estimated_effect") is not None:
        cards.append(
            {
                "title": "What-if effect",
                "value": f"{cw['estimated_effect']:.3f}",
                "detail": f"+{cw.get('delta', 1.0)} {cw.get('treatment_col')} → {cw.get('target_col')}",
            }
        )

    _write_json(plots_data_dir / "summary_cards.json", {"kind": "cards", "cards": cards})

    # --- Upgrade/extend _PLOTS_INDEX.json (don’t break existing PNG UI) ---
    plots_index_path = run_dir / "_PLOTS_INDEX.json"
    plots_index = _read_json(plots_index_path) if plots_index_path.exists() else {"plots_dir": str(plots_dir), "files": []}

    # Keep original fields for backward compatibility
    plots_index["plots_data_dir"] = str(plots_data_dir)

    # New: UI-friendly registry
    plots_index["plots_v2"] = [
        {
            "id": "forecast_ci",
            "title": "Forecast with uncertainty",
            "kind": "line_ci",
            "data_file": str(plots_data_dir / "forecast_ci.json"),
            "thumb_file": str(plots_dir / "forecast.png") if (plots_dir / "forecast.png").exists() else None,
        },
        {
            "id": "risk_surface_bands",
            "title": "Risk surface (p05/p50/p95)",
            "kind": "bands",
            "data_file": str(plots_data_dir / "risk_surface_bands.json"),
            "thumb_file": None,
        },
        {
            "id": "correlation_ci",
            "title": "Correlation with confidence interval",
            "kind": "table_ci",
            "data_file": str(plots_data_dir / "correlation_ci.json"),
            "thumb_file": str(plots_dir / "correlations.png") if (plots_dir / "correlations.png").exists() else None,
        },
        {
            "id": "regimes",
            "title": "Detected regimes",
            "kind": "regimes",
            "data_file": str(plots_data_dir / "regimes.json"),
            "thumb_file": None,
        },
        {
            "id": "summary_cards",
            "title": "Summary cards",
            "kind": "cards",
            "data_file": str(plots_data_dir / "summary_cards.json"),
            "thumb_file": None,
        },
    ]

    _write_json(plots_index_path, plots_index)

    return {
        "ok": True,
        "run_dir": str(run_dir),
        "plots_index": str(plots_index_path),
        "plots_data_dir": str(plots_data_dir),
        "written": [str(p) for p in sorted(plots_data_dir.glob("*.json"))],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()

    out = build_plot_pack(Path(args.run_dir))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
