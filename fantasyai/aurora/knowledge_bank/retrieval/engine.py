"""
Retrieval engine — three modes, one API.

* :class:`RetrievalMode.STRUCTURED` — match findings against pattern_triggers
                                       and aliases. Deterministic, fast.
* :class:`RetrievalMode.SEMANTIC`   — vector cosine top-k against the bank.
* :class:`RetrievalMode.HYBRID`     — union of both, ranked by a weighted
                                       combination. Default for synthesis.

Public API (re-exported from ``fantasyai.aurora.knowledge_bank``)::

    retrieve(query, mode=HYBRID, domains=None, max_results=20, min_relevance=0.0)
        → List[RetrievedEntry]

    retrieve_for_state(state, max_results=20)
        → List[RetrievedEntry]

The latter is the synthesis-time entry point: it builds a query
automatically from ``state.findings`` + ``state.system_model`` and
returns entries the RAG synthesis prompt can ground itself in.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from ..schema import KnowledgeEntry
from ..storage.sqlite_store import KnowledgeBankStore, get_bank


class RetrievalMode(enum.Enum):
    STRUCTURED = "structured"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


@dataclass
class RetrievedEntry:
    """One result from the retrieval engine."""
    entry: KnowledgeEntry
    relevance: float                         # composite score in [0, 1]
    matched_via: List[str] = field(default_factory=list)  # ["pattern:oscillation", "semantic:0.71", ...]
    structured_score: float = 0.0
    semantic_score: float = 0.0


# ---------------------------------------------------------------------------
# Finding kind → pattern_type bridge
# ---------------------------------------------------------------------------

# Aurora's synthesis-vocabulary kinds map to the pattern_types entries
# in the bank carry. The bridge is intentional: synthesis-engine kinds
# describe Aurora-side findings; pattern_types describe data-side
# manifestations. Either side can be the source of truth.
FINDING_KIND_TO_PATTERN: Dict[str, List[str]] = {
    "anomaly":                    ["anomaly", "outlier"],
    "regime_change":              ["regime_shift", "structural_break"],
    "forecast_peak":              ["projection"],
    "physics_match":              ["oscillation", "decay", "growth", "diffusion"],
    "discovered_motif":           ["motif", "oscillation"],
    "discovered_dynamics":        ["governing_equation", "oscillation"],
    "latent_regime_structure":    ["regime_shift", "hidden_state"],
    "nonlinear_dependence":       ["dependence", "coupling"],
    "causal_direction":           ["causation"],
    "nonstationary_oscillation":  ["chirp", "oscillation"],
    "uneven_sampling_period":     ["oscillation"],
    "topological_structure":      ["manifold", "loop"],
    "compositional_correction":   ["composition", "simplex"],
    "multivariate_outlier":       ["outlier"],
    "cluster_structure":          ["cluster"],
    "panel_decomposition":        ["panel"],
    "survival_curve":             ["survival"],
    "spatial_autocorrelation":    ["spatial_cluster"],
    "graph_structure":            ["graph_community"],
    "treatment_effect_did":       ["treatment_effect"],
}


# ---------------------------------------------------------------------------
# Query construction from state
# ---------------------------------------------------------------------------

def _query_text_from_state(state: Dict[str, Any]) -> str:
    """Build a short natural-language query string from state.

    Combined: dominant kinds + active domain + a description of the
    headline finding(s). This is what the semantic search embeds.
    """
    parts: List[str] = []
    findings = state.get("findings") or []
    headlines = [f for f in findings if isinstance(f, dict)
                  and (f.get("headline") or f.get("kind_hint"))]
    headlines = headlines[:5]
    for f in headlines:
        title = f.get("title") or ""
        desc = f.get("description") or ""
        parts.append(title)
        if desc:
            parts.append(desc)
    sm = state.get("system_model") or {}
    template_id = sm.get("template_id")
    if template_id:
        parts.append(f"domain {template_id}")
    return ". ".join(p for p in parts if p)[:1000]


def _kinds_in_state(state: Dict[str, Any]) -> List[str]:
    findings = state.get("findings") or []
    seen: Set[str] = set()
    for f in findings:
        if not isinstance(f, dict):
            continue
        k = f.get("_synthesis_kind") or f.get("kind_hint")
        if k:
            seen.add(k)
    return sorted(seen)


def _domains_from_state(state: Dict[str, Any]) -> List[str]:
    sm = state.get("system_model") or {}
    template_id = sm.get("template_id")
    if not template_id:
        return []
    # Map a few well-known Aurora templates to KB domain tags.
    domain_map = {
        "enviro":    ["enviro", "physics"],
        "bio":       ["bio", "medical"],
        "medical":   ["medical", "bio"],
        "finance":   ["finance", "economics"],
        "economics": ["economics", "finance"],
        "industrial":["industrial", "physics"],
        "logistics": ["logistics"],
        "research":  ["research"],
        "sports":    ["sports"],
    }
    return domain_map.get(str(template_id).lower(), [str(template_id).lower()])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def retrieve(
    query: Union[str, Dict[str, Any], "np.ndarray"],
    *,
    mode: RetrievalMode = RetrievalMode.HYBRID,
    domains: Optional[Sequence[str]] = None,
    max_results: int = 20,
    min_relevance: float = 0.0,
    finding_kinds: Optional[Sequence[str]] = None,
    bank: Optional[KnowledgeBankStore] = None,
) -> List[RetrievedEntry]:
    """Retrieve relevant knowledge entries for a query.

    Parameters
    ----------
    query
        Natural-language string OR a state dict. (Vector input also
        accepted; passed straight to the vector index.)
    mode
        STRUCTURED / SEMANTIC / HYBRID.
    domains
        Restrict to entries tagged with any of these domains.
    max_results
        Top-k cap.
    min_relevance
        Drop entries below this composite score.
    finding_kinds
        When provided, structured retrieval restricts pattern matching
        to the pattern_types these kinds map to.
    bank
        Override the singleton (mostly for tests).
    """
    bank = bank or get_bank()

    if mode == RetrievalMode.STRUCTURED:
        return _retrieve_structured(bank, query, domains, max_results,
                                       min_relevance, finding_kinds)
    if mode == RetrievalMode.SEMANTIC:
        return _retrieve_semantic(bank, query, domains, max_results,
                                     min_relevance)
    return _retrieve_hybrid(bank, query, domains, max_results,
                              min_relevance, finding_kinds)


def retrieve_for_state(
    state: Dict[str, Any],
    *,
    max_results: int = 20,
    bank: Optional[KnowledgeBankStore] = None,
) -> List[RetrievedEntry]:
    """Auto-build a query from state and retrieve. Used by RAG synthesis."""
    text = _query_text_from_state(state) or "general analytical findings"
    domains = _domains_from_state(state) or None
    kinds = _kinds_in_state(state) or None
    return retrieve(text,
                     mode=RetrievalMode.HYBRID,
                     domains=domains,
                     max_results=max_results,
                     finding_kinds=kinds,
                     bank=bank)


# ---------------------------------------------------------------------------
# Mode implementations
# ---------------------------------------------------------------------------

def _retrieve_structured(bank, query, domains, max_results, min_rel,
                           finding_kinds) -> List[RetrievedEntry]:
    candidate_ids: Dict[str, Tuple[float, List[str]]] = {}
    # Pattern-type triggers from finding_kinds.
    if finding_kinds:
        for k in finding_kinds:
            patterns = FINDING_KIND_TO_PATTERN.get(k) or [k]
            for p in patterns:
                ids = bank.find_by_pattern(p, domains=domains, limit=20)
                for eid in ids:
                    score, ms = candidate_ids.get(eid, (0.0, []))
                    candidate_ids[eid] = (score + 0.6,
                                            ms + [f"pattern:{p}"])
    # Alias / name lookups when query is a string.
    if isinstance(query, str):
        # Try first 2 words as an alias prefix.
        first_words = query.lower().split()
        prefix = " ".join(first_words[:2])
        if prefix:
            ids = bank.find_by_alias(prefix, domains=domains, limit=10)
            for eid in ids:
                score, ms = candidate_ids.get(eid, (0.0, []))
                candidate_ids[eid] = (score + 0.4,
                                        ms + [f"alias:{prefix}"])
    # Hydrate.
    if not candidate_ids:
        return []
    # Sort by structured score and cap.
    ordered = sorted(candidate_ids.items(),
                      key=lambda kv: -kv[1][0])[:max_results]
    ids = [eid for eid, _ in ordered]
    entries = bank.get_many(ids)
    by_id = {e.id: e for e in entries}
    results: List[RetrievedEntry] = []
    for eid, (score, ms) in ordered:
        e = by_id.get(eid)
        if e is None:
            continue
        rel = min(1.0, score)
        if rel < min_rel:
            continue
        results.append(RetrievedEntry(
            entry=e, relevance=rel, matched_via=ms,
            structured_score=score, semantic_score=0.0,
        ))
    return results


def _retrieve_semantic(bank, query, domains, max_results, min_rel
                          ) -> List[RetrievedEntry]:
    pairs = bank.search_vector(query, top_k=max_results, domains=domains)
    if not pairs:
        return []
    entries = bank.get_many([eid for eid, _ in pairs])
    by_id = {e.id: e for e in entries}
    out: List[RetrievedEntry] = []
    for eid, sim in pairs:
        e = by_id.get(eid)
        if e is None:
            continue
        if sim < min_rel:
            continue
        out.append(RetrievedEntry(
            entry=e, relevance=float(sim),
            matched_via=[f"semantic:{sim:.3f}"],
            structured_score=0.0, semantic_score=float(sim),
        ))
    return out


def _retrieve_hybrid(bank, query, domains, max_results, min_rel,
                       finding_kinds) -> List[RetrievedEntry]:
    """Combine structured + semantic with structured weighted higher.

    The hybrid ranker is intentionally simple: relevance =
    0.65 * structured_score + 0.35 * semantic_score, capped at 1.
    Structured matches that ALSO appear in the semantic top-k get a
    small co-occurrence boost.
    """
    # Get structured candidates (more than max_results so semantic can re-rank).
    structured = _retrieve_structured(bank, query, domains,
                                          max_results=max_results * 2,
                                          min_rel=0.0,
                                          finding_kinds=finding_kinds)
    semantic = _retrieve_semantic(bank, query, domains,
                                       max_results=max_results * 2,
                                       min_rel=0.0)
    by_id: Dict[str, RetrievedEntry] = {}
    for r in structured:
        by_id[r.entry.id] = r
    for r in semantic:
        if r.entry.id in by_id:
            existing = by_id[r.entry.id]
            existing.semantic_score = r.semantic_score
            existing.matched_via = list(set(existing.matched_via
                                              + r.matched_via))
        else:
            by_id[r.entry.id] = r
    # Composite scoring + co-occurrence boost.
    for r in by_id.values():
        co_boost = 0.1 if (r.structured_score > 0 and r.semantic_score > 0) else 0.0
        r.relevance = min(1.0, 0.65 * min(1.0, r.structured_score)
                                 + 0.35 * r.semantic_score
                                 + co_boost)
    ordered = sorted(by_id.values(), key=lambda r: -r.relevance)
    return [r for r in ordered if r.relevance >= min_rel][:max_results]
