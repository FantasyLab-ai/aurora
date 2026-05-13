"""
SeedRanker — Workstream 3 of the pre-launch sprint.

Pure-function ranker; no LLM. Given a state's findings + a user's seed,
produces two ordered lists:

  - relevant_findings: ranked by RELEVANCE to the seed
                        (keyword + kind + confidence + referent + severity)
  - surprise_findings: ranked by SURPRISE — strong findings the user
                        did NOT ask about (significance + unusualness +
                        contradiction + novelty + confidence)

The ranker NEVER excludes or hides — every finding is still in
state["findings"]. Only the prioritisation changes when a seed is set.

Weights are config-driven (~/.aurora/seed_ranker.json). Defaults match
the brief; per-run override possible via state["run_meta"]["seed_ranker_override"].

Acceptance: Spearman correlation against a hand-labelled fixture
(tests/fixtures/seed_ranker/golden_relevance.jsonl) ≥ 0.70 median.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Config — defaults ship hard-coded; user override via JSON file
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": "v1",
    "relevance": {
        "weights": {
            "keyword":    0.40,
            "kind":       0.25,
            "confidence": 0.15,
            "referent":   0.10,
            "severity":   0.10,
        },
        "section_threshold": 0.30,
        "max_results": 8,
    },
    "surprise": {
        "weights": {
            "significance":  0.40,
            "unusualness":   0.25,
            "contradiction": 0.20,
            "novelty":       0.10,
            "confidence":    0.05,
        },
        "section_threshold": 0.50,
        "max_results": 5,
    },
    "kind_synonyms": {
        "anomaly":       ["anomaly", "outlier", "spike", "weird",
                           "unusual", "off", "abnormal"],
        "regime":        ["regime", "shift", "phase", "transition",
                           "change", "switch"],
        "forecast":      ["forecast", "predict", "projection", "next",
                           "future", "upcoming", "ahead", "trajectory"],
        "physics_match": ["law", "model", "fit", "rule", "equation",
                           "relationship", "principle"],
        "causal":        ["effect", "cause", "impact", "drive", "lead to",
                           "because", "result"],
    },
    "stopwords": [
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "of", "in", "on", "at", "to", "for", "and", "or", "but", "with",
        "what", "is", "the", "any", "do", "does", "did", "have", "has",
        "i", "we", "you", "they", "he", "she", "it",
        "want", "need", "look", "see", "find", "show",
        "data", "dataset",
    ],
}


_CACHE: Dict[str, Any] = {"path": None, "mtime": 0.0, "config": None,
                          "loaded_at": 0.0}


def _config_path() -> Path:
    override = os.environ.get("AURORA_SEED_RANKER_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".aurora" / "seed_ranker.json"


def load_config(path: Optional[Path] = None,
                ttl_seconds: float = 60.0) -> Dict[str, Any]:
    """Load + cache the ranker config. Falls back to DEFAULT_CONFIG cleanly."""
    p = path or _config_path()
    now = time.time()
    if (_CACHE["path"] == str(p)
            and _CACHE["config"] is not None
            and (now - _CACHE["loaded_at"]) < ttl_seconds):
        return _CACHE["config"]
    cfg = _deep_merge(DEFAULT_CONFIG, {})
    if p.exists():
        try:
            user_cfg = json.loads(p.read_text(encoding="utf-8"))
            cfg = _deep_merge(cfg, user_cfg)
        except Exception:
            pass
    _CACHE.update(path=str(p), config=cfg, loaded_at=now)
    return cfg


def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge b into a; b wins on conflicts. Returns a new dict."""
    out = dict(a)
    for k, v in (b or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Tokenisation
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str, stopwords: Optional[set] = None) -> List[str]:
    if not text:
        return []
    s = str(text).lower()
    raw = _TOKEN_RE.findall(s)
    if stopwords is None:
        return raw
    return [t for t in raw if t not in stopwords and len(t) > 1]


def _expand_with_synonyms(seed_tokens: List[str],
                          kind_synonyms: Dict[str, List[str]]) -> List[str]:
    """For each kind whose synonym list overlaps the seed, add the canonical
    kind name to the token bag — and add all of its synonyms so the
    keyword-jaccard captures domain language used in titles."""
    expanded = list(seed_tokens)
    seed_set = set(seed_tokens)
    for kind, syns in kind_synonyms.items():
        if any(s in seed_set for s in syns):
            expanded.append(kind)
            expanded.extend(syns)
    return expanded


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------

@dataclass
class RankedFinding:
    finding: Dict[str, Any]
    score: float
    components: Dict[str, float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "finding": self.finding,
            "score": float(self.score),
            "components": {k: float(v) for k, v in self.components.items()},
        }


