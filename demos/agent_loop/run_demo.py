"""
Demo 4 — Verification Cortex.

An agent calls Aurora's MCP, chains analyze → findings(severity=crit)
→ explain(claim_id=...), then takes a (printed) action on the
verified number. We show:

  1. A "naive baseline" first — a raw LLM-style invented z-score
     (we just print a deliberately wrong value) for visual contrast.
  2. Aurora's cited answer — pulled directly from the MCP tool surface,
     with method + claim_id + the actual statistic on a known row.

The point is the contrast. The naive line is the kind of confidently-
wrong number agents emit today. Aurora's line is the cited fix.

Run:

    python -m demos.agent_loop.run_demo \
        --dataset data/fixtures/factory_bearing_demo.csv

Set the contract+overlay running in another terminal first if you want
the on-screen card to fire when the verified finding lands.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


# Cosmetic helpers — keep the terminal output cinematic.
RESET   = "\x1b[0m"
DIM     = "\x1b[2m"
BOLD    = "\x1b[1m"
MINT    = "\x1b[38;5;121m"
CYAN    = "\x1b[38;5;87m"
AMBER   = "\x1b[38;5;215m"
CRIT    = "\x1b[38;5;203m"
GREY    = "\x1b[38;5;240m"


def _line(s: str = "", color: str = "") -> None:
    """Print one terminal line — colour optional."""
    sys.stdout.write(color + s + (RESET if color else "") + "\n")
    sys.stdout.flush()


def _pause(seconds: float) -> None:
    time.sleep(seconds)


def _naive_baseline(dataset: Path) -> Dict[str, Any]:
    """Pretend to be a vanilla LLM. Emit a confidently-wrong number."""
    _line()
    _line("┏━━ NAIVE AGENT (no Aurora) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", GREY)
    _line("┃", GREY)
    _line(f"┃  Q: scan {dataset.name} — give me the biggest anomaly.", GREY)
    _line("┃", GREY)
    _pause(0.4)
    _line("┃  → [thinking...]", GREY)
    _pause(0.6)
    invented = {
        "method":   "iso-forest",
        "row":      237,
        "z":        4.21,
        "value":    1.83,
        "claim_id": "a9d2",
    }
    _line("┃", GREY)
    _line(f"┃  ANSWER (no citation):", AMBER)
    _line(f"┃    \"The biggest anomaly is at row {invented['row']}", AMBER)
    _line(f"┃     with a z-score of {invented['z']:.2f}σ.\"", AMBER)
    _line("┃", GREY)
    _line(f"┃  {DIM}(plausible. wrong. unverifiable.){RESET}", GREY)
    _line("┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", GREY)
    return invented


def _aurora_verified(dataset: Path) -> Optional[Dict[str, Any]]:
    """Run Aurora's MCP chain (analyze → findings → explain) and
    return the verified finding. The whole flow uses the same code
    paths the MCP server exposes to LLM clients."""
    _line()
    _line("┏━━ AURORA-VERIFIED AGENT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", MINT)
    _line("┃", MINT)

    # Step 1 — aurora_analyze.
    _line(f"┃  [1/3] aurora_analyze(path={dataset.name})", CYAN)
    try:
        from aurora_mcp.tools import (
            aurora_analyze, aurora_findings, aurora_explain,
            set_allowed_roots,
        )
        # Allow the dataset's parent so the MCP path-allowlist permits the read.
        set_allowed_roots([str(dataset.resolve().parent)])
    except Exception as e:
        _line(f"┃  ERROR: aurora_mcp.tools not importable — {e}", CRIT)
        return None
    _pause(0.3)
    analyzed = aurora_analyze({"path": str(dataset)})
    if analyzed.get("error"):
        _line(f"┃  ERROR: analyze failed — {analyzed['error']}", CRIT)
        return None
    bundle_run_id = analyzed.get("run_id") or analyzed.get("run_dir")
    fab = analyzed.get("fabricated_count", "?")
    n_crit = analyzed.get("crit_count")
    if n_crit is None:
        # Derive from the summary findings block if present.
        sev = analyzed.get("findings_by_severity") or {}
        n_crit = sev.get("crit") or sev.get("critical") or 0
    _line(f"┃         · run_id          = {bundle_run_id}", GREY)
    _line(f"┃         · fabricated      = {fab}", MINT)
    _line(f"┃         · critical count  = {n_crit}", AMBER)
    _line("┃", MINT)

    # Step 2 — aurora_findings(severity="crit").
    _line(f"┃  [2/3] aurora_findings(severity=\"crit\")", CYAN)
    _pause(0.3)
    crits_out = aurora_findings({"severity": "crit", "limit": 3,
                                   "run_dir": analyzed.get("run_dir")})
    if crits_out.get("error"):
        _line(f"┃  ERROR: findings failed — {crits_out['error']}", CRIT)
        return None
    crit_list: List[Dict[str, Any]] = crits_out.get("findings") or []
    if not crit_list:
        _line(f"┃         (no critical findings on this run)", AMBER)
        # Fall back to whatever the most severe finding was.
        all_out = aurora_findings({"limit": 1, "run_dir": analyzed.get("run_dir")})
        crit_list = (all_out.get("findings") or [])[:1]
        if not crit_list:
            _line(f"┃  no findings at all — abort.", CRIT)
            return None
    head = crit_list[0]
    _line(f"┃         · top finding     = {head.get('title', '(untitled)')[:64]}", GREY)
    _line(f"┃         · method          = {head.get('method', '?')}", GREY)
    _line(f"┃         · claim_id        = {head.get('claim_id', '?')}", GREY)
    _line("┃", MINT)

    # Step 3 — aurora_explain(claim_id).
    claim_id = head.get("claim_id") or ""
    _line(f"┃  [3/3] aurora_explain(claim_id=\"{claim_id}\")", CYAN)
    _pause(0.3)
    expl = aurora_explain({"claim_id": claim_id,
                            "run_dir": analyzed.get("run_dir")}) if claim_id else {}
    method_spec = expl.get("method_spec") if isinstance(expl, dict) else None
    evidence = expl.get("evidence") if isinstance(expl, dict) else None
    if evidence:
        # Pull a couple of representative fields for the terminal climax.
        ev_lines: List[str] = []
        for k in ("row", "row_idx", "z", "z_score", "threshold", "n_obs"):
            if isinstance(evidence, dict) and k in evidence:
                ev_lines.append(f"{k}={evidence[k]}")
        _line(f"┃         · evidence       = {'  '.join(ev_lines) or '(see bundle)'}", GREY)
    if method_spec:
        ms_name = method_spec.get("name") or method_spec.get("id") or "?"
        ms_cite = (method_spec.get("citation") or {}).get("text") \
                   or method_spec.get("source") or ""
        _line(f"┃         · method_spec    = {ms_name}", GREY)
        if ms_cite:
            _line(f"┃         · citation       = {ms_cite[:64]}", GREY)
    _line("┃", MINT)

    _line(f"┃  {BOLD}ANSWER (cited){RESET}{MINT}:", MINT)
    _line(f"┃    \"Critical finding on row {head.get('row') or '?'}, ", MINT)
    _line(f"┃     method={head.get('method', '?')}, ", MINT)
    _line(f"┃     claim={claim_id}, ", MINT)
    _line(f"┃     fabricated={fab}.\"", MINT)
    _line(f"┃    {DIM}every claim traces to a named method + cited paper.{RESET}", GREY)
    _line("┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", MINT)

    return {
        "method":     head.get("method"),
        "row":        head.get("row"),
        "claim_id":   claim_id,
        "bundle_run": bundle_run_id,
        "fabricated": fab,
        "evidence":   evidence,
    }


def _action(verified: Dict[str, Any]) -> None:
    """The 'action the agent takes on the verified figure' beat."""
    _line()
    _line("┏━━ AGENT ACTION (on the verified finding) ━━━━━━━━━━━━━━━━━━", CYAN)
    _line(f"┃", CYAN)
    _line(f"┃  Opening a ticket for the maintenance team:", CYAN)
    _line(f"┃", CYAN)
    _line(f"┃    \"Bearing anomaly detected.", CYAN)
    _line(f"┃     Row {verified.get('row') or '?'} via {verified.get('method') or '?'}.", CYAN)
    _line(f"┃     Aurora bundle: {verified.get('bundle_run') or '—'}.", CYAN)
    _line(f"┃     Claim id: {verified.get('claim_id') or '—'} — see signed bundle.\"", CYAN)
    _line(f"┃", CYAN)
    _line(f"┃  This is what makes the agent {BOLD}defensible{RESET}{CYAN}:", CYAN)
    _line(f"┃    every number above is computed, not guessed.", CYAN)
    _line("┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", CYAN)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="demos.agent_loop.run_demo")
    parser.add_argument("--dataset", type=Path,
                         default=Path("data/fixtures/factory_bearing_demo.csv"),
                         help="Dataset for the verification chain (default: bearing demo)")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if not args.dataset.exists():
        _line(f"ERROR: dataset not found: {args.dataset}", CRIT)
        return 2

    _line()
    _line("AURORA — VERIFICATION CORTEX DEMO", BOLD + CYAN)
    _line(f"dataset: {args.dataset}", GREY)
    _line()

    _naive_baseline(args.dataset)
    verified = _aurora_verified(args.dataset)
    if verified is not None:
        _action(verified)

    _line()
    _line("end of demo.", GREY)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
