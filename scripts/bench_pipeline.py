"""
Aurora Pipeline Benchmark Harness — pure measurement, zero production-code changes.

Purpose
=======
Produce ground truth on where time and memory are actually spent in the
Aurora analysis pipeline across dataset sizes (1K, 10K, 100K, 1M, 10M
rows). The output (CSV + Markdown) informs prioritization of the
post-launch performance overhaul (DuckDB ingest, streaming progressive
results, per-method budgets, content-hash caching, etc.).

This harness does NOT modify any production code. It:

  * Imports the existing pipeline modules and runs them on synthesized /
    bootstrapped datasets at each size.
  * Wraps the full pipeline invocation via subprocess + psutil polling
    so the total runtime and peak RSS are measured honestly.
  * Re-imports the advanced-pass methods and times them in-process for
    per-method granularity (a separate measurement phase; does not
    perturb the main subprocess timing).
  * Writes structured CSV + human-readable Markdown reports to
    ``outputs/benchmarks/``.

Constraints (per the task brief)
================================
  * No production code path is modified or instrumented.
  * Honest measurement — no warm-cache cheating, no skipping stages.
    Each subprocess runs the same code path the UI triggers at AUTO depth.
  * Bootstrap synthesis from real curated datasets only — no purely
    random data, because methods like SINDy and HMM behave very
    differently on noise vs. structured data.
  * Skip 10M if it would exceed reasonable bench budget (>30 min or
    OOM); log the skip and continue.

Usage
=====
::

    python scripts/bench_pipeline.py \\
        --sizes 1k,10k,100k,1m,10m \\
        --runs-per-size 3 \\
        --out outputs/benchmarks/

    # Quick sanity check:
    python scripts/bench_pipeline.py --sizes 1k,10k --runs-per-size 1

Output files (timestamped)
==========================
  * ``outputs/benchmarks/bench_<ts>.csv``   — per-stage / per-method rows
  * ``outputs/benchmarks/bench_<ts>.md``    — human-readable summary

CSV columns
-----------
  ``dataset_size_rows, dataset_columns, run_index, phase, stage_name,
  method_name, duration_ms, peak_memory_mb, result_status, skip_reason,
  sample_size_used``

  ``phase`` is one of:
    * ``pipeline``   — total subprocess wall time (one row per run)
    * ``method``     — per-method advanced-pass timing
    * ``ingest``     — CSV file read time (in-process)
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# We import these lazily inside _bench_methods_in_process so the
# top-level import of the harness stays fast and the harness itself
# never crashes at import time on missing optional deps.
PYTHON = sys.executable
RUNNER_MODULE = "scripts.run_aurora_with_steps_1_4"
DEFAULT_OUT = ROOT / "outputs" / "benchmarks"

# Synthesis sources — per the brief, bootstrap from real curated
# datasets so methods see realistic structure (time axis, column types,
# value distributions).
SYNTH_SOURCES: Dict[str, Path] = {
    "1k":   ROOT / "data" / "fixtures" / "factory_bearing_demo.csv",   # 1000 rows
    "10k":  ROOT / "docs" / "captures" / "2026-05-05" / "after" / "data" / "noaa_buoy_2024.csv",  # 8760 rows
    "100k": ROOT / "data" / "uploads" / "20260508_132034__diabetic_data.csv",  # ~101k rows
}

# Per-size soft budgets — if a single subprocess exceeds the budget the
# harness times it out and logs the skip. Keeps long-running 10M from
# wedging an overnight bench.
SIZE_TIMEOUT_SECONDS: Dict[str, int] = {
    "1k":   180,    # 3 min
    "10k":  600,    # 10 min
    "100k": 1800,   # 30 min
    "1m":   3600,   # 60 min  — bench budget cap
    "10m":  1800,   # 30 min  — if it doesn't fit in this, mark skipped
}

# The advanced-pass methods we instrument per-call. Read from
# ``fantasyai.aurora.stats.advanced_pass`` at run time (lazy import) so
# the harness doesn't blow up if advanced_pass evolves.
ADVANCED_METHOD_NAMES: List[str] = [
    "_maybe_sindy", "_maybe_hmm", "_maybe_mutual_info", "_maybe_granger",
    "_maybe_wavelet", "_maybe_lomb_scargle", "_maybe_gp", "_maybe_topology",
    "_maybe_compositional", "_maybe_power", "_maybe_cluster_stability",
    "_maybe_bayesian", "_maybe_panel", "_maybe_survival",
    "_maybe_spatial", "_maybe_network", "_maybe_did",
]


# ============================================================
# Dataset synthesis — bootstrap real curated CSVs to target size
# ============================================================

def synthesize_dataset(target_size_key: str, dest: Path, seed: int = 42) -> Tuple[int, int]:
    """Create a CSV at ``dest`` with ~``target_size_key`` rows.

    Strategy (per brief): take the closest real curated dataset and
    row-bootstrap WITH a small jitter on a numeric column to break
    exact-row repetition (so robust-z doesn't see literal duplicates
    that would saturate MAD).

    For sizes ≤ source rows we slice the head. For sizes > source rows
    we sample with replacement and add 1-σ Gaussian jitter on numeric
    columns to preserve distribution shape while keeping the rows
    distinct (still realistic, since real measurements include noise).

    Returns ``(rows, cols)`` of the written file.
    """
    import pandas as pd
    import numpy as np

    target_rows = {
        "1k":   1_000,
        "10k":  10_000,
        "100k": 100_000,
        "1m":   1_000_000,
        "10m":  10_000_000,
    }.get(target_size_key)
    if target_rows is None:
        raise ValueError(f"unknown size key: {target_size_key}")

    # Choose the source closest to the target.
    source_key = (
        "100k" if target_rows >= 100_000 else
        "10k"  if target_rows >= 10_000  else
        "1k"
    )
    src = SYNTH_SOURCES.get(source_key)
    if not src or not src.exists():
        raise FileNotFoundError(f"synthesis source not found for {source_key}: {src}")

    df = pd.read_csv(src)
    n_src = len(df)
    rng = np.random.default_rng(seed)

    if target_rows <= n_src:
        # Slice (preserves the time-axis if there is one).
        out_df = df.head(target_rows).copy()
    else:
        # Bootstrap with replacement + per-column jitter.
        idx = rng.integers(0, n_src, size=target_rows)
        out_df = df.iloc[idx].reset_index(drop=True).copy()
        # Jitter numeric columns by 1% of std to break literal repeats
        # without distorting the distribution.
        numeric_cols = out_df.select_dtypes(include=[np.number]).columns
        for c in numeric_cols:
            s = out_df[c].astype(float)
            sigma = float(s.std(skipna=True)) or 0.0
            if sigma > 0:
                out_df[c] = s + rng.normal(0.0, sigma * 0.01, size=len(s))
        # If there's a time-like column at index 0 with monotone
        # ordering, re-sort by it so the bootstrap doesn't break the
        # time axis. Best-effort — never crash here.
        first_col = out_df.columns[0]
        try:
            parsed = pd.to_datetime(out_df[first_col], errors="coerce")
            if parsed.notna().sum() > len(out_df) * 0.9:
                out_df = out_df.assign(_t=parsed).sort_values("_t").drop(columns=["_t"]).reset_index(drop=True)
        except Exception:
            pass

    dest.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(dest, index=False)
    return len(out_df), len(out_df.columns)


# ============================================================
# Subprocess timing with peak-RSS polling
# ============================================================

def run_pipeline_subprocess(dataset_path: Path,
                              timeout_seconds: int,
                              poll_interval: float = 0.5,
                              ) -> Dict[str, Any]:
    """Invoke the production runner via subprocess; poll RSS each tick.

    Returns ``{returncode, duration_ms, peak_memory_mb, stdout_tail,
    stderr_tail, run_dir, timed_out}`` — never raises.

    Implementation note: we use temp files for stdout/stderr rather
    than ``subprocess.PIPE`` because the inner runner subprocess emits
    a wall of numpy RuntimeWarnings to stderr (divide-by-zero, overflow
    in scalar multiply, etc.). With PIPE the OS-level pipe buffer
    (~64KB on most platforms) fills, the writer BLOCKS on write, and
    the subprocess deadlocks waiting for us to drain. Using temp files
    lets the kernel grow the buffer arbitrarily so the subprocess never
    stalls on output. Verified on factory_bearing_demo: 180s timeout
    via PIPE → 3.5s real runtime via temp files.
    """
    import psutil
    import tempfile

    cmd = [
        PYTHON, "-m", RUNNER_MODULE,
        "--dataset", str(dataset_path),
        "--seed", "42",
        "--also-math", "--math-v2", "--deep-math", "--also-llm",
    ]
    t0 = time.perf_counter()
    timed_out = False
    peak_rss_bytes = 0

    stdout_fp = tempfile.NamedTemporaryFile(
        prefix="aurora_bench_stdout_", suffix=".log",
        delete=False, mode="w+b",
    )
    stderr_fp = tempfile.NamedTemporaryFile(
        prefix="aurora_bench_stderr_", suffix=".log",
        delete=False, mode="w+b",
    )
    stdout_path = Path(stdout_fp.name)
    stderr_path = Path(stderr_fp.name)
    stdout_fp.close()
    stderr_fp.close()

    try:
        proc = subprocess.Popen(
            cmd, cwd=str(ROOT),
            stdout=open(stdout_path, "wb"),
            stderr=open(stderr_path, "wb"),
        )
    except Exception as e:
        return {
            "returncode": -1, "duration_ms": 0, "peak_memory_mb": 0,
            "stdout_tail": "", "stderr_tail": f"spawn failed: {e}",
            "run_dir": None, "timed_out": False,
        }

    try:
        ps_proc = psutil.Process(proc.pid)
    except psutil.NoSuchProcess:
        ps_proc = None

    while proc.poll() is None:
        elapsed = time.perf_counter() - t0
        if elapsed > timeout_seconds:
            timed_out = True
            try:
                proc.kill()
            except Exception:
                pass
            break
        # Sum RSS across the subprocess tree — the runner spawns an
        # inner runner subprocess, so the actual peak lives in a child.
        try:
            if ps_proc is not None and ps_proc.is_running():
                rss = ps_proc.memory_info().rss
                for child in ps_proc.children(recursive=True):
                    try:
                        rss += child.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                peak_rss_bytes = max(peak_rss_bytes, rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        time.sleep(poll_interval)

    # Wait for the subprocess to finish flushing if it's still alive.
    try:
        proc.wait(timeout=10)
    except Exception:
        pass

    # Drain the log files.
    try:
        stdout_buf = stdout_path.read_bytes()
    except Exception:
        stdout_buf = b""
    try:
        stderr_buf = stderr_path.read_bytes()
    except Exception:
        stderr_buf = b""
    # Cleanup tempfiles.
    for p in (stdout_path, stderr_path):
        try:
            p.unlink()
        except Exception:
            pass

    duration_ms = int((time.perf_counter() - t0) * 1000)
    rc = proc.returncode if proc.returncode is not None else -1

    # Parse the runner's stdout for the produced run_dir (the runner
    # emits a JSON summary on stdout).
    run_dir: Optional[str] = None
    try:
        text = stdout_buf.decode("utf-8", errors="replace")
        i = text.rfind("{")
        if i >= 0:
            parsed = json.loads(text[i:])
            run_dir = parsed.get("run_dir")
    except Exception:
        pass

    return {
        "returncode": rc,
        "duration_ms": duration_ms,
        "peak_memory_mb": round(peak_rss_bytes / (1024 * 1024), 1),
        "stdout_tail": stdout_buf[-2000:].decode("utf-8", errors="replace"),
        "stderr_tail": stderr_buf[-2000:].decode("utf-8", errors="replace"),
        "run_dir": run_dir,
        "timed_out": timed_out,
    }


# ============================================================
# Per-method timing — in-process re-call of advanced-pass methods
# ============================================================

def bench_methods_in_process(dataset_path: Path) -> List[Dict[str, Any]]:
    """Time each advanced-pass method on the same DataFrame.

    Importing+calling in this harness process is a SEPARATE phase from
    the subprocess timing — it produces per-method numbers without
    perturbing the main pipeline measurement. The state dict passed to
    each method is built best-effort from the CSV; methods that don't
    find the inputs they expect will return ``{applied: False, ...}``
    and be recorded as ``skipped`` rather than ``error``.

    Returns one row per method.
    """
    import pandas as pd
    import numpy as np
    rows: List[Dict[str, Any]] = []

    try:
        df = pd.read_csv(dataset_path)
    except Exception as e:
        rows.append({
            "stage_name": "ingest_for_method_bench", "method_name": "",
            "duration_ms": 0, "peak_memory_mb": 0,
            "result_status": "error", "skip_reason": f"csv_read_failed: {e}",
            "sample_size_used": 0,
        })
        return rows

    # Build a minimal state dict that the methods expect. The methods
    # tolerate missing keys (they all check and short-circuit), so this
    # gives an honest measurement of the work each method does on a
    # well-formed input.
    numeric_cols = list(df.select_dtypes(include=[np.number]).columns)[:30]
    arr = df[numeric_cols].to_numpy(dtype=float) if numeric_cols else np.zeros((len(df), 0))
    # Time axis: try the first column.
    has_time = False
    time_col = None
    primary_times: List[float] = []
    if len(df.columns):
        first = df.columns[0]
        try:
            ts = pd.to_datetime(df[first], errors="coerce")
            if ts.notna().sum() > len(df) * 0.9:
                has_time = True
                time_col = first
                primary_times = [(t - ts.min()).total_seconds() for t in ts.dropna()]
        except Exception:
            pass

    primary_series: List[float] = []
    if numeric_cols:
        primary_series = df[numeric_cols[0]].astype(float).fillna(0.0).tolist()

    state: Dict[str, Any] = {
        "structure": {"time_axis": has_time, "time_col": time_col},
        "dataset_matrix": arr.tolist(),
        "dataset_matrix_columns": numeric_cols,
        "dataset_dt": 1.0,
        "primary_times": primary_times[: len(primary_series)],
        "primary_series": primary_series,
    }

    try:
        from fantasyai.aurora.stats import advanced_pass as _ap
    except Exception as e:
        rows.append({
            "stage_name": "method", "method_name": "(import)",
            "duration_ms": 0, "peak_memory_mb": 0,
            "result_status": "error",
            "skip_reason": f"advanced_pass_import_failed: {e}",
            "sample_size_used": 0,
        })
        return rows

    for name in ADVANCED_METHOD_NAMES:
        fn = getattr(_ap, name, None)
        if fn is None:
            rows.append({
                "stage_name": "method", "method_name": name,
                "duration_ms": 0, "peak_memory_mb": 0,
                "result_status": "skipped",
                "skip_reason": "method_not_found", "sample_size_used": 0,
            })
            continue
        t0 = time.perf_counter()
        status = "ok"
        skip_reason: Optional[str] = None
        try:
            res = fn(state)
            dur_ms = int((time.perf_counter() - t0) * 1000)
            if isinstance(res, dict) and res.get("applied") is False:
                status = "skipped"
                skip_reason = res.get("reason") or "method_skipped"
        except Exception as e:
            dur_ms = int((time.perf_counter() - t0) * 1000)
            status = "error"
            skip_reason = f"{type(e).__name__}: {str(e)[:200]}"
            # Don't log full traceback; the harness should not be noisy.
        rows.append({
            "stage_name": "method", "method_name": name,
            "duration_ms": dur_ms, "peak_memory_mb": 0,
            "result_status": status,
            "skip_reason": skip_reason or "",
            "sample_size_used": len(df),
        })
    return rows


# ============================================================
# Ingest-phase timing — separate measurement
# ============================================================

def bench_ingest(dataset_path: Path) -> Dict[str, Any]:
    """Time the pure ``pd.read_csv`` call. Reported separately because
    ingest is often the single biggest stage at large dataset sizes."""
    import pandas as pd
    t0 = time.perf_counter()
    try:
        df = pd.read_csv(dataset_path)
        status = "ok"; reason = ""; n_rows = len(df); n_cols = len(df.columns)
    except Exception as e:
        status = "error"; reason = f"{type(e).__name__}: {e}"
        n_rows = n_cols = 0
    return {
        "stage_name": "ingest", "method_name": "",
        "duration_ms": int((time.perf_counter() - t0) * 1000),
        "peak_memory_mb": 0,
        "result_status": status,
        "skip_reason": reason,
        "sample_size_used": n_rows,
        "_rows": n_rows, "_cols": n_cols,
    }


# ============================================================
# Result aggregation — CSV + Markdown
# ============================================================

CSV_HEADERS = [
    "dataset_size_rows", "dataset_columns", "run_index",
    "phase", "stage_name", "method_name",
    "duration_ms", "peak_memory_mb", "result_status",
    "skip_reason", "sample_size_used",
]


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_markdown(path: Path, rows: List[Dict[str, Any]],
                    sizes_run: List[str], runs_per_size: int) -> None:
    """Human-readable summary report."""
    # Group rows by (size, phase, stage/method).
    by_size: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        by_size.setdefault(r["dataset_size_rows"], []).append(r)

    lines: List[str] = []
    lines.append(f"# Aurora Pipeline Benchmark Report\n")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n")
    lines.append(f"Sizes: {', '.join(sizes_run)}  ·  runs/size: {runs_per_size}\n")
    lines.append("")
    lines.append("This report is honest measurement of the CURRENT pipeline. ")
    lines.append("Methods that timed out, skipped, or errored are shown — no cherry-picking.\n")
    lines.append("")

    sorted_sizes = sorted(by_size.keys())
    for size_rows in sorted_sizes:
        size_label = _human_size(size_rows)
        recs = by_size[size_rows]
        # Pipeline total wall time — average across runs.
        pipeline_recs = [r for r in recs if r["phase"] == "pipeline"]
        if pipeline_recs:
            durs = [r["duration_ms"] for r in pipeline_recs if r["result_status"] == "ok"]
            peaks = [r["peak_memory_mb"] for r in pipeline_recs if r["result_status"] == "ok"]
            avg_ms = (sum(durs) / len(durs)) if durs else None
            max_mb = max(peaks) if peaks else None
            timed_out = sum(1 for r in pipeline_recs if r["skip_reason"] == "timed_out")
        else:
            avg_ms = max_mb = None; timed_out = 0

        lines.append(f"## {size_label} rows\n")
        if avg_ms is not None:
            lines.append(f"- **Total pipeline runtime (avg over {len(durs)} runs):** "
                         f"{_fmt_ms(avg_ms)}")
        if max_mb is not None:
            lines.append(f"- **Peak memory (max across runs):** {max_mb:.1f} MB")
        if timed_out:
            lines.append(f"- **Timed out:** {timed_out} run(s)")
        # Stage breakdown — ingest separate.
        ingest_rows = [r for r in recs if r["phase"] == "ingest" and r["result_status"] == "ok"]
        if ingest_rows:
            avg_in = sum(r["duration_ms"] for r in ingest_rows) / len(ingest_rows)
            lines.append(f"- **Ingest (pd.read_csv) avg:** {_fmt_ms(avg_in)}")
        lines.append("")

        # Top slow methods
        method_rows = [r for r in recs if r["phase"] == "method"]
        # Average duration per method across runs.
        method_agg: Dict[str, List[float]] = {}
        method_status: Dict[str, str] = {}
        method_reason: Dict[str, str] = {}
        for r in method_rows:
            method_agg.setdefault(r["method_name"], []).append(r["duration_ms"])
            method_status[r["method_name"]] = r["result_status"]
            method_reason[r["method_name"]] = r["skip_reason"]
        avg_method = sorted(
            [(name, sum(d) / len(d), method_status[name], method_reason[name])
             for name, d in method_agg.items()],
            key=lambda t: -t[1],
        )
        if avg_method:
            lines.append("### Top 5 slowest advanced-pass methods\n")
            lines.append("| Method | Avg duration | Status | Reason |")
            lines.append("|---|---:|---|---|")
            for name, dur, status, reason in avg_method[:5]:
                lines.append(f"| `{name}` | {_fmt_ms(dur)} | {status} | "
                             f"{reason or '—'} |")
            lines.append("")

        # Methods that always skip/error
        skipped = [(name, status, reason)
                   for name, _, status, reason in avg_method
                   if status in ("skipped", "error")]
        if skipped:
            lines.append("### Methods that skipped or errored\n")
            lines.append("| Method | Status | Reason |")
            lines.append("|---|---|---|")
            for name, status, reason in skipped:
                lines.append(f"| `{name}` | {status} | {reason or '—'} |")
            lines.append("")

    # Scaling factor table — pipeline duration vs dataset size.
    lines.append("## Scaling — pipeline total vs dataset size\n")
    lines.append("Ratio of avg pipeline runtime at size N vs the next-smaller size. ")
    lines.append("Linear scaling = ~10× per 10× rows; super-linear = bigger; ")
    lines.append("sub-linear = smaller (e.g. due to sampling, caching).\n")
    pipeline_summary: List[Tuple[int, float]] = []
    for size_rows in sorted_sizes:
        recs = [r for r in by_size[size_rows]
                if r["phase"] == "pipeline" and r["result_status"] == "ok"]
        if recs:
            avg = sum(r["duration_ms"] for r in recs) / len(recs)
            pipeline_summary.append((size_rows, avg))
    if pipeline_summary:
        lines.append("| Rows | Avg pipeline | Ratio vs prev |")
        lines.append("|---:|---:|---:|")
        prev: Optional[float] = None
        for rows_n, avg in pipeline_summary:
            ratio = f"{avg / prev:.2f}×" if prev else "—"
            lines.append(f"| {rows_n:,} | {_fmt_ms(avg)} | {ratio} |")
            prev = avg
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _human_size(n: int) -> str:
    if n >= 1_000_000: return f"{n // 1_000_000}M"
    if n >= 1_000:     return f"{n // 1_000}K"
    return str(n)


def _fmt_ms(ms: float) -> str:
    if ms >= 60_000:
        return f"{ms / 60_000:.2f} min"
    if ms >= 1000:
        return f"{ms / 1000:.2f} s"
    return f"{int(ms)} ms"


# ============================================================
# Main
# ============================================================

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Aurora pipeline benchmark harness (measurement only)",
    )
    ap.add_argument("--sizes", default="1k,10k",
                     help="Comma-separated sizes (1k,10k,100k,1m,10m)")
    ap.add_argument("--runs-per-size", type=int, default=1,
                     help="Number of full-pipeline runs per size for averaging")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                     help="Directory to write CSV + MD")
    ap.add_argument("--keep-synth", action="store_true",
                     help="Don't delete the synthesized CSV after the bench")
    args = ap.parse_args(argv)

    sizes = [s.strip().lower() for s in args.sizes.split(",") if s.strip()]
    valid_sizes = {"1k", "10k", "100k", "1m", "10m"}
    bad = [s for s in sizes if s not in valid_sizes]
    if bad:
        print(f"Unknown sizes: {bad}; valid: {sorted(valid_sizes)}",
              file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"bench_{ts}.csv"
    md_path  = out_dir / f"bench_{ts}.md"
    synth_dir = out_dir / "_synth"
    synth_dir.mkdir(parents=True, exist_ok=True)

    all_rows: List[Dict[str, Any]] = []
    sizes_run: List[str] = []

    for size_key in sizes:
        # Synthesize the dataset.
        synth_path = synth_dir / f"bench_{size_key}.csv"
        print(f"\n[{size_key}] synthesizing dataset -> {synth_path.name}")
        try:
            n_rows, n_cols = synthesize_dataset(size_key, synth_path)
        except Exception as e:
            print(f"  synthesis failed: {e}")
            all_rows.append({
                "dataset_size_rows": 0, "dataset_columns": 0,
                "run_index": 0, "phase": "synthesis", "stage_name": size_key,
                "method_name": "",
                "duration_ms": 0, "peak_memory_mb": 0,
                "result_status": "error",
                "skip_reason": f"synthesis_failed: {e}",
                "sample_size_used": 0,
            })
            continue
        print(f"  {n_rows:,} rows × {n_cols} cols")
        sizes_run.append(size_key)
        timeout = SIZE_TIMEOUT_SECONDS.get(size_key, 1800)

        # Ingest timing (one-shot — same for every run of this size).
        ingest_rec = bench_ingest(synth_path)
        all_rows.append({
            "dataset_size_rows": n_rows, "dataset_columns": n_cols,
            "run_index": 0, "phase": "ingest", **ingest_rec,
        })
        print(f"  [ingest]   {_fmt_ms(ingest_rec['duration_ms'])}")

        # Per-method in-process timing — one-shot per size.
        print(f"  [methods]  timing {len(ADVANCED_METHOD_NAMES)} advanced-pass methods")
        method_recs = bench_methods_in_process(synth_path)
        for mr in method_recs:
            all_rows.append({
                "dataset_size_rows": n_rows, "dataset_columns": n_cols,
                "run_index": 0, "phase": "method", **mr,
            })

        # Full-pipeline subprocess runs — averaged over args.runs_per_size.
        for run_i in range(args.runs_per_size):
            print(f"  [pipeline] run {run_i + 1}/{args.runs_per_size} (timeout {timeout}s)")
            t_start = time.perf_counter()
            res = run_pipeline_subprocess(synth_path, timeout_seconds=timeout)
            status = "ok" if res["returncode"] == 0 and not res["timed_out"] \
                     else ("timed_out" if res["timed_out"] else "error")
            skip_reason = (
                "timed_out" if res["timed_out"]
                else (res["stderr_tail"][-300:] if res["returncode"] != 0 else "")
            )
            all_rows.append({
                "dataset_size_rows": n_rows, "dataset_columns": n_cols,
                "run_index": run_i + 1, "phase": "pipeline",
                "stage_name": "subprocess_total", "method_name": "",
                "duration_ms": res["duration_ms"],
                "peak_memory_mb": res["peak_memory_mb"],
                "result_status": status,
                "skip_reason": skip_reason,
                "sample_size_used": n_rows,
            })
            elapsed = time.perf_counter() - t_start
            print(f"    {status}  ·  {_fmt_ms(res['duration_ms'])}  ·  "
                  f"peak {res['peak_memory_mb']:.0f} MB  "
                  f"(wall {elapsed:.1f}s)")

        # Write CSV + MD after each size so partial results survive a crash.
        write_csv(csv_path, all_rows)
        write_markdown(md_path, all_rows, sizes_run, args.runs_per_size)

    print(f"\nDone. CSV: {csv_path}")
    print(f"      MD : {md_path}")

    # Cleanup synthesized CSVs by default — they can be huge at 1M+.
    if not args.keep_synth:
        try:
            shutil.rmtree(synth_dir)
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