def _kind_of(finding: Dict[str, Any]) -> str:
    """Best-effort finding classifier — mirrors narrative_engine.resolve_finding_kind
    but stays decoupled so seed_ranker can be used without the engine."""
    title = (finding.get("title") or "").lower()
    method = (finding.get("method") or "").lower()
    if "anomaly" in title or "iso-forest" in method or "iso forest" in method:
        return "anomaly"
    if title.startswith("forecast") or "forecast" in method:
        return "forecast"
    if "regime" in title or "regime" in method:
        return "regime"
    if "matches known law" in title or "physical model fit" in title \
            or "physics_prior" in method:
        return "physics_match"
    if "causal" in title or "causal" in method:
        return "causal"
    return "generic"


def _seed_kind_hints(seed_tokens: List[str],
                     kind_synonyms: Dict[str, List[str]]) -> List[str]:
    """Which finding kinds does the seed reference?"""
    hints: List[str] = []
    seed_set = set(seed_tokens)
    for kind, syns in kind_synonyms.items():
        if any(s in seed_set for s in syns) or kind in seed_set:
            hints.append(kind)
    return hints


def _jaccard(a: List[str], b: List[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return (inter / union) if union else 0.0


def _severity_score(severity: str) -> float:
    return {"crit": 1.0, "warn": 0.7, "info": 0.4, "ok": 0.2}.get(severity, 0.3)


def _referent_match(finding: Dict[str, Any],
                    seed_text: str,
                    template_id: Optional[str]) -> float:
    """1.0 when seed text directly mentions the referent label;
    0.5 when same template_id; 0.0 otherwise."""
    ref = finding.get("referent") or {}
    label = (ref.get("primary_label") or "").lower()
    seed_l = (seed_text or "").lower()
    if label and label in seed_l:
        return 1.0
    # Approximate: any token from referent label appears in seed
    label_toks = _tokens(label)
    seed_toks  = _tokens(seed_text)
    if label_toks and any(t in seed_toks for t in label_toks):
        return 0.7
    if template_id and template_id in seed_l:
        return 0.5
    return 0.0


def score_relevance(finding: Dict[str, Any],
                    seed_text: str,
                    *,
                    template_id: Optional[str] = None,
                    config: Optional[Dict[str, Any]] = None) -> RankedFinding:
    """Compute a [0,1] relevance score + component breakdown for ONE finding."""
    cfg = config or load_config()
    weights = cfg["relevance"]["weights"]
    stop = set(cfg.get("stopwords") or [])
    kind_syns = cfg["kind_synonyms"]
    seed_toks = _tokens(seed_text, stopwords=stop)
    expanded_seed = _expand_with_synonyms(seed_toks, kind_syns)

    # Build the finding's "bag of words" from title + description + method.
    finding_text_parts = [
        finding.get("title", ""),
        finding.get("description", ""),
        finding.get("method", ""),
    ]
    fi_toks = _tokens(" ".join(finding_text_parts), stopwords=stop)

    keyword_s = _jaccard(expanded_seed, fi_toks)
    if keyword_s == 0.0 and seed_toks and fi_toks:
        # Soft-match: substring presence of any seed token in finding text.
        ftext = " ".join(fi_toks)
        if any(t in ftext for t in seed_toks):
            keyword_s = 0.15

    # Kind match.
    seed_kinds = _seed_kind_hints(expanded_seed, kind_syns)
    f_kind = _kind_of(finding)
    kind_s = 1.0 if f_kind in seed_kinds else 0.0

    confidence_s = float(finding.get("confidence") or 0.0)
    referent_s = _referent_match(finding, seed_text, template_id)
    severity_s = _severity_score(finding.get("severity", "info"))

    components = {
        "keyword":    keyword_s,
        "kind":       kind_s,
        "confidence": confidence_s,
        "referent":   referent_s,
        "severity":   severity_s,
    }
    score = sum(weights.get(k, 0.0) * v for k, v in components.items())
    return RankedFinding(finding=finding, score=float(score),
                          components=components)


# ---------------------------------------------------------------------------
# Surprise scoring
# ---------------------------------------------------------------------------

# A finding "contradicts" the seed when the seed's verb implies stability
# but the finding signals change (or vice versa).
_STABILITY_HINTS = {"stable", "steady", "consistent", "calm", "normal",
                     "baseline", "typical"}
_CHANGE_HINTS = {"shift", "change", "regime", "anomaly", "spike",
                  "break", "outlier", "transition"}


def _contradiction_score(finding: Dict[str, Any],
                         seed_tokens: List[str]) -> float:
    seed_set = set(seed_tokens)
    title = (finding.get("title") or "").lower()
    finding_signals_change = any(h in title for h in _CHANGE_HINTS)
    finding_signals_stable = any(h in title for h in _STABILITY_HINTS)
    seed_implies_stable = bool(_STABILITY_HINTS & seed_set)
    seed_implies_change = bool(_CHANGE_HINTS & seed_set)
    if seed_implies_stable and finding_signals_change:
        return 1.0
    if seed_implies_change and finding_signals_stable:
        return 1.0
    return 0.3   # baseline — most findings are neither contradicting nor confirming


def _significance(finding: Dict[str, Any]) -> float:
    """Combined evidence of how significant this finding is. Tries z, score,
    and severity in order; returns [0, 1]."""
    z = finding.get("z")
    score = finding.get("score")
    if z is not None:
        try:
            return min(1.0, abs(float(z)) / 5.0)
        except Exception:
            pass
    if score is not None:
        try:
            return min(1.0, abs(float(score)) / 5.0)
        except Exception:
            pass
    return _severity_score(finding.get("severity", "info"))


def _novelty(finding: Dict[str, Any]) -> float:
    rs = finding.get("replication_status") or {}
    status = rs.get("status")
    return {
        "novel": 1.0,
        "strengthened": 0.5,
        "confirmed": 0.3,
        "weakened": 0.2,
        "contradicted": 0.1,
        "pending": 0.4,
    }.get(status, 0.3)


def score_surprise(finding: Dict[str, Any],
                   seed_text: str,
                   relevance_value: float,
                   *,
                   config: Optional[Dict[str, Any]] = None) -> RankedFinding:
    cfg = config or load_config()
    weights = cfg["surprise"]["weights"]
    stop = set(cfg.get("stopwords") or [])
    seed_toks = _tokens(seed_text, stopwords=stop)

    significance = _significance(finding)
    unusualness = max(0.0, 1.0 - relevance_value)   # explicitly orthogonal to relevance
    contradiction = _contradiction_score(finding, seed_toks)
    novelty = _novelty(finding)
    confidence = float(finding.get("confidence") or 0.0)

    components = {
        "significance":  significance,
        "unusualness":   unusualness,
        "contradiction": contradiction,
        "novelty":       novelty,
        "confidence":    confidence,
    }
    score = sum(weights.get(k, 0.0) * v for k, v in components.items())
    return RankedFinding(finding=finding, score=float(score),
                          components=components)


# ---------------------------------------------------------------------------
# Section assembly
# ---------------------------------------------------------------------------

def rank(findings: List[Dict[str, Any]],
         seed_text: str,
         *,
         template_id: Optional[str] = None,
         config: Optional[Dict[str, Any]] = None
         ) -> Tuple[List[RankedFinding], List[RankedFinding]]:
    """Walk findings, score each for relevance + surprise, return two
    ordered lists matching the dual-section spec.

    Returns (relevant, surprise). When seed_text is empty or whitespace,
    BOTH lists are empty and the caller falls back to legacy unified
    ordering.
    """
    if not seed_text or not seed_text.strip():
        return [], []
    cfg = config or load_config()
    relevance_threshold = float(cfg["relevance"]["section_threshold"])
    surprise_threshold  = float(cfg["surprise"]["section_threshold"])
    max_relevant = int(cfg["relevance"]["max_results"])
    max_surprise = int(cfg["surprise"]["max_results"])

    # 1. Score relevance for every finding.
    relevance_scored: List[RankedFinding] = [
        score_relevance(f, seed_text, template_id=template_id, config=cfg)
        for f in findings if isinstance(f, dict)
    ]
    relevance_scored.sort(key=lambda r: -r.score)

    relevant = [r for r in relevance_scored
                if r.score >= relevance_threshold][:max_relevant]
    relevant_set = set(id(r.finding) for r in relevant)

    # 2. Score surprise on the rest. Surprise uses the actual finding's
    #    relevance score so unusualness = 1 - relevance is honest.
    surprise_pool = [r for r in relevance_scored
                     if id(r.finding) not in relevant_set]
    surprise_scored = [
        score_surprise(r.finding, seed_text, relevance_value=r.score,
                        config=cfg)
        for r in surprise_pool
    ]
    surprise_scored.sort(key=lambda r: -r.score)
    surprise = [r for r in surprise_scored
                if r.score >= surprise_threshold][:max_surprise]

    return relevant, surprise


def attach_to_state(state: Dict[str, Any],
                    seed_text: Optional[str],
                    *,
                    template_id: Optional[str] = None,
                    config: Optional[Dict[str, Any]] = None) -> None:
    """Mutate ``state`` in-place: add ``relevant_findings`` and
    ``surprise_findings`` arrays when seed_text is non-empty.

    ``state["findings"]`` is left untouched — the seed only changes
    surface prioritisation, never excludes from the underlying analysis.
    """
    if not isinstance(state, dict):
        return
    findings = state.get("findings") or []
    if not seed_text or not seed_text.strip():
        # Be explicit: when there's no seed, drop any stale dual-section keys.
        state.pop("relevant_findings", None)
        state.pop("surprise_findings", None)
        state["seed_ranker"] = {"active": False}
        return
    relevant, surprise = rank(findings, seed_text,
                                template_id=template_id, config=config)
    state["relevant_findings"] = [r.to_dict() for r in relevant]
    state["surprise_findings"] = [r.to_dict() for r in surprise]
    state["seed_ranker"] = {
        "active": True,
        "seed_text": seed_text,
        "template_id": template_id,
        "n_findings_total": len(findings),
        "n_relevant": len(relevant),
        "n_surprise": len(surprise),
    }


__all__ = [
    "DEFAULT_CONFIG",
    "load_config",
    "score_relevance",
    "score_surprise",
    "rank",
    "attach_to_state",
    "RankedFinding",
]
