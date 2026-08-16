"""
Phantom Signal — hard constraints govern actions; calibration governs
inferences.

A constraint checker can prove an action is LEGAL. It cannot prove the
reason for the action is REAL. This demo walks one such failure, end to
end, with Aurora's production changepoint detector and the shipped
calibration corpus:

  Beat 1  A seeded, stationary demand series. The production CUSUM
          detector fires anyway. A simulated autonomous system
          reallocates stock, and every hard constraint passes.
  Beat 2  The ground truth: the series was generated with no changepoint.
          The reallocation was triggered by nothing.
  Beat 3  The same detection, through Aurora's calibration engine: the
          verdict downgrades to not-identifiable, with the measured
          false-fire rate, the matched regime, the remediation, and a
          signed, hashed bundle anyone can verify.

Fully seeded — same output every run, on every machine. Runs in seconds:
the calibration corpus is precomputed and shipped, never built here.

Run from the repo root:

    python -m demos.phantom_signal.run_demo
    python -m demos.phantom_signal.run_demo --fast   # no dramatic pauses

Honesty constraints this demo observes (see README.md):
  * Every rate printed is read from the shipped, hash-verified corpus.
  * The logistics scenario is labelled SIMULATED; no volume-of-decisions
    or dollar figures are invented anywhere.
  * No claim is made about any named company, vendor, or product. This
    demonstrates a failure class in detector calibration, nothing more.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Windows consoles default to cp1252, which cannot encode the box-drawing
# glyphs below. Reconfigure to UTF-8 with replacement so the demo renders
# everywhere instead of crashing on its first frame.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ---------------------------------------------------------------- cosmetics
RESET = "\x1b[0m"
DIM   = "\x1b[2m"
BOLD  = "\x1b[1m"
MINT  = "\x1b[38;5;121m"
CYAN  = "\x1b[38;5;87m"
AMBER = "\x1b[38;5;215m"
CRIT  = "\x1b[38;5;203m"
GREY  = "\x1b[38;5;240m"

_FAST = False


def _line(s: str = "", color: str = "") -> None:
    sys.stdout.write(color + s + (RESET if color else "") + "\n")
    sys.stdout.flush()


def _pause(seconds: float) -> None:
    if not _FAST:
        time.sleep(seconds)


# ---------------------------------------------------------------- the data
# One seeded draw from the calibration engine's own AR(1) null generator:
# phi=0.6 (unremarkable for daily demand), n=90, TRIAL selects the draw.
# Ground truth by construction: stationary, no changepoint. The affine
# map to demand units changes presentation only — CUSUM and the regime
# fingerprint are affine-invariant, so detection and calibration see the
# identical series.
PHI = 0.6
N_DAYS = 90
TRIAL = 12
DEMAND_MEAN = 140.0
DEMAND_SD = 12.0
SKU = "SKU-8871"

# A public, deterministic Ed25519 demo key (sha256 of a fixed string).
# It proves the signing MECHANICS, not an identity — a key anyone can
# derive authenticates nobody, and the demo says so on screen.
DEMO_KEY_SEED = b"aurora-phantom-signal-demo-key-v1"


def build_series():
    import numpy as np
    from fantasyai.aurora.calibration import generate_cell_trial
    x = generate_cell_trial("AR1", {"phi": PHI}, N_DAYS, TRIAL).values
    return np.round(DEMAND_MEAN + DEMAND_SD * x, 4)


# ------------------------------------------------------------- beat one
def beat_one(demand) -> Dict[str, Any]:
    import pandas as pd
    from fantasyai.aurora.math.regimes import detect_regimes_worldclass

    _line()
    _line("┏━━ BEAT 1 · THE DECISION ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", CYAN)
    _line("┃", CYAN)
    _line(f"┃  {N_DAYS} days of daily demand for {SKU} arrive at a", CYAN)
    _line("┃  simulated autonomous replenishment system.", CYAN)
    _line("┃", CYAN)
    _pause(0.8)

    df = pd.DataFrame({"demand": demand})
    regimes = detect_regimes_worldclass(df, "demand")   # production path
    cps = regimes.get("changepoints_cusum") or []
    if not cps:
        raise RuntimeError(
            "expected the production CUSUM detector to fire on this seeded "
            "draw — the pinned-values generator test should have caught this"
        )
    day = int(cps[0])
    steps = regimes.get("step_changes") or []
    step = next((s for s in steps if s.get("idx") == day), steps[0] if steps else {})

    _line(f"┃  detector      CUSUM mean-shift (production path)", CYAN)
    _line(f"┃  finding       changepoint at day {day}", CYAN)
    if step:
        _line(
            f"┃  step          mean {step['mean_before']:.1f} → "
            f"{step['mean_after']:.1f} units/day "
            f"({step['delta_in_sd']:+.2f} sd)", CYAN,
        )
    _line("┃", CYAN)
    _line("┃  The system drafts a reallocation: move 900 units of", CYAN)
    _line(f"┃  {SKU} from DC Reno to DC Atlanta ahead of the 'new", CYAN)
    _line("┃  demand regime'. Hard constraint check (SIMULATED network):", CYAN)
    _line("┃", CYAN)
    _pause(0.8)

    # The simulated network state. Every check below is computed, not
    # asserted — the point is that the action is genuinely legal.
    network = {
        "reno_before": 4200, "atlanta_before": 2200, "move": 900,
        "atlanta_capacity": 5000, "lead_time_days": 4, "sla_days": 7,
        "reallocation_cap": 1000,
    }
    reno_after = network["reno_before"] - network["move"]
    atlanta_after = network["atlanta_before"] + network["move"]
    checks = [
        ("conservation",
         f"{network['reno_before']} = {reno_after} + {network['move']}",
         network["reno_before"] == reno_after + network["move"]),
        ("non-negative",
         f"Reno {reno_after}, Atlanta {atlanta_after} >= 0",
         reno_after >= 0 and atlanta_after >= 0),
        ("DC capacity",
         f"Atlanta {atlanta_after}/{network['atlanta_capacity']}",
         atlanta_after <= network["atlanta_capacity"]),
        ("lead time",
         f"{network['lead_time_days']}d <= {network['sla_days']}d SLA",
         network["lead_time_days"] <= network["sla_days"]),
        ("reallocation cap",
         f"{network['move']} <= {network['reallocation_cap']} threshold",
         network["move"] <= network["reallocation_cap"]),
    ]
    all_pass = True
    for name, detail, ok in checks:
        all_pass = all_pass and ok
        _line(f"┃    {name:<18} {detail:<34} {'PASS' if ok else 'FAIL'}",
              MINT if ok else CRIT)
    _line("┃    " + "─" * 55, GREY)
    verdict = "EXECUTE — no escalation" if all_pass else "BLOCKED"
    _line(f"┃    DECISION: {verdict}", BOLD + (MINT if all_pass else CRIT))
    _line("┃", CYAN)
    _line("┃  Every constraint holds. The action is legal.", CYAN)
    _line("┗" + "━" * 62, CYAN)
    _pause(1.2)
    return {"day": day, "step": step, "regimes": regimes,
            "network": network, "all_pass": all_pass}


# ------------------------------------------------------------- beat two
def beat_two() -> None:
    from fantasyai.aurora.calibration import BASE_SEED
    _line()
    _line("┏━━ BEAT 2 · THE GROUND TRUTH ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", AMBER)
    _line("┃", AMBER)
    _line("┃  That series was generated by this demo, seeded, from a", AMBER)
    _line("┃  stationary AR(1) null process:", AMBER)
    _line("┃", AMBER)
    _line(f"┃    generator   AR1 v1.0.0, phi={PHI}, n={N_DAYS}", AMBER)
    _line(f"┃    seed        base {BASE_SEED}, trial {TRIAL} "
          f"(SeedSequence + sha256 cell key)", AMBER)
    _line("┃    truth       NO changepoint. Nothing happened on any day.", AMBER)
    _line("┃", AMBER)
    _line("┃  The reallocation was triggered by nothing. Autocorrelated", AMBER)
    _line("┃  wander looked like a regime shift to a detector that", AMBER)
    _line("┃  assumes independent observations — and no constraint", AMBER)
    _line("┃  checker can catch a legal action taken for a wrong reason.", AMBER)
    _line("┗" + "━" * 62, AMBER)
    _pause(1.2)


# ----------------------------------------------------------- beat three
def beat_three(demand, det: Dict[str, Any], out_dir: Path) -> Dict[str, Any]:
    from fantasyai.aurora.calibration import (
        apply_calibration_to_finding, load_corpus,
    )

    corpus = load_corpus("v1")  # hash-verified on load

    day = det["day"]
    step = det["step"]
    finding: Dict[str, Any] = {
        "severity": "warn",
        "confidence": 0.7,
        "title": f"CUSUM: changepoint in '{SKU}' demand at day {day}",
        "description": (
            f"CUSUM mean-shift detection fired at day {day}"
            + (f" (mean {step['mean_before']:.1f} → {step['mean_after']:.1f}, "
               f"{step['delta_in_sd']:+.2f} sd)." if step else ".")
        ),
        "method": "cusum",
        "actions": ["VIEW EVIDENCE"],
        "kind_hint": "regime_change",
        "claim_id": "cusum-0000",
        "fabricated": False,
        "evidence": {
            "status": "fit",
            "target_col": "demand",
            "detector": "cusum_mean_shift",
            "threshold_sigma_units": 8.0,
            "changepoints": [day],
            "step_changes": [step] if step else [],
        },
    }

    # The calibration engine: fingerprint the actual series, look up the
    # measured false-fire rate, downgrade the verdict if the data can't
    # support the claim.
    apply_calibration_to_finding(finding, demand, corpus=corpus)
    cal = finding["calibration"]

    _line()
    _line("┏━━ BEAT 3 · THE CALIBRATED FINDING ━━━━━━━━━━━━━━━━━━━━━━━━━", MINT)
    _line("┃", MINT)
    _line("┃  Same detection, through Aurora with calibration active:", MINT)
    _line("┃", MINT)
    _line(f"┃  finding      changepoint detected, {SKU}, day {day}", MINT)
    _line("┃  method       cusum (production path, threshold 8.0)", MINT)
    verdict = finding.get("verdict", "(unchanged)")
    _line(f"┃  verdict      {verdict.upper().replace('_', ' ')}",
          BOLD + (CRIT if verdict != "(unchanged)" else MINT))
    _line("┃", MINT)
    obs = cal["observed_regime"]; match = cal["matched_regime"]
    _line(f"┃  status       {cal['status']}", MINT)
    _line(f"┃  measured     fires on {cal['empirical_fdr']:.1%} of stationary "
          f"null series", MINT)
    _line(f"┃               with these properties "
          f"(95% CI {cal['ci95'][0]:.1%}–{cal['ci95'][1]:.1%}, "
          f"{cal['trials']} seeded trials)", MINT)
    _line(f"┃  regime       observed phi_hat={obs['phi_hat']:.2f}, n={obs['n']}  "
          f"→ matched grid phi_hat={match['phi_hat']:.2f}, n={match['n']}", MINT)
    _line(f"┃  distance     {cal['regime_distance']['phi_hat']:.3f} in phi_hat "
          "(reported, never hidden)", MINT)
    if cal.get("fdr_vs_iid") is not None:
        _line(f"┃  vs IID       {cal['fdr_vs_iid']}x the measured independent-"
              f"data rate", MINT)
    elif cal.get("fdr_vs_iid_note"):
        _line(f"┃  vs IID       {cal['fdr_vs_iid_note']}", MINT)
    _line(f"┃  ceiling      verdict downgrades above "
          f"{cal['fdr_ceiling']:.0%} (configured, disclosed)", MINT)
    _line("┃", MINT)
    for rem in cal.get("remediation", []):
        _line(f"┃  remediate    {rem['action']}:", MINT)
        for chunk in _wrap(rem["detail"], 52):
            _line(f"┃                 {chunk}", DIM + MINT)
    _line("┃", MINT)
    _line("┃  The raw statistic is still reported above — Aurora does", MINT)
    _line("┃  not hide what it computed. It contextualises it.", MINT)
    _line("┗" + "━" * 62, MINT)
    _pause(1.0)

    # ---- The verifiable artifact --------------------------------------
    bundle_doc = _emit_bundle(demand, finding, det, corpus, out_dir)
    return {"finding": finding, "bundle": bundle_doc}


def _wrap(text: str, width: int) -> List[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def _emit_bundle(demand, finding, det, corpus, out_dir: Path) -> Dict[str, Any]:
    import pandas as pd
    from aurora_sdk.bundle import Bundle, build_bundle_from_state

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "phantom_signal_demand.csv"
    pd.DataFrame({"day": range(N_DAYS), "demand": demand}).to_csv(
        csv_path, index=False, lineterminator="\n",
    )

    state = {
        "run_meta": {
            "run_id": "phantom-signal-demo",
            # Fixed timestamps: the bundle's content hash covers the run
            # block, and this demo promises identical output every run.
            "started_at": "2026-08-15T00:00:00",
            "completed_at": "2026-08-15T00:00:00",
            "state": "completed",
            "tier": "demo",
        },
        "dataset": {"name": "phantom_signal_demand", "rows": N_DAYS, "cols": 2},
        "findings": [finding],
        "regimes": det["regimes"],
        "synthesis": {
            "assumptions": [
                f"Series is a seeded draw from the AR1 v1.0.0 null generator "
                f"(phi={PHI}, n={N_DAYS}, trial {TRIAL}); ground truth contains "
                "no changepoint.",
                "The logistics network in beat 1 is simulated; its figures "
                "illustrate constraint checking and describe no real system.",
            ],
        },
    }
    doc = build_bundle_from_state(state, dataset_path=csv_path)

    bundle = Bundle(doc)
    signed = False
    try:
        bundle.sign(hashlib.sha256(DEMO_KEY_SEED).digest())
        signed = True
    except RuntimeError:
        pass  # cryptography not installed — bundle ships hashed, unsigned

    bundle_path = out_dir / "phantom_signal_bundle.json"
    bundle.save(bundle_path)
    bundle.verify(require_signature=signed)

    integ = bundle.doc["integrity"]
    _line()
    _line("┏━━ THE ARTIFACT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", GREY)
    _line(f"┃  bundle        {bundle_path}", GREY)
    _line(f"┃  content hash  sha256:{integ['content_hash'][:32]}…", GREY)
    _line(f"┃  corpus        {finding['calibration']['corpus_version']} "
          f"sha256:{finding['calibration']['corpus_sha256'][:32]}…", GREY)
    if signed:
        _line(f"┃  signature     ed25519, public key "
              f"{integ['public_key'][:24]}… (deterministic DEMO key —", GREY)
        _line("┃                proves signing mechanics, not identity)", GREY)
    _line(f"┃  dataset       {csv_path.name} "
          f"sha256:{(bundle.doc['dataset']['sha256'] or '')[:32]}…", GREY)
    _line("┃  verify        aurora_sdk.bundle.verify_bundle(...)  → OK", GREY)
    _line("┗" + "━" * 62, GREY)
    return bundle.doc


# ------------------------------------------------------------- FDR table
def print_fdr_table() -> None:
    """The measured rates behind this demo, read from the shipped corpus."""
    from fantasyai.aurora.calibration import load_corpus
    corpus = load_corpus("v1")
    rows = []
    for e in corpus["entries"]:
        if e["method"] != "cusum" or e["n"] != N_DAYS:
            continue
        if e["generator"] == "IIDNormal":
            rows.append((0.0, e))
        elif e["generator"] == "AR1":
            rows.append((float(e["params"]["phi"]), e))
    rows.sort()
    _line()
    _line(f"  Measured false-fire rates — CUSUM, n={N_DAYS}, "
          f"{corpus['trials_per_cell']} seeded null trials per cell:", DIM)
    _line()
    _line(f"  {'phi':>5} | {'fires on stationary data':>25} | {'95% CI':>17}")
    _line("  " + "-" * 55)
    for phi, e in rows:
        ci = f"{e['ci95'][0]:.1%}–{e['ci95'][1]:.1%}"
        _line(f"  {phi:>5.1f} | {e['fdr']:>24.1%} | {ci:>17}")
    _line()


# -------------------------------------------------------------------- main
def main(argv=None) -> int:
    global _FAST
    ap = argparse.ArgumentParser(description="Phantom Signal calibration demo")
    ap.add_argument("--fast", action="store_true", help="skip dramatic pauses")
    ap.add_argument("--out", default=None, help="artifact output directory")
    args = ap.parse_args(argv)
    _FAST = bool(args.fast)
    out_dir = Path(args.out) if args.out else Path(__file__).parent / "out"

    t0 = time.time()
    _line()
    _line("  PHANTOM SIGNAL — hard constraints govern actions;", BOLD)
    _line("  calibration governs inferences.", BOLD)
    _line("  (seeded end to end; same output on every machine)", DIM)

    demand = build_series()
    det = beat_one(demand)
    beat_two()
    result = beat_three(demand, det, out_dir)
    print_fdr_table()

    verdict = result["finding"].get("verdict", "")
    _line(f"  Done in {time.time() - t0:.1f}s. The action was legal. "
          f"The reason was {verdict.upper().replace('_', ' ') or 'UNCHANGED'}.",
          BOLD)
    _line()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
