#!/usr/bin/env python3
"""
Benchmark sweep — turns single runs into a result you could defend.

One run of one scenario with one seed tells you nothing: a small model's
sampling noise alone moves the outcome by several percent. This harness runs
every arm across many randomised scenario instances and reports means with
confidence intervals, so "the LLM beat the heuristic" becomes a claim with an
error bar attached instead of a screenshot.

Arms
----
  heuristic   the deterministic fallback policy alone (--offline). The control.
  <model>     one arm per Ollama model tag passed on the command line.

Every arm sees the SAME set of scenario instances (same seeds), so the
comparison is paired — the variance between instances cancels out rather than
swamping the difference between methods.

Headline metric is allocation efficiency against the LP optimum from oracle.py:
locally-served demand as a fraction of the most any method could have served.
It is bounded, comparable across scenarios, and it is the number that decides
whether this research direction is real.

Usage
-----
    # control arm only — no Ollama needed, runs in seconds
    python benchmark.py --seeds 20

    # compare two local models against the control
    python benchmark.py --models llama3.2:1b qwen2.5:3b --seeds 10

    # the discriminating configuration (see --help for why)
    python benchmark.py --models llama3.2:1b --scenario contended --jitter 0.4 --seeds 20
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from typing import Optional

import microgrid_sim as sim


# =============================================================================
# Aggregation
# =============================================================================

def summarise_run(results: list[dict], agents: dict, tel: sim.Telemetry) -> dict:
    """Reduce one simulation to the handful of numbers worth comparing."""
    demand = sum(r["demand_kwh"] for r in results)
    unmet = sum(r["utility_import_kwh"] for r in results)
    curtailed = sum(r["curtailed_kwh"] for r in results)
    generation = sum(r["generation_kwh"] for r in results)
    traded = sum(r["traded_kwh"] for r in results)

    opt_unmet = [r.get("optimal_utility_import_kwh") for r in results]
    efficiency = None
    if all(o is not None for o in opt_unmet):
        opt_served = demand - sum(opt_unmet)
        if opt_served > 1e-6:
            efficiency = 100.0 * (demand - unmet) / opt_served

    prices = [r["avg_price"] for r in results if r["avg_price"] > 0]
    return {
        "allocation_efficiency_pct": efficiency,
        "utility_import_kwh": unmet,
        "curtailed_kwh": curtailed,
        "traded_kwh": traded,
        "local_coverage_pct": 100.0 * (1 - unmet / demand) if demand else 100.0,
        "self_consumption_pct": 100.0 * (1 - curtailed / generation) if generation else 0.0,
        "avg_price": statistics.fmean(prices) if prices else 0.0,
        "structured_compliance_pct": 100.0 * tel.structured_compliance,
        "autonomy_pct": 100.0 * tel.autonomy_rate,
        "clamps": float(tel.clamped_values),
        "llm_calls": float(tel.llm_calls),
        "llm_failures": float(tel.llm_failures),
        "mean_latency_s": (tel.latency_s / tel.llm_calls) if tel.llm_calls else 0.0,
        "seconds_per_block": tel.feasibility()["seconds_per_block"],
        "tokens_per_block": tel.feasibility()["tokens_per_block"],
        # Marks the control arm so LLM-only rows can be suppressed when no model ran.
        "offline": 1.0 if tel.json_ok + tel.json_salvaged == 0 and tel.llm_calls else 0.0,
    }


def mean_ci(values: list[float], confidence: float = 0.95) -> tuple[float, float]:
    """
    Mean and half-width of a normal-approximation confidence interval.

    With fewer than ~15 samples the normal approximation understates the
    interval; the sweep prints the sample count alongside so a thin run is
    visible as such rather than being quoted as if it were solid.
    """
    clean = [v for v in values if v is not None]
    if not clean:
        return float("nan"), float("nan")
    if len(clean) == 1:
        return clean[0], 0.0
    z = 1.96 if confidence == 0.95 else 2.576
    return statistics.fmean(clean), z * statistics.stdev(clean) / math.sqrt(len(clean))


# =============================================================================
# Sweep
# =============================================================================

def run_arm(label: str, seeds: list[int], args: argparse.Namespace,
            offline: bool, model: str) -> list[dict]:
    """Run one arm across every seed, returning a per-seed summary list."""
    rows = []
    console = sim.Console(color=False, quiet=True)
    for i, seed in enumerate(seeds, start=1):
        started = time.time()
        results, agents, tel, meta = sim.run_simulation(
            scenario=args.scenario, model=model, url=args.url, blocks=args.blocks,
            battery_soc=args.battery_soc, offline=offline, seed=seed,
            jitter=args.jitter, timeout=args.timeout, trace_path=args.trace,
            nodes=args.nodes, topology=args.topology, start_hour=args.start_hour,
            prompt_style=args.prompt_style, adversary=args.adversary,
            adversary_fraction=args.adversary_fraction, console=console)
        row = summarise_run(results, agents, tel)
        row.update({"arm": label, "seed": seed, "wall_s": time.time() - started})
        rows.append(row)
        print(f"    {label:<16} seed {seed:>3}  "
              f"efficiency {row['allocation_efficiency_pct']:6.1f}%  "
              f"({row['wall_s']:5.1f}s)", flush=True)
    return rows


METRICS = [
    ("allocation_efficiency_pct", "allocation efficiency", "%", True),
    ("local_coverage_pct", "demand served locally", "%", True),
    ("utility_import_kwh", "utility import", "kWh", False),
    ("curtailed_kwh", "clean energy curtailed", "kWh", False),
    ("self_consumption_pct", "solar self-consumption", "%", True),
    ("avg_price", "mean clearing price", "tok", None),
    ("structured_compliance_pct", "structured compliance", "%", True),
    ("autonomy_pct", "decision autonomy", "%", True),
    ("clamps", "physics clamps", "n", False),
    ("mean_latency_s", "mean call latency", "s", False),
    ("seconds_per_block", "wall clock per block", "s", False),
    ("tokens_per_block", "tokens per block", "n", False),
]


def print_comparison(arms: dict[str, list[dict]], control: str) -> None:
    """Print each metric per arm as mean ± 95% CI, with a delta vs the control."""
    print("\n" + "=" * 86)
    print("  BENCHMARK RESULT   (mean ± 95% CI over paired scenario instances)")
    print("=" * 86)

    names = list(arms)
    width = max(len(n) for n in names) + 2
    for key, label, unit, higher_better in METRICS:
        # Skip LLM-only metrics when nothing but the control arm ran.
        if key in ("structured_compliance_pct", "autonomy_pct", "mean_latency_s",
                   "clamps", "seconds_per_block", "tokens_per_block") \
                and all(r["offline"] for n in names for r in arms[n]):
            continue
        print(f"\n  {label}  [{unit}]")
        base, _ = mean_ci([r[key] for r in arms[control]]) if control in arms else (None, None)
        for name in names:
            values = [r[key] for r in arms[name]]
            mean, ci = mean_ci(values)
            line = f"    {name:<{width}} {mean:8.2f}  ± {ci:5.2f}   (n={len(values)})"
            if base is not None and name != control and higher_better is not None:
                delta = mean - base
                better = (delta > 0) == higher_better
                # A difference smaller than the control's own CI is not a result.
                mark = "  <- inside the noise" if abs(delta) < ci else (
                    "  ** better" if better else "  ** worse")
                line += f"   Δ {delta:+7.2f}{mark}"
            print(line)
    print("\n" + "=" * 86)
    print("  Reading this: a Δ smaller than the confidence interval is not evidence of")
    print("  anything. Raise --seeds until the interval is smaller than the effect you")
    print("  are claiming, or accept that there is no effect to claim.")
    print("=" * 86 + "\n")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Paired benchmark sweep for the MALO micro-grid.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog="Note: on the 'brief' scenario with no jitter the greedy control arm "
               "already scores ~99% of optimum, so no method can show a meaningful "
               "advantage there. Use --scenario contended --jitter 0.4 for instances "
               "where allocation quality actually varies.")
    p.add_argument("--models", nargs="*", default=[],
                   help="Ollama model tags to benchmark against the control arm")
    p.add_argument("--seeds", type=int, default=10, help="number of scenario instances")
    p.add_argument("--seed-start", type=int, default=1, help="first seed value")
    p.add_argument("--scenario", default="contended",
                   help="brief, contended, or a generated preset: street, block, feeder, district")
    p.add_argument("--nodes", type=int, help="override node count for generated presets")
    p.add_argument("--topology", choices=sim.scen.TOPOLOGIES, help="override topology")
    p.add_argument("--start-hour", type=int, default=15)
    p.add_argument("--prompt-style", choices=sim.PROMPT_STYLES, default="full",
                   help="prompt ablation to run this sweep under")
    p.add_argument("--adversary", choices=sim.BEHAVIOURS, default="honest")
    p.add_argument("--adversary-fraction", type=float, default=0.0)
    p.add_argument("--blocks", type=int, default=3)
    p.add_argument("--jitter", type=float, default=0.4,
                   help="scenario randomisation; 0 replays one fixed instance")
    p.add_argument("--battery-soc", type=float, default=6.0)
    p.add_argument("--url", default=sim.OLLAMA_URL_DEFAULT)
    p.add_argument("--timeout", type=int, default=sim.LLM_TIMEOUT_S)
    p.add_argument("--trace", metavar="PATH",
                   help="append every prompt/response pair to a JSONL file")
    p.add_argument("--outdir", default="runs", help="where to write benchmark_raw.csv")
    p.add_argument("--no-control", action="store_true",
                   help="skip the heuristic control arm (not recommended)")
    args = p.parse_args(argv)

    seeds = list(range(args.seed_start, args.seed_start + args.seeds))
    print(f"\nscenario={args.scenario}  blocks={args.blocks}  jitter={args.jitter}  "
          f"prompt={args.prompt_style}  adversary={args.adversary}"
          f"@{args.adversary_fraction:g}  "
          f"instances={len(seeds)}  arms={'heuristic ' if not args.no_control else ''}"
          f"{' '.join(args.models)}\n")

    arms: dict[str, list[dict]] = {}
    if not args.no_control:
        arms["heuristic"] = run_arm("heuristic", seeds, args, offline=True, model="none")
    for model in args.models:
        arms[model] = run_arm(model, seeds, args, offline=False, model=model)

    if not arms:
        print("nothing to run: pass --models or drop --no-control", file=sys.stderr)
        return 2

    control = "heuristic" if "heuristic" in arms else next(iter(arms))
    print_comparison(arms, control)

    os.makedirs(args.outdir, exist_ok=True)
    path = os.path.join(args.outdir, "benchmark_raw.csv")
    rows = [r for arm in arms.values() for r in arm]
    try:
        import pandas as pd
        pd.DataFrame(rows).to_csv(path, index=False)
    except ImportError:
        import csv
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=sorted({k for r in rows for k in r}))
            w.writeheader()
            w.writerows(rows)
    print(f"raw per-instance results: {path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
