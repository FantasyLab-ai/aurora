"""
Synthesis engine entry point.

Pipeline:

  1. classify every finding into a synthesis-vocabulary kind
  2. evaluate every loaded template against the findings; rank by
     (match score × specificity)
  3. pick the best matching template; fill its slots from state
  4. render the skeleton → state.interpretive_summary
  5. equation translations → state.equation_translations
     (keyed by finding index — frontend reads finding[i]'s entry)
  6. domain-context rules → state.domain_context (top 3)
  7. record metadata in state.synthesis_meta

Glass-box invariant: every paragraph that ends up in the user's view is
reproducible from the templates + the input state. No LLM calls in this
module.

Public:

    from fantasyai.aurora.synthesis import synthesize
    synthesize(state)             # mutates state in place
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("aurora.synthesis")

# v1.2.1 fix — synthesis cache file name. Lives inside the run_dir
# so it's automatically scoped to the run and gets cleaned up when
# the run is removed. Bumping the version suffix forces a recompute
# (e.g. after a template or prompt change).
SYNTHESIS_CACHE_FILE = "synthesis_cache.v1.json"

from .matcher import (
    classify_finding_kind, evaluate_match,
    list_templates, list_equation_translations,
    list_domain_context_notes, index_findings_by_kind,
)
from .slot_extractor import extract_slot, render_skeleton
from .equation_translator import translate_equation


@dataclass
class SynthesisResult:
    interpretive_summary: str = ""
    equation_translations: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    domain_context: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "interpretive_summary": self.interpretive_summary,
            "equation_translations": {str(k): v
                                        for k, v in self.equation_translations.items()},
            "domain_context": list(self.domain_context),
            "meta": dict(self.meta),
        }


# ---------------------------------------------------------------------------
# Template selection
# ---------------------------------------------------------------------------

def _rank_templates(templates: List[Dict[str, Any]],
                     findings: List[Dict[str, Any]],
                     active_domain: Optional[str]
                     ) -> List[Tuple[Dict[str, Any], float, Dict[str, Any]]]:
    """Evaluate each template's match_condition; return list sorted by
    (score × specificity) descending. Domain-matching templates get a
    small boost to outrank generic when both match."""
    out = []
    for t in templates:
        cond = t.get("match_condition") or {}
        ev = evaluate_match(cond, findings)
        if not ev["matched"]:
            continue
        specificity = float(t.get("specificity", 1))
        domain = (t.get("domain") or "generic").lower()
        domain_boost = 1.5 if (active_domain and domain == active_domain.lower()) else 1.0
        rank = ev["score"] * specificity * domain_boost
        out.append((t, rank, ev))
    out.sort(key=lambda r: -r[1])
    return out


# ---------------------------------------------------------------------------
# Slot filling
# ---------------------------------------------------------------------------

def _fill_slots(template: Dict[str, Any],
                 state: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Resolve every slot expression. Returns (filled_dict, missing_keys).

    Resolution order matters when slots reference each other; we walk
    extractors in their declaration order (insertion-ordered dicts) and
    pass already-filled slots into subsequent expressions.
    """
    extractors = template.get("slot_extractors") or {}
    filled: Dict[str, Any] = {}
    missing: List[str] = []
    for slot_name, expr in extractors.items():
        try:
            v = extract_slot(expr, state, filled_slots=filled)
        except Exception as e:
            v = None
        if v is None or (isinstance(v, str) and v.strip() == ""):
            missing.append(slot_name)
        filled[slot_name] = v
    return filled, missing


def _required_slots(skeleton: str) -> List[str]:
    """Slots referenced in the skeleton (excluding any ending in
    "_if_present" or "_clause" — those are intentionally optional)."""
    import re
    placeholders = re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", skeleton or "")
    required = []
    for p in placeholders:
        if p.endswith("_if_present") or p.endswith("_clause") or \
                p.endswith("_optional"):
            continue
        required.append(p)
    return list(dict.fromkeys(required))


