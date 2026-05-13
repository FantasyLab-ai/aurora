"""
Pattern matching for the synthesis engine.

Two responsibilities:

  1. Map every finding into a synthesis-vocabulary "kind" — the brief
     uses richer labels than the narrative_engine's kind classifier:
       anomaly, regime_change, forecast_peak, discovered_motif,
       physics_match, causal_chain, variable_motif, oscillation,
       monotonic_trend, generic.
  2. Evaluate a template's ``match_condition`` against the current
     findings set: requires/optional, kind+subkind+min_count.

Templates and equation translations are loaded from disk (JSON files
under ``templates/`` and ``equation_translations/``). Loading is cached.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Synthesis-vocabulary kind classification
# ---------------------------------------------------------------------------

_OSCILLATION_HINTS = (
    "damped", "harmonic", "oscillator", "ar(2)", "ar2", "resonance",
    "limit cycle", "oscillation",
)
_LOGISTIC_HINTS = ("logistic", "saturation", "carrying capacity")
_DECAY_HINTS    = ("decay", "newton's cooling", "exponential decay")


def classify_finding_kind(finding: Dict[str, Any]) -> str:
    """Return a synthesis-vocabulary kind for the finding.

    The synthesis engine uses a richer vocabulary than the narrative
    engine's kind classifier so templates can match more specifically
    (e.g. "discovered_motif" requires a fitted equation; a plain anomaly
    is "anomaly", not "generic").
    """
    if not isinstance(finding, dict):
        return "generic"
    title = (finding.get("title") or "").lower()
    desc = (finding.get("description") or "").lower()
    method = (finding.get("method") or "").lower()
    headline_kind = (finding.get("headline_kind") or "").lower()
    haystack = f"{title} {desc} {method}"

    # Wave B — multivariate outlier consensus carries an explicit
    # ``kind_hint`` field so it never gets mis-classified as a regular
    # univariate anomaly. Check for it before the generic anomaly branch.
    if (finding.get("kind_hint") == "multivariate_outlier"
            or "multivariate" in method
            or "multivariate outlier" in title):
        return "multivariate_outlier"
    # Brief-3 advanced kinds — every emitter sets ``kind_hint`` so the
    # matcher routes to the right template even when the title alone
    # is ambiguous.
    kh = finding.get("kind_hint")
    if kh in {
        "discovered_dynamics",
        "latent_regime_structure",
        "nonlinear_dependence",
        "causal_direction",
        "nonstationary_oscillation",
        "uneven_sampling_period",
        "topological_structure",
        "compositional_correction",
        "experimental_design_recommendation",
        # Brief-2 methods that previously had no orchestrator hook
        # (now wired into apply_advanced_pass).
        "cluster_structure",
        "panel_decomposition",
        "survival_curve",
        "spatial_autocorrelation",
        "graph_structure",
        "treatment_effect_did",
    }:
        return kh
    if "anomaly" in title or "iso-forest" in method:
        return "anomaly"
    if title.startswith("forecast") or "forecast" in method:
        return "forecast_peak"
    if "regime" in title or "regime" in method:
        return "regime_change"
    if "matches known law" in title or headline_kind == "physics_match" \
            or "physics_prior" in method:
        return "physics_match"
    if "physical model fit" in title or "physics_discovery" in method:
        # If the title carries a recognisable equation form, surface it.
        for h in _OSCILLATION_HINTS:
            if h in haystack:
                return "discovered_motif"
        for h in _LOGISTIC_HINTS:
            if h in haystack:
                return "discovered_motif"
        for h in _DECAY_HINTS:
            if h in haystack:
                return "discovered_motif"
        return "discovered_motif"
    if "causal" in title or "causal" in method:
        return "causal_chain"
    if any(h in haystack for h in _OSCILLATION_HINTS):
        return "oscillation"
    if any(h in haystack for h in _LOGISTIC_HINTS):
        return "monotonic_trend"
    if "matrix-profile motif" in title or "high_correlation" in method:
        return "variable_motif"
    if "monotonic" in title or "trend" in title:
        return "monotonic_trend"
    return "generic"


def index_findings_by_kind(findings: List[Dict[str, Any]]
                            ) -> Dict[str, List[Dict[str, Any]]]:
    """Return {kind: [finding, ...]} — caller can also query
    ``findings[kind=foo]`` style via slot_extractor."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        k = classify_finding_kind(f)
        # Stash the resolved kind on the finding so downstream consumers
        # (slot extractor, frontend) can read it without re-classifying.
        f.setdefault("_synthesis_kind", k)
        out.setdefault(k, []).append(f)
    return out


# ---------------------------------------------------------------------------
# match_condition evaluation
# ---------------------------------------------------------------------------

def _kind_satisfies(req: Dict[str, Any],
                     findings_by_kind: Dict[str, List[Dict[str, Any]]]
                     ) -> bool:
    """Single requirement evaluator."""
    kind = req.get("kind")
    pool = list(findings_by_kind.get(kind, []))
    # subkind_in narrows the pool by checking finding fields.
    subkinds = req.get("subkind_in") or []
    if subkinds:
        narrowed = []
        for f in pool:
            f_text = (
                (f.get("title", "") + " "
                 + f.get("description", "") + " "
                 + f.get("method", "")).lower()
            )
            if any(sk.lower() in f_text for sk in subkinds):
                narrowed.append(f)
        pool = narrowed
    min_count = int(req.get("min_count", 1))
    return len(pool) >= min_count


