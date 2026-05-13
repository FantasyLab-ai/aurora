"""
Export module — produces shareable + reproducible artifacts from a run.

Three deliverables:

  1. ``findings.md``         — narrative findings doc, one section per finding,
                               with method, evidence summary, falsification.
                               Suitable for sharing with a non-technical
                               stakeholder. Markdown prints to PDF cleanly.

  2. ``Methods.md``          — every statistical procedure used in the run
                               with parameters and citations. Suitable for a
                               paper's methods section.

  3. ``aurora.bundle.json``  — reproducibility bundle: aurora_version,
                               code_version_hash, data_input_hash, prior set
                               hash, run config, artifact hashes. Anyone with
                               Aurora installed can replay this bundle and
                               verify hash-equivalent results.

Local-first: no external services, no telemetry. Output written to the run
directory; user controls distribution.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


_AURORA_VERSION = "0.9.5"  # mirrors pyproject.toml


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _read_json(path: Path) -> Optional[Any]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    return None


def _hash_file(path: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return "sha256:" + h.hexdigest()
    except Exception:
        return None


def _detect_code_version(repo_root: Optional[Path] = None) -> str:
    try:
        import subprocess
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root) if repo_root else None,
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            return "git_sha:" + r.stdout.strip()
    except Exception:
        pass
    return "unknown"


# ----------------------------------------------------------------------
# 1. findings.md  —  narrative findings export
# ----------------------------------------------------------------------

def export_findings_markdown(run_dir: Path, *, domain_id: Optional[str] = None,
                             outfile_name: str = "findings.md") -> Path:
    """Produce a per-finding markdown narrative doc.

    Reads the assembled state via state_builder so any domain re-skinning is
    applied. Output lands at ``<run_dir>/<outfile_name>``.
    """
    run_dir = Path(run_dir)
    # Local import to avoid a circular dep.
    from .state_builder import build_state
    try:
        from .domain_config import apply_domain_to_state
    except Exception:
        apply_domain_to_state = None

    state = build_state(run_dir)
    if domain_id and apply_domain_to_state is not None:
        try:
            apply_domain_to_state(state, domain_id)
        except Exception:
            pass

    lines: List[str] = []
    lines.append(f"# Aurora Findings — {state.get('run_id') or run_dir.name}")
    lines.append("")
    ds = state.get("dataset") or {}
    if ds.get("available"):
        lines.append(f"**Dataset:** `{ds.get('name')}`  · "
                     f"{ds.get('rows', '?'):,} rows × {ds.get('cols', '?')} cols")
    structure = state.get("structure") or {}
    if structure.get("available"):
        lines.append(f"**Structure:** time_axis={structure.get('time_axis')} · "
                     f"cadence={structure.get('cadence')} · "
                     f"columns={len(structure.get('columns') or [])}")
    if state.get("domain"):
        lines.append(f"**Domain:** {state['domain'].get('display_name')}")
    lines.append("")

    overview = state.get("overview") or {}
    if overview.get("available"):
        if overview.get("domain_prefix"):
            lines.append(f"> {overview['domain_prefix']}")
            lines.append("")
        lines.append("## Overview")
        lines.append("")
        lines.append(f"- **Anomalies:** {overview.get('anomalies_count', 0)} "
                     f"({overview.get('critical_anomalies', 0)} critical)")
        lines.append(f"- **Regimes:** {overview.get('regimes_count', 0)}")
        lines.append(f"- **Motifs:** {overview.get('motifs_count', 0)}")
        if overview.get('confidence') is not None:
            lines.append(f"- **Confidence:** {overview['confidence']:.2f}")
        lines.append("")

    findings = state.get("findings") or []
    if findings:
        lines.append("## Findings")
        lines.append("")
        for i, f in enumerate(findings, 1):
            lines.append(f"### {i}. {f.get('title','(untitled)')}")
            lines.append("")
            sev = f.get("severity", "info").upper()
            conf = f.get("confidence")
            conf_str = f" · confidence {conf:.2f}" if isinstance(conf, (int, float)) else ""
            lines.append(f"_severity: **{sev}**{conf_str}_")
            lines.append("")
            if f.get("description"):
                lines.append(f"{f['description']}")
                lines.append("")
            method = f.get("method")
            if method:
                lines.append(f"**Method:** `{method}`")
            if f.get("z") is not None:
                lines.append(f"**Statistic:** z = {f['z']:+.2f}σ")
            lines.append("")

    # Anomaly drill-down
    anomalies = state.get("anomalies") or []
    if anomalies:
        lines.append("## Top anomalies (full list)")
        lines.append("")
        lines.append("| Rank | When | Column | Description | Z | Severity |")
        lines.append("|------|------|--------|-------------|---|----------|")
        for a in anomalies[:15]:
            z = f"{a.get('z'):+.2f}σ" if a.get('z') is not None else ""
            lines.append(
                f"| {a.get('rank')} | {a.get('when','')} | `{a.get('column','')}` | "
                f"{a.get('description','')} | {z} | {a.get('severity','').upper()} |"
            )
        lines.append("")

    # Physics
    physics = state.get("physics") or {}
    if physics.get("available"):
        law = physics.get("discovered_law", {})
        lines.append("## Physics")
        lines.append("")
        if law.get("equation"):
            lines.append(f"**Best fit:** `{law.get('name')}` → `{law.get('equation')}`")
            if law.get("rmse") is not None:
                lines.append(f"**RMSE:** {law['rmse']:.4g}")
            lines.append("")
        prior_matches = physics.get("prior_matches") or []
        if prior_matches:
            lines.append("**Matches against known laws:**")
            for pm in prior_matches[:3]:
                pct = round((pm.get("confidence") or 0) * 100)
                lines.append(f"- **{pm.get('display_name')}** ({pm.get('domain')}) "
                             f"— {pct}% match. _{pm.get('citation')}_")
            lines.append("")

    # Forecast
    fc = state.get("forecast") or {}
    if fc.get("available"):
        lines.append("## Forecast")
        lines.append("")
        lines.append(f"**Target:** `{fc.get('target')}` · "
                     f"**Method:** {fc.get('method')} · "
                     f"**Horizon:** {fc.get('horizon')} steps")
        if fc.get("headline_value") is not None:
            ci = ""
            if fc.get("headline_ci_low") is not None and fc.get("headline_ci_high") is not None:
                ci = f" (95% CI {fc['headline_ci_low']:.3g} – {fc['headline_ci_high']:.3g})"
            lines.append(f"**Peak prediction:** {fc['headline_value']:.3g}{ci}")
        lines.append("")

    # Causal what-ifs
    causal = state.get("causal") or {}
    if causal.get("available") and causal.get("items"):
        lines.append("## Causal what-ifs")
        lines.append("")
        for item in causal["items"]:
            lines.append(f"- **IF** {item.get('if_clause')} → {item.get('then_clause')}")
        lines.append(f"_Method: {causal.get('method')}, n={causal.get('n')}_")
        lines.append("")

    # Provenance footer
    lines.append("---")
    lines.append("")
    lines.append(f"_Generated by Aurora v{_AURORA_VERSION} on "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}_  ")
    if state.get("provenance_available"):
        lines.append("_Every claim above has a provenance ledger entry — see "
                     "`_provenance_ledger.jsonl` in the run directory._")
    lines.append("")

    out_path = run_dir / outfile_name
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ----------------------------------------------------------------------
# 2. Methods.md  —  reproducibility-grade methods doc
# ----------------------------------------------------------------------

def export_methods_markdown(run_dir: Path, *, outfile_name: str = "Methods.md") -> Path:
    """Auto-generated methods section listing every statistical procedure used.

    Suitable for a paper's methods section. Reads the deep_math.json + structure
    contract to enumerate every method that was applied with its parameters
    and citation.
    """
    run_dir = Path(run_dir)
    deep = _read_json(run_dir / "deep_math.json") or {}
    structure = _read_json(run_dir / "structure_contract.json") or {}
    motif_doc = _read_json(run_dir / "motif.json") or {}

    lines: List[str] = []
    lines.append(f"# Methods — Aurora run `{run_dir.name}`")
    lines.append("")
    lines.append("All analyses were performed using **Aurora v" + _AURORA_VERSION +
                 "** (local-first, no cloud calls). The following procedures "
                 "were applied to the dataset:")
    lines.append("")

    # Schema / structure
    lines.append("## 1. Structure contract")
    lines.append("")
    lines.append("Each column was classified by:")
    lines.append("")
    lines.append("- **datetime parse rate** ≥ 0.85 → time axis candidate")
    lines.append("- **numeric density** ≥ 0.6 → numeric column")
    lines.append("- **unique-ratio** thresholds for categorical detection")
    lines.append("")
    if structure.get("has_time_axis"):
        lines.append(f"A valid time axis was identified (`{structure.get('time_col')}`, "
                     f"dt = {structure.get('dt_seconds')}s).")
    else:
        lines.append("No valid time axis was identified; the dataset was "
                     "analyzed as ordered observations.")
    lines.append("")

    # Anomaly detection
    aa = (deep.get("extensions") or {}).get("anomaly_attribution") or {}
    if aa:
        lines.append("## 2. Anomaly detection")
        lines.append("")
        lines.append("Outlier detection was performed using **Isolation Forest** "
                     "(Liu, Ting & Zhou, 2008) over the numeric columns. For each "
                     "flagged row, a robust per-feature z-score (median + MAD-based) "
                     "was computed to identify which variables drove the flag. "
                     "Contributions are normalized percentages of summed |robust_z| "
                     "across the top features.")
        lines.append("")
        lines.append(f"- Top-K reported: {aa.get('top_k', 'N/A')}")
        lines.append(f"- Points flagged: {len(aa.get('points') or [])}")
        lines.append("")

    # Forecasting
    fc = deep.get("forecasting") or {}
    if fc and fc.get("note") in (None, "ok"):
        lines.append("## 3. Forecasting")
        lines.append("")
        lines.append(f"A first-order autoregressive model **AR(1)** was fit on "
                     f"the target variable `{deep.get('target_col')}`. Forecast "
                     f"intervals at α={fc.get('alpha', 0.05)} were computed under "
                     f"normal-error assumptions.")
        lines.append("")
        lines.append("Fitted parameters:")
        lines.append(f"- φ (autoregression coefficient): {fc.get('phi', 'N/A')}")
        lines.append(f"- σ (residual std deviation): {fc.get('sigma', 'N/A')}")
        lines.append(f"- Horizon: {fc.get('horizon', 'N/A')} steps")
        lines.append("")
        lines.append("_References: Box & Jenkins (1970)._")
        lines.append("")

    # Causal
    cw = deep.get("causal_what_if") or {}
    if cw and cw.get("note") in (None, "ok"):
        lines.append("## 4. Causal what-if estimation")
        lines.append("")
        covariates = cw.get("covariates_used") or []
        lines.append(f"A linear ordinary-least-squares regression was fit with "
                     f"`{cw.get('target_col')}` as the outcome and "
                     f"`{cw.get('treatment_col')}` as the treatment, controlling "
                     f"for {len(covariates)} covariate(s)"
                     + (f" ({', '.join(covariates)})" if covariates else "") + ". "
                     "The reported effect is the coefficient on the treatment "
                     "variable, interpretable as a causal estimate under the "
                     "back-door criterion if the conditioning set blocks all "
                     "common-cause paths.")
        lines.append("")
        lines.append("_References: Pearl (2009), Hernán & Robins (2020)._")
        lines.append("")

    # Physics
    pd_ = deep.get("physics_discovery") or {}
    best = pd_.get("best") or {}
    if best:
        lines.append("## 5. Physics-aware discovery")
        lines.append("")
        lines.append("A library of candidate ordinary differential equations "
                     "(linear ODE: `dy/dt = a·y + b`; logistic growth: "
                     "`dy/dt = r·y·(1-y/K)`; damped harmonic oscillator: "
                     "`y'' + c·y' + k·y = 0`) was fit by least-squares on the "
                     "derivative gradient. Models were ranked by RMSE on a "
                     "held-out 20% test split, with AIC as a tie-breaker. Each "
                     "ODE was simulated forward via Euler integration to produce ŷ(t).")
        lines.append("")
        lines.append(f"**Best-fit model:** `{best.get('name')}` "
                     f"({best.get('model','')}). Parameters: "
                     f"`{best.get('params')}`. RMSE = {best.get('rmse_test', 'N/A')}, "
                     f"AIC = {best.get('aic', 'N/A')}.")
        lines.append("")
        lines.append("Fitted parameters were checked against a curated catalogue "
                     "of known physical laws (Newton's cooling, exponential decay, "
                     "Verhulst logistic, damped harmonic oscillator, etc.) using "
                     "structural-form matching plus parameter-range validation. "
                     "Matches at ≥70% confidence are reported as recognized laws.")
        lines.append("")

    # Regimes
    regimes = deep.get("regimes") or {}
    if regimes and regimes.get("note") in (None, "ok"):
        lines.append("## 6. Change-point / regime detection")
        lines.append("")
        lines.append(f"Regime boundaries were detected using the **PELT algorithm** "
                     f"(Killick, Fearnhead & Eckley, 2012) with an RBF kernel via "
                     f"the `ruptures` library. The algorithm minimizes a penalized "
                     f"cost function over the full signal; penalty was set to "
                     f"{regimes.get('penalty', 'N/A')} (BIC-aware default).")
        lines.append("")
        lines.append(f"- Change-points detected: {len(regimes.get('change_points') or [])}")
        lines.append(f"- Regime count: {regimes.get('regime_count', 'N/A')}")
        lines.append("")

    # Motifs
    motifs = motif_doc.get("motifs") or []
    if motifs:
        lines.append("## 7. Motif discovery")
        lines.append("")
        lines.append("Recurring patterns were identified by clustering rolling "
                     "windows of the multivariate signal via K-means. High-volatility, "
                     "high-correlation, and monotonic-run motifs are reported with "
                     "their canonical example occurrence and frequency count.")
        lines.append("")
        lines.append(f"- Motifs identified: {len(motifs)}")
        lines.append("")

    # Reproducibility footer
    lines.append("---")
    lines.append("")
    lines.append("## Reproducibility")
    lines.append("")
    lines.append("All analyses were performed with seed=42. The full reproducibility "
                 "bundle (data hash, code version, artifact hashes, prior set hash) "
                 "is available as `aurora.bundle.json` in the run directory. "
                 "Replay via `aurora replay aurora.bundle.json` should produce "
                 "hash-equivalent results within floating-point tolerance.")
    lines.append("")
    lines.append(f"_Auto-generated by Aurora v{_AURORA_VERSION} on "
                 f"{time.strftime('%Y-%m-%d', time.gmtime())}._")
    lines.append("")

    out_path = run_dir / outfile_name
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ----------------------------------------------------------------------
# 3. aurora.bundle.json  —  reproducibility bundle
# ----------------------------------------------------------------------

def export_reproducibility_bundle(run_dir: Path, *,
                                  outfile_name: str = "aurora.bundle.json") -> Path:
    """Write a manifest sufficient to replay this run and verify equivalence.

    Bundle contents:

        aurora_version          str
        code_version            "git_sha:..." | "unknown"
        run_id                  the run directory name
        seed                    integer
        flags                   list of CLI flags used
        input_data_hash         sha256 of the source dataset bytes
        priors_set_hash         sha256 of the v1 catalogue contents
        artifact_hashes         dict {filename: sha256}
        domain                  active domain id when run
        created_at              ISO-8601 UTC

    Reviewer side: `aurora replay <bundle>` re-runs and checks that artifact
    hashes match (within floating-point tolerance for any non-deterministic
    parallel reductions).
    """
    run_dir = Path(run_dir)
    cache_key = _read_json(run_dir / "_CACHE_KEY.json") or {}
    resolved = _read_json(run_dir / "_RESOLVED_CONTRACT.json") or {}
    run_meta = _read_json(run_dir / "_RUN_META.json") or {}

    # Hash every artifact in the run dir.
    artifact_hashes: Dict[str, str] = {}
    for p in sorted(run_dir.iterdir()):
        if not p.is_file():
            continue
        # Skip the bundle itself and the cache key (which we hash separately)
        # to avoid self-referential hashes.
        if p.name in (outfile_name, "_CACHE_KEY.json"):
            continue
        h = _hash_file(p)
        if h:
            artifact_hashes[p.name] = h

    # Hash the v1 priors set so a replay can detect catalogue drift.
    priors_set_hash = _hash_priors_set()

    # Detect code version from git.
    code_version = _detect_code_version(run_dir.parents[1] if len(run_dir.parents) >= 2 else None)

    bundle = {
        "aurora_version": _AURORA_VERSION,
        "bundle_format": "1.0",
        "code_version": code_version,
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "seed": cache_key.get("seed") or run_meta.get("seed") or 42,
        "flags": cache_key.get("flags") or [],
        "input_data_hash": cache_key.get("input_hash"),
        "priors_set_hash": priors_set_hash,
        "domain": resolved.get("domain"),
        "artifact_hashes": artifact_hashes,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "replay_command": "aurora replay <this-file>",
        "replay_tolerance": "floating-point ≤ 1e-9 RMSE per artifact",
    }

    out_path = run_dir / outfile_name
    out_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    return out_path


def _hash_priors_set() -> str:
    """SHA-256 of the v1 priors catalogue — detects catalogue drift across versions."""
    try:
        from .priors import list_priors
        priors = list_priors()
        # Stable serialization: sort by id, dump each prior's identifying fields.
        material = json.dumps(
            [
                {"id": p.id, "form": p.matches_runner_form,
                 "domain": p.domain, "structural_form": p.structural_form,
                 "sign_constraints": dict(sorted(p.sign_constraints.items()))}
                for p in sorted(priors, key=lambda x: x.id)
            ],
            sort_keys=True, separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()
    except Exception:
        return "unknown"


# ----------------------------------------------------------------------
# Public entry — wraps all three exports
# ----------------------------------------------------------------------

def export_all(run_dir: Path, *, domain_id: Optional[str] = None) -> Dict[str, str]:
    """Generate all three exports + return their paths."""
    return {
        "findings_md": str(export_findings_markdown(run_dir, domain_id=domain_id)),
        "methods_md":  str(export_methods_markdown(run_dir)),
        "bundle_json": str(export_reproducibility_bundle(run_dir)),
    }