# ---------------------------------------------------------------------------
# Domain-context selection
# ---------------------------------------------------------------------------

def _select_domain_notes(domain: str,
                          findings_by_kind: Dict[str, List[Dict[str, Any]]],
                          state: Dict[str, Any],
                          max_notes: int = 3) -> List[Dict[str, Any]]:
    """Walk the domain's context-notes list and keep up to ``max_notes``
    whose ``trigger`` rule matches the current findings.

    Each note carries a ``trigger`` shaped like a template's
    match_condition (requires/optional). When trigger is missing we treat
    the note as always-applicable.
    """
    notes = list_domain_context_notes(domain)
    if not notes:
        return []
    chosen: List[Dict[str, Any]] = []
    for n in notes:
        trigger = n.get("trigger")
        if trigger:
            ev = evaluate_match(trigger, state.get("findings") or [])
            if not ev["matched"]:
                continue
        chosen.append({
            "id": n.get("id") or "",
            "title": n.get("title") or "",
            "note": n.get("note") or "",
            "domain": domain,
            "matched": True,
        })
        if len(chosen) >= max_notes:
            break
    return chosen


# ---------------------------------------------------------------------------
# Equation translation pass
# ---------------------------------------------------------------------------

def _translate_findings_equations(findings: List[Dict[str, Any]]
                                    ) -> Dict[int, Dict[str, Any]]:
    """Per-finding equation→intuition translations."""
    if not findings:
        return {}
    translations = list_equation_translations()
    if not translations:
        return {}
    out: Dict[int, Dict[str, Any]] = {}
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            continue
        result = translate_equation(f, translations)
        if result is None:
            continue
        out[i] = result
    return out


# ---------------------------------------------------------------------------
# Public entry: synthesize
# ---------------------------------------------------------------------------

