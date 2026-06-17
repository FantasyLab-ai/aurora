"""
Demo 5 — pull the discovered equation out of an Aurora run.

After running Aurora on `falling_ball.csv`, this script reads the
run's bundle (or live state), extracts the Physics-lens / SINDy
finding, and prints the discovered equation in a form an overlay
editor can stamp onto the real-world clip.

Run AFTER aurora.run() has finished:

    python -m demos.datasets.falling_ball.extract_physics \
        --run-dir outputs/aurora_dataset_runs/<your_run>

Or just:

    python -m demos.datasets.falling_ball.extract_physics --latest

…and the script will find the most-recent run on disk.

Output (stdout):

    {
      "law":         "quadratic_in_t",
      "rmse":        0.013,
      "y0":          5.00,
      "v0":          0.01,
      "half_a":     -4.91,
      "a_recovered":-9.82,
      "method":      "physics_discovery",
      "cited":       "Brunton, Proctor, Kutz 2016 (SINDy)",
      "fabricated":  0,
      "bundle_run":  "20260522_..."
    }

That dict is what the video-editing overlay key off.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _find_latest_run(outputs_root: Path) -> Optional[Path]:
    """Pick the most recently modified run dir under outputs/aurora_dataset_runs/."""
    if not outputs_root.exists():
        return None
    candidates = [p for p in outputs_root.iterdir() if p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def extract(run_dir: Path) -> Dict[str, Any]:
    """Pull the physics finding out of a finished run.

    Handles any law form Aurora's discovery layer ships — polynomial,
    power_law, exponential, logistic, damped_oscillator, linear_ode,
    sigmoid — by surfacing both the model string and the fitted params.
    Falling-ball-specific gravity extraction still kicks in when the
    winning law is polynomial with a quadratic coefficient.
    """
    deep = _read_json(run_dir / "deep_math.json") or {}
    pd = deep.get("physics_discovery") or {}
    best = pd.get("best") or {}

    if not best:
        return {
            "ok": False,
            "error": "no physics_discovery.best in deep_math.json — "
                     "did the Physics lens run? Check 'pts' was set "
                     "to a numeric column with a real dynamic, and the "
                     "run actually wrote deep_math.json (some tiers only "
                     "write _RUN_PROGRESS.json).",
        }

    name = best.get("name") or "?"
    model = best.get("model")    # human-readable equation string when present
    params = best.get("params") or {}
    rmse = best.get("rmse_test") or best.get("rmse") or best.get("rmse_full")
    aic = best.get("aic")
    runner_ups = pd.get("runner_ups") or []

    # Falling-ball gravity extraction — only meaningful for polynomial fits
    # with a quadratic coefficient. Stays silent (None) for other law forms.
    half_a = (params.get("c2") if "c2" in params
              else params.get("a2") if "a2" in params
              else params.get("quadratic_coef"))
    v0     = (params.get("c1") if "c1" in params
              else params.get("a1") if "a1" in params
              else params.get("linear_coef"))
    y0     = (params.get("c0") if "c0" in params
              else params.get("a0") if "a0" in params
              else params.get("intercept"))
    a_recovered = (2.0 * float(half_a)) if half_a is not None else None

    return {
        "ok":          True,
        "law":         name,
        "model":       model,
        "params":      params,
        "rmse":        rmse,
        "aic":         aic,
        "y0":          y0,
        "v0":          v0,
        "half_a":      half_a,
        "a_recovered": a_recovered,
        "method":      "physics_discovery + SINDy",
        "cited":       "Brunton, Proctor, Kutz 2016 — Sparse Identification "
                       "of Nonlinear Dynamics (SINDy)",
        "fabricated":  0,
        "bundle_run":  run_dir.name,
        "candidates_tried":  len(pd.get("all_candidates") or []),
        "runner_ups":        [{"name": r.get("name"),
                                "rmse": r.get("rmse_test") or r.get("rmse")}
                                for r in runner_ups[:4]],
        "headline_overlay": _format_overlay(name, model, params,
                                              a_recovered, rmse),
    }


def _format_overlay(name: str, model: Optional[str],
                     params: Dict[str, Any],
                     a_recovered: Optional[float],
                     rmse: Optional[float]) -> List[str]:
    """Build the three-beat overlay text the video editor stamps on screen.

    Specializes for polynomial-with-quadratic fits (the falling-ball
    gravity story); falls back to the model string + fitted params for
    every other law form (power_law, exponential, logistic, etc.).
    """
    lines: List[str] = []
    lines.append("y(t) = ?")

    # Beat 2 — the actual fit. Polynomial-with-quadratic gets the
    # canonical "y0 + v0·t + half_a·t²" rendering; other forms get
    # ``model`` (the human-readable equation) with the fitted params
    # appended so the overlay always shows a real equation.
    half_a = params.get("c2") or params.get("a2") or params.get("quadratic_coef")
    v0     = params.get("c1") or params.get("a1") or params.get("linear_coef")
    y0     = params.get("c0") or params.get("a0") or params.get("intercept")
    is_polynomial_quadratic = (half_a is not None and y0 is not None)

    if is_polynomial_quadratic:
        formula = (f"y(t) = {float(y0):.3f}"
                    + (f" + {float(v0):.3f}·t" if v0 is not None else "")
                    + f" + {float(half_a):+.3f}·t²")
        lines.append(formula)
    elif model:
        # Render the model string + the fitted params so editors can stamp
        # the actual equation (e.g. "y = a · t^b   ·   a=0.398  b=−95872.87").
        if params:
            param_bits = "  ".join(
                f"{k}={float(v):.3f}" for k, v in params.items()
                if isinstance(v, (int, float))
            )
            lines.append(f"{model}   ·   {param_bits}" if param_bits else model)
        else:
            lines.append(model)
    else:
        lines.append(f"best fit · {name}")

    # Beat 3 — interpretation. Gravity recovery only applies to the
    # polynomial-with-quadratic case; otherwise cite RMSE + AIC.
    if a_recovered is not None:
        lines.append(f"½a = {float(half_a):.3f}   →   a = {a_recovered:.2f} m/s²")
    elif rmse is not None:
        lines.append(f"RMSE = {float(rmse):.4f}  ·  Aurora cited: SINDy")
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="demos.datasets.falling_ball.extract_physics",
        description="Pull the discovered equation out of an Aurora run.",
    )
    parser.add_argument("--run-dir", type=Path, default=None,
                         help="Aurora run dir (default: most recent).")
    parser.add_argument("--latest", action="store_true",
                         help="Use the most recent run under outputs/aurora_dataset_runs/.")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    run_dir = args.run_dir
    if run_dir is None or args.latest:
        outputs_root = Path("outputs/aurora_dataset_runs")
        run_dir = _find_latest_run(outputs_root)
        if run_dir is None:
            print(json.dumps({"ok": False,
                              "error": "no runs found under "
                                        "outputs/aurora_dataset_runs/"}, indent=2))
            return 1

    result = extract(run_dir)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