def evaluate_match(match_condition: Dict[str, Any],
                    findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return ``{matched: bool, score: float, reasons: [...]}`` describing
    whether the template's match_condition is satisfied + why.

    Score = (#required satisfied / total required) * 1.0
            + (#optional satisfied / max(1, total optional)) * 0.2
    A single failed required → matched=False regardless of optional.
    """
    by_kind = index_findings_by_kind(findings)
    required = match_condition.get("requires") or []
    optional = match_condition.get("optional") or []
    reasons: List[str] = []
    n_req_ok = 0
    for req in required:
        ok = _kind_satisfies(req, by_kind)
        if ok:
            n_req_ok += 1
            reasons.append(f"req[{req.get('kind')}] satisfied")
        else:
            reasons.append(f"req[{req.get('kind')}] FAILED "
                            f"(min_count={req.get('min_count', 1)})")
    if required and n_req_ok < len(required):
        return {"matched": False, "score": 0.0, "reasons": reasons,
                "n_req_ok": n_req_ok, "n_req_total": len(required)}
    n_opt_ok = sum(1 for o in optional if _kind_satisfies(o, by_kind))
    base = (n_req_ok / max(1, len(required))) if required else 1.0
    bonus = 0.2 * (n_opt_ok / max(1, len(optional))) if optional else 0.0
    return {
        "matched": True,
        "score": base + bonus,
        "reasons": reasons + [f"opt {n_opt_ok}/{len(optional)} ok"],
        "n_req_ok": n_req_ok, "n_req_total": len(required),
        "n_opt_ok": n_opt_ok, "n_opt_total": len(optional),
    }


# ---------------------------------------------------------------------------
# Disk loading (cached)
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_TEMPLATES_DIR = _HERE / "templates"
_TRANSLATIONS_DIR = _HERE / "equation_translations"
_DOMAIN_LIBRARIES_DIR = _HERE / "domain_libraries"
_TEMPLATES_CACHE: Dict[str, List[Dict[str, Any]]] = {}
_TRANSLATIONS_CACHE: Dict[str, Dict[str, Any]] = {}
_DOMAIN_CACHE: Dict[str, List[Dict[str, Any]]] = {}
_CACHE_LOADED_AT: Dict[str, float] = {}


def _load_json_files_in(directory: Path) -> Dict[str, Any]:
    """Load every *.json under ``directory`` into a {basename: parsed} dict."""
    out: Dict[str, Any] = {}
    if not directory.exists():
        return out
    for f in sorted(directory.glob("*.json")):
        try:
            out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
    return out


def list_templates(domain: Optional[str] = None,
                    force_reload: bool = False) -> List[Dict[str, Any]]:
    """Return all templates, optionally filtered by domain.

    Templates live as either:
      - templates/<domain>.json — array of templates
      - templates/<domain>/<id>.json — one template per file (future)

    ``domain`` filter still surfaces generic templates (they're domain-
    agnostic fallbacks). The caller ranks results.
    """
    cache_key = "_all_templates"
    if force_reload or cache_key not in _TEMPLATES_CACHE:
        loaded = _load_json_files_in(_TEMPLATES_DIR)
        all_tpl: List[Dict[str, Any]] = []
        for stem, parsed in loaded.items():
            if isinstance(parsed, list):
                for t in parsed:
                    if not isinstance(t, dict):
                        continue
                    t.setdefault("domain", stem)
                    t.setdefault("id", f"{stem}_{len(all_tpl)}")
                    all_tpl.append(t)
            elif isinstance(parsed, dict):
                parsed.setdefault("domain", stem)
                parsed.setdefault("id", stem)
                all_tpl.append(parsed)
        _TEMPLATES_CACHE[cache_key] = all_tpl
        _CACHE_LOADED_AT[cache_key] = time.time()
    all_tpl = _TEMPLATES_CACHE[cache_key]
    if not domain:
        return list(all_tpl)
    domain_l = domain.lower()
    return [t for t in all_tpl
            if t.get("domain") in (domain_l, "generic")]


def list_equation_translations(force_reload: bool = False
                                 ) -> Dict[str, Dict[str, Any]]:
    """Return {equation_form_id: translation_dict}."""
    if force_reload or not _TRANSLATIONS_CACHE:
        _TRANSLATIONS_CACHE.clear()
        _TRANSLATIONS_CACHE.update(_load_json_files_in(_TRANSLATIONS_DIR))
    return dict(_TRANSLATIONS_CACHE)


def list_domain_context_notes(domain: str,
                                force_reload: bool = False
                                ) -> List[Dict[str, Any]]:
    """Return the domain's context-notes list (each entry is a dict with
    a ``trigger`` rule + ``note`` text). When the file doesn't exist,
    returns []."""
    cache_key = f"domain:{domain}"
    if force_reload or cache_key not in _DOMAIN_CACHE:
        path = _DOMAIN_LIBRARIES_DIR / f"{domain}_context.json"
        notes: List[Dict[str, Any]] = []
        if path.exists():
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(d, list):
                    notes = [n for n in d if isinstance(n, dict)]
                elif isinstance(d, dict) and isinstance(d.get("notes"), list):
                    notes = d["notes"]
            except Exception:
                pass
        _DOMAIN_CACHE[cache_key] = notes
    return list(_DOMAIN_CACHE[cache_key])