def synthesize(state: Dict[str, Any]) -> SynthesisResult:
    """Run the synthesis engine. Mutates state in place + returns the
    SynthesisResult for convenience.

    Failure mode: returns an empty SynthesisResult and writes nothing to
    state when no template matches. The frontend hides the "WHAT THIS
    MEANS" panel cleanly when interpretive_summary is empty.

    v1.2.1: caches the FIRST successful RAG synthesis to disk
    (``<run_dir>/synthesis_cache.v1.json``) and replays it on every
    subsequent /api/state poll. Without this, the function would
    re-run RAG every poll — calling Ollama, racing the network — and
    on transient failures the result would flip from RAG-grounded to
    the template fallback between successive polls, then back again
    on the next poll. The cache fixes that flip.

    Cache rules:
      * Only written when the run is ``complete`` AND synthesis
        succeeded with ``mode == "rag"``.
      * Template-mode results are NOT cached, so a transient
        Ollama failure doesn't pin the user to template forever.
      * Cache hit replays the full ``state["interpretive_summary"]``,
        ``state["equation_translations"]``, ``state["domain_context"]``,
        and ``state["synthesis_meta"]`` — same shape as a fresh run.
      * Cache key is the run_dir path; one cache per run, isolated.
    """
    t0 = time.time()
    if not isinstance(state, dict):
        return SynthesisResult()

    # ---- v1.2.1: cache check ------------------------------------------
    cache_path: Optional[Path] = None
    run_dir_str = state.get("run_dir")
    run_meta_state = (state.get("run_meta") or {}).get("state")
    run_complete = (run_meta_state == "complete")
    if run_dir_str and run_complete:
        try:
            cache_path = Path(run_dir_str) / SYNTHESIS_CACHE_FILE
            if cache_path.exists():
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                # Stamp the cache hit into the state same way a fresh
                # run would. Casting key types defensively in case the
                # JSON round-trip changed dict-key types.
                state["interpretive_summary"]  = cached.get("interpretive_summary", "") or ""
                state["equation_translations"] = {
                    str(k): v for k, v in (cached.get("equation_translations") or {}).items()
                }
                state["domain_context"]        = cached.get("domain_context") or []
                state["synthesis_meta"]        = cached.get("synthesis_meta") or {}
                # Mark this response as a cache replay so the frontend
                # could distinguish if it ever wants to (currently it
                # doesn't — looks identical to a fresh run).
                if isinstance(state["synthesis_meta"], dict):
                    state["synthesis_meta"]["cache_hit"] = True
                return SynthesisResult(
                    interpretive_summary=state["interpretive_summary"],
                    equation_translations=state["equation_translations"],
                    domain_context=state["domain_context"],
                    meta=state["synthesis_meta"],
                )
        except Exception as e:
            # Cache read failures are non-fatal — fall through to a
            # fresh compute. Log so the next regression can find it.
            _log.warning("synthesis cache read failed (%s); recomputing",
                          type(e).__name__, exc_info=True)
            cache_path = None

    findings = state.get("findings") or []
    sm = state.get("system_model") or {}
    active_domain = (sm.get("template_id") or "").lower() or None

    # Pre-classify: stamps _synthesis_kind on each finding.
    by_kind = index_findings_by_kind(findings)

    # 1-3: pick + fill the best template.
    templates = list_templates()
    ranked = _rank_templates(templates, findings, active_domain)
    chosen_template: Optional[Dict[str, Any]] = None
    chosen_filled: Dict[str, Any] = {}
    chosen_missing: List[str] = []
    chosen_eval: Dict[str, Any] = {}
    interpretive: str = ""
    fired_id: Optional[str] = None
    for tpl, rank, ev in ranked:
        filled, missing = _fill_slots(tpl, state)
        skeleton = tpl.get("skeleton") or ""
        # Skip when REQUIRED slots are missing (those NOT ending in
        # _if_present / _clause / _optional).
        required = _required_slots(skeleton)
        critical_missing = [k for k in required if k in missing]
        if critical_missing:
            continue
        rendered, _ = render_skeleton(skeleton, filled)
        if rendered.strip():
            chosen_template = tpl
            chosen_filled = filled
            chosen_missing = missing
            chosen_eval = ev
            interpretive = rendered
            fired_id = tpl.get("id")
            break

    # 5: equation translations.
    eq_translations = _translate_findings_equations(findings)

    # 6: domain context.
    domain_notes: List[Dict[str, Any]] = []
    if active_domain:
        domain_notes = _select_domain_notes(active_domain, by_kind, state)

    # 7: meta. Carries both the engine-facing names (fired_template_id,
    # match_score) and the friendlier downstream names (template_id,
    # score, kinds_seen, specificity) so the frontend pill and the
    # learning-loop telemetry can both read it without translation.
    kinds_seen = sorted({k for k in by_kind.keys() if k})
    specificity_val = (chosen_template.get("specificity")
                        if chosen_template else None)
    meta = {
        # Friendly aliases — used by the frontend pill and the changelog
        # ``synthesis_template_fires`` collector.
        "template_id": fired_id,
        "score": (chosen_eval.get("score") if chosen_eval else None),
        "specificity": specificity_val,
        "kinds_seen": kinds_seen,
        # Engine-internal names (kept for backward compatibility / debug).
        "fired_template_id": fired_id,
        "fired_template_domain": chosen_template.get("domain") if chosen_template else None,
        "ranked_template_ids": [t[0].get("id") for t in ranked[:5]],
        "n_templates_evaluated": len(templates),
        "n_templates_matched": len(ranked),
        "n_findings": len(findings),
        "active_domain": active_domain,
        "missing_slots": chosen_missing,
        "match_score": (chosen_eval.get("score") if chosen_eval else None),
        "elapsed_ms": int((time.time() - t0) * 1000),
    }

    # ---- Brief 4 (Knowledge Bank + RAG) — RAG synthesis layer.
    # When the feature flag allows it, attempt RAG: retrieve grounding
    # entries from the knowledge bank, prompt Gemma 12B with strict
    # grounding instructions, verify citations. On any failure
    # (no entries, no Ollama, verification below threshold) we keep
    # the template-engine output that's already been computed above.
    # The template synthesis ALWAYS runs — it's the canonical fallback
    # and the regression baseline (findings cards / equation chips
    # never depend on RAG).
    rag_meta: Optional[Dict[str, Any]] = None
    rag_used = False
    try:
        from .rag import synthesis_mode, synthesize_rag
        mode = synthesis_mode()
    except Exception:
        import logging as _logging
        _logging.getLogger("aurora.rag").warning(
            "Could not import RAG module — using template mode",
            exc_info=True,
        )
        mode = "template"
    if mode in ("rag", "auto"):
        try:
            rag_result = synthesize_rag(state)
            rag_meta = rag_result.get("synthesis_meta") or {}
            if rag_result.get("ok"):
                interpretive = rag_result["interpretive_summary"]
                rag_used = True
        except Exception as e:
            # Brief: every except in the synthesis pipeline must log
            # exc_info=True so the next regression is diagnosable from
            # server logs alone.
            import logging as _logging
            _logging.getLogger("aurora.rag").error(
                "RAG synthesis raised an unhandled exception (%s) — "
                "falling back to template",
                type(e).__name__,
                exc_info=True,
            )
            rag_meta = {"mode": "rag_failed",
                          "reason": f"rag_exception: {type(e).__name__}: {e}"}
    if rag_meta is not None:
        # Stash retrieval + verification info for the frontend.
        meta["rag"] = rag_meta
    meta["mode"] = ("rag" if rag_used
                       else ("template" if mode != "rag" else "rag_failed"))

    # ---- v1.1: deterministic pipeline-behavior disclosure.
    # Scan advanced_methods.steps for sampled / timed-out methods and
    # append honest disclosure lines AFTER the interpretive summary.
    # These are factual statements about pipeline behaviour (not
    # analytical claims), so they bypass the RAG grounding rules — no
    # KB citation required, no LLM hallucination risk.
    interpretive = _append_pipeline_disclosure(interpretive, state)
    # Mirror the disclosure into ``synthesis_meta`` so the frontend
    # and tests can introspect what was disclosed without re-parsing
    # the prose. The structured field is the truth; the prose is the
    # human-readable surface.
    meta["pipeline_disclosure"] = _collect_pipeline_disclosure(state)

    result = SynthesisResult(
        interpretive_summary=interpretive,
        equation_translations=eq_translations,
        domain_context=domain_notes,
        meta=meta,
    )

    # Mutate the state.
    state["interpretive_summary"] = interpretive
    state["equation_translations"] = {str(k): v for k, v in eq_translations.items()}
    state["domain_context"] = domain_notes
    state["synthesis_meta"] = meta

    # ---- v1.2.1: cache write ------------------------------------------
    # Persist ONLY when the run is complete AND the synthesis came back
    # in RAG mode. Template-mode results are transient (typically caused
    # by a brief Ollama hiccup); not caching them lets the next poll
    # retry RAG cleanly. Once RAG succeeds we lock in the cache and
    # every subsequent poll is a stable replay.
    try:
        if cache_path is not None and meta.get("mode") == "rag":
            cache_path.write_text(
                json.dumps({
                    "interpretive_summary":  interpretive,
                    "equation_translations": state["equation_translations"],
                    "domain_context":        domain_notes,
                    "synthesis_meta":        meta,
                }, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
    except Exception as e:
        # Cache write failures must NEVER break the response.
        _log.warning("synthesis cache write failed (%s); response unaffected",
                      type(e).__name__, exc_info=True)

    return result


# ============================================================
# v1.1 — pipeline-behavior disclosure (sampling + timeouts)
# ============================================================

def _collect_pipeline_disclosure(state: Dict[str, Any]) -> Dict[str, Any]:
    """Walk ``state["advanced_methods"]["steps"]`` and the per-method
    state blocks (``state.hmm``, ``state.wavelet_period``, …) to build
    a structured record of what was sampled and what timed out.

    Returns::

        {
          "sampled_methods": [
            {"method": "hmm", "n_train_used": 2000, "n_train_total": 101766,
             "sample_strategy": "stratified_time_preserving"},
            ...
          ],
          "timed_out_methods": [
            {"method": "mutual_info", "elapsed_seconds": 90.2,
             "timeout_seconds": 90},
            ...
          ],
        }

    The frontend reads this for the "methods skipped" summary row;
    tests assert specific entries to verify the disclosure path is
    not dead code.
    """
    sampled: List[Dict[str, Any]] = []
    timed_out: List[Dict[str, Any]] = []

    # Sampling lives on the per-method state blocks (state["hmm"],
    # state["wavelet_period"]) — Fix 1 and Fix 2 stamp these fields
    # uniformly whether or not the method's available flag is True.
    for method_key, state_key in [("hmm", "hmm"),
                                    ("wavelet", "wavelet_period")]:
        blk = state.get(state_key)
        if isinstance(blk, dict) and blk.get("downsampled") is True:
            sampled.append({
                "method": method_key,
                "n_train_used": blk.get("n_train_used"),
                "n_train_total": blk.get("n_train_total"),
                "sample_strategy": blk.get("sample_strategy"),
                # When state_builder pre-capped the CSV (long-standing
                # 5000-row tractability cap), this is the ORIGINAL CSV
                # row count. Used by the disclosure prose to be honest
                # about the full cascade.
                "dataset_rows_full": blk.get("dataset_rows_full"),
            })

    # Timeouts live on advanced_methods.steps[<method>] — Fix 3 stamps
    # ``skipped_reason: method_timeout`` + elapsed_seconds + timeout_seconds.
    am = state.get("advanced_methods") or {}
    steps = am.get("steps") or {}
    for label, res in steps.items():
        if not isinstance(res, dict):
            continue
        if res.get("skipped_reason") == "method_timeout":
            timed_out.append({
                "method": label,
                "elapsed_seconds": res.get("elapsed_seconds"),
                "timeout_seconds": res.get("timeout_seconds"),
            })

    # v1.2: deep_math methods that hit ``_call_dm_with_timeout``'s cap.
    # state_builder writes a slim summary under ``_deep_math_timeouts``
    # (the raw deep_math dict is too large to stash on state). Each
    # entry has the same shape advanced_pass timeouts use.
    dm_timeouts = state.get("_deep_math_timeouts") or []
    for entry in dm_timeouts:
        if not isinstance(entry, dict):
            continue
        timed_out.append({
            "method": entry.get("method") or entry.get("block_key"),
            "elapsed_seconds": entry.get("elapsed_seconds"),
            "timeout_seconds": entry.get("timeout_seconds"),
        })

    return {"sampled_methods": sampled, "timed_out_methods": timed_out}


# Phrasing per the v1.1 brief — short, factual, no analytical claim.
# Sentences are appended to interpretive_summary as a separate
# "Pipeline notes" paragraph so they're visually distinct from the
# RAG-grounded narrative.
_DISCLOSURE_HEADER = "\n\nPipeline notes:"
_METHOD_DISPLAY_NAMES = {
    "hmm":     "HMM",
    "wavelet": "Wavelet",
    "mutual_info":         "Mutual information",
    "granger":             "Granger causality",
    "sindy":               "SINDy",
    "lomb_scargle":        "Lomb-Scargle",
    "gaussian_process":    "Gaussian process",
    "topology":            "Topology",
    "power_analysis":      "Power analysis",
    "cluster_stability":   "Cluster stability",
    "bayesian_target":     "Bayesian target",
    "panel_decomposition": "Panel decomposition",
    "survival":            "Survival",
    "spatial":             "Spatial",
    "network":             "Network",
    "did":                 "Difference-in-differences",
    "compositional":       "Compositional",
    # v1.2: deep_math method labels (the strings _call_dm_with_timeout
    # emits as the ``method`` field). Friendly forms surface in the
    # "Pipeline notes" disclosure paragraph.
    "regimes":              "Regime detection",
    "forecasting":          "Forecasting",
    "uncertainty":          "Uncertainty (bootstrap CI)",
    "risk_surface":         "Risk surface",
    "causal_what_if":       "Causal what-if",
    "physics_diagnostics_a":  "Physics diagnostics",
    "physics_diagnostics_b":  "Physics diagnostics",
    "physics_v2":           "Physics v2",
    "physics_discovery":    "Physics discovery",
    "physics_invariants":   "Physics invariants",
    "simulation":           "Counterfactual simulation",
    "optimization_lp":      "Optimization (LP)",
    "optimization_nonlinear": "Optimization (nonlinear)",
    "prepare_model_frame":  "Model-frame preparation",
}


def _append_pipeline_disclosure(interpretive: str,
                                  state: Dict[str, Any]) -> str:
    """Append a "Pipeline notes" paragraph to ``interpretive`` that
    deterministically discloses any sampling or timeouts that
    occurred during this run. Returns the (possibly extended) string.

    No KB citations — these are factual pipeline-behavior statements,
    not analytical claims. The text is generated by template
    interpolation from the metadata fields, not by the LLM, so it
    cannot hallucinate counts or methods.
    """
    disc = _collect_pipeline_disclosure(state)
    sampled = disc.get("sampled_methods") or []
    timed_out = disc.get("timed_out_methods") or []
    if not sampled and not timed_out:
        return interpretive   # nothing to disclose — leave prose untouched

    lines: List[str] = []
    for s in sampled:
        name = _METHOD_DISPLAY_NAMES.get(s["method"], s["method"].title())
        n_used = s.get("n_train_used") or "?"
        n_total = s.get("n_train_total") or "?"
        n_full = s.get("dataset_rows_full")
        strategy = s.get("sample_strategy") or "stratified_time_preserving"
        if strategy == "stratified_time_preserving":
            strat_note = "stratified time-preserving sample"
        else:
            strat_note = f"{strategy} sample"
        try:
            n_used_fmt  = f"{int(n_used):,}"
            n_total_fmt = f"{int(n_total):,}"
            n_full_fmt  = f"{int(n_full):,}" if isinstance(n_full, int) else None
        except Exception:
            n_used_fmt = str(n_used); n_total_fmt = str(n_total)
            n_full_fmt = None
        # When the pipeline pre-capped the CSV (state_builder's
        # tractability cap), disclose the full cascade so the user knows
        # both stages. When no pre-cap occurred, omit the parenthetical
        # so the prose stays clean on small datasets.
        if (n_full_fmt is not None
                and isinstance(n_full, int)
                and isinstance(n_total, int)
                and n_full > n_total):
            cascade = (f" (selected from a {n_total_fmt}-row recent window "
                        f"of your {n_full_fmt}-row dataset)")
        else:
            cascade = f" of your {n_total_fmt}-row dataset"
        lines.append(
            f"{name} analyzed a {n_used_fmt}-row {strat_note}{cascade} "
            f"(time axis preserved). Findings reflect this sample. "
            f"Higher tiers (STANDARD, FULL) may analyze more of the data."
        )

    for t in timed_out:
        name = _METHOD_DISPLAY_NAMES.get(t["method"], t["method"].title())
        cap = t.get("timeout_seconds") or "the per-method"
        lines.append(
            f"{name} analysis exceeded the {cap}-second per-method time "
            f"budget on this dataset and was deferred. Reducing column "
            f"count or using a higher tier will allow it to complete."
        )

    if not lines:
        return interpretive
    return interpretive.rstrip() + _DISCLOSURE_HEADER + "\n" + "\n".join(
        f"  - {ln}" for ln in lines
    )
