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
import contextlib
import io
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Aurora's RAG layer emits `_log.warning("RAG falling back: ...")` whenever a
# new dataset has no matching precedent in the knowledge bank. That's the
# correct behaviour — the system refusing to fabricate — but on a clean demo
# run it clutters the cinematic terminal output. Belt-and-braces: silence the
# loggers and capture stdout/stderr around each MCP call.
for _name in ("fantasyai", "fantasyai.aurora.synthesis.rag", "aurora_mcp"):
    logging.getLogger(_name).setLevel(logging.ERROR)


def _quiet_call(fn: Callable[..., Any], *args, **kwargs) -> Any:
    """Run an MCP tool with stdout/stderr captured. Aurora internals may
    emit logger warnings ("RAG falling back: ...") or stray prints; we
    don't want them interleaved with the demo's cinematic terminal blocks.
    The function's return value still propagates normally."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        return fn(*args, **kwargs)


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


def _row_display(finding: Dict[str, Any]) -> str:
    """Render whichever row representation a finding carries.

    Findings from scalar detectors (z-score, Hampel) expose a single
    ``row`` int. Subspace / multi-row detectors (Robust PCA, BOCPD,
    matrix profile) stash a list under ``evidence.top_outlier_rows`` /
    ``evidence.rows`` / ``rows``. We surface the first few + the count
    so the demo always cites a real number, never an em dash.
    """
    # Scalar shapes first.
    for k in ("row", "row_idx", "claim_row"):
        v = finding.get(k)
        if isinstance(v, (int, float)):
            return str(int(v))
    # List-row shapes — finding-level then evidence-level.
    ev = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    for src in (finding, ev):
        for k in ("top_outlier_rows", "outlier_rows", "rows"):
            v = (src or {}).get(k)
            if isinstance(v, (list, tuple)) and v:
                # Stringify whatever's there — numpy scalars, ints, even
                # tuples — without filtering. An empty preview was the bug
                # in the previous version when values weren't plain ints.
                preview = ", ".join(str(x) for x in v[:3])
                more = f" (+{len(v) - 3} more)" if len(v) > 3 else ""
                return f"[{preview}{more}]"
    # n_outlier_rows is a count, not the rows themselves — last resort.
    n = (ev or {}).get("n_outlier_rows") or finding.get("n_rows")
    if isinstance(n, (int, float)):
        return f"{int(n)} outlier rows"
    return "—"


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
    analyzed = _quiet_call(aurora_analyze, {"path": str(dataset)})
    if analyzed.get("error"):
        _line(f"┃  ERROR: analyze failed — {analyzed['error']}", CRIT)
        return None
    # aurora_analyze returns {"ok": True, "summary": {...}, "bundle": ...} —
    # the run id, fabricated count, and severity counts all live under summary.
    summary = analyzed.get("summary") or {}
    bundle_run_id = summary.get("run_id")
    fab = summary.get("fabricated_count", "?")
    sev = summary.get("findings_count_by_severity") or {}
    n_crit = sev.get("crit") or sev.get("critical") or 0
    n_warn = sev.get("warn") or sev.get("warning") or 0
    n_info = sev.get("info") or 0
    n_total = n_crit + n_warn + n_info
    _line(f"┃         · run_id          = {bundle_run_id}", GREY)
    _line(f"┃         · fabricated      = {fab}", MINT)
    _line(f"┃         · findings        = {n_total} "
          f"(crit={n_crit}  warn={n_warn}  info={n_info})", AMBER)
    _line("┃", MINT)

    # Step 2 — aurora_findings(severity="crit"). The findings tool takes
    # `path`, not `run_dir`; the SDK's run cache short-circuits the re-call
    # so this doesn't re-analyse.
    _line(f"┃  [2/3] aurora_findings(severity=\"crit\")", CYAN)
    _pause(0.3)
    crits_out = _quiet_call(aurora_findings, {"path": str(dataset),
                                                "severity": "crit", "limit": 3})
    if crits_out.get("error"):
        _line(f"┃  ERROR: findings failed — {crits_out['error']}", CRIT)
        return None
    crit_list: List[Dict[str, Any]] = crits_out.get("findings") or []
    if not crit_list:
        _line(f"┃         (no critical findings on this run)", AMBER)
        # Fall back to whatever the most severe finding was.
        all_out = _quiet_call(aurora_findings, {"path": str(dataset), "limit": 1})
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
    expl = _quiet_call(aurora_explain, {"path": str(dataset),
                                          "claim_id": claim_id}) if claim_id else {}
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

    row_str = _row_display(head)
    _line(f"┃  {BOLD}ANSWER (cited){RESET}{MINT}:", MINT)
    _line(f"┃    \"Top finding: row(s) {row_str}, ", MINT)
    _line(f"┃     method={head.get('method', '?')}, ", MINT)
    _line(f"┃     claim={claim_id}, ", MINT)
    _line(f"┃     fabricated={fab}.\"", MINT)
    _line(f"┃    {DIM}every claim traces to a named method + cited paper.{RESET}", GREY)
    _line("┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", MINT)

    return {
        "method":     head.get("method"),
        "row":        _row_display(head),
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
    _line(f"┃     Row(s) {verified.get('row') or '—'} via {verified.get('method') or '?'}.", CYAN)
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
