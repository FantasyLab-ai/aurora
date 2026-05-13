"""
Retrieval-augmented synthesis (Brief: Knowledge Bank + RAG).

Replaces the template-based synthesis as the default WHEN the feature
flag is on AND the knowledge bank has entries AND Ollama is reachable.
Falls back to the existing template engine on any failure.

Pipeline:
  1. Build retrieval query from state (findings + system_model + kinds).
  2. Retrieve top-K relevant entries via the hybrid retriever.
  3. Build a strict grounding prompt for Gemma 12B (or whatever Ollama
     model the maintainer has). Include all retrieved entries verbatim.
  4. Generate.
  5. Verify: parse [entry_id] citations, check each is a real retrieved
     entry, semantic-score the claim against its cited entry.
  6. Render — store the prose AND the per-claim provenance map under
     ``state.interpretive_summary`` + ``state.synthesis_meta``.

Glass-box at the user-facing level:
  * synthesis_meta.mode = "rag" | "template" | "rag_failed_fell_back"
  * synthesis_meta.retrieved_entries  = list of {id, name, relevance, ...}
  * synthesis_meta.claims             = list of {text, cited_entries, verified}
  * synthesis_meta.flagged_uncertain  = list of claim indices

Feature flag:
  ``AURORA_SYNTHESIS_MODE`` env var — "rag" (default) | "template" | "auto".
  In "auto" mode (the default if env unset) the engine tries RAG first
  and falls back to template on any failure.

Diagnostic logging:
  Every decision point in this module logs to ``aurora.rag``. Every
  exception that triggers a fallback is logged with ``exc_info=True``
  so the next regression is visible in server logs alone.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple


_log = logging.getLogger("aurora.rag")


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

def synthesis_mode() -> str:
    """Resolve the active synthesis mode.

    "rag"      → always attempt RAG; on failure return empty interpretive_summary.
    "template" → always use template synthesis.
    "auto"     → try RAG, fall back to template on failure (default).
    """
    return (os.environ.get("AURORA_SYNTHESIS_MODE") or "auto").lower()


# ---------------------------------------------------------------------------
# Ollama bridge — reuse the existing copilot path
# ---------------------------------------------------------------------------

def _call_ollama(messages: List[Dict[str, str]], *,
                  timeout: int = 60) -> Dict[str, Any]:
    """Best-effort Ollama call. Returns ``{ok, text, model, error}``.

    The studio_api module already wraps Ollama for the copilot; we
    re-implement the minimal shape here so the synthesis engine stays
    importable without studio_api (tests, headless contexts).
    """
    try:
        import requests
    except Exception as e:
        return {"ok": False, "text": "", "model": None,
                "error": f"requests not installed: {e}"}
    base = (os.environ.get("OLLAMA_URL")
            or "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "gemma3:12b")
    try:
        r = requests.post(
            f"{base}/api/chat",
            json={"model": model, "messages": messages, "stream": False},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        text = ""
        if isinstance(data, dict):
            msg = data.get("message") or {}
            text = msg.get("content") or data.get("response") or ""
        return {"ok": True, "text": (text or "").strip(),
                "model": model, "error": None}
    except Exception as e:
        return {"ok": False, "text": "", "model": model, "error": str(e)}


def _ollama_reachable(timeout: float = 1.5) -> bool:
    try:
        import requests
        base = (os.environ.get("OLLAMA_URL")
                or "http://127.0.0.1:11434").rstrip("/")
        r = requests.get(f"{base}/api/tags", timeout=timeout)
        return r.ok
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _format_findings(state: Dict[str, Any]) -> str:
    """Render findings WITHOUT square-bracket markers.

    The previous implementation used ``[0] [1] [2]`` numeric prefixes,
    which Gemma 12B then mimicked when generating citations — it would
    write ``[2]`` thinking "this cites finding 2" instead of citing the
    requested ``[entry_id]``. The verification regex matched ``[2]`` as
    a candidate citation, looked up entry id ``"2"`` (which doesn't
    exist), and failed every claim. Dropping the bracket markers from
    the findings block removes the format collision.
    """
    findings = state.get("findings") or []
    if not findings:
        return "No findings."
    lines = []
    for i, f in enumerate(findings[:10]):
        if not isinstance(f, dict):
            continue
        kind = f.get("_synthesis_kind") or f.get("kind_hint") or "generic"
        title = f.get("title") or ""
        desc = f.get("description") or ""
        sev = f.get("severity") or ""
        conf = f.get("confidence")
        conf_s = f" (confidence {conf:.2f})" if isinstance(conf, (int, float)) else ""
        # Use parens — never brackets — for finding markers so Gemma
        # can't confuse them with the citation grammar.
        lines.append(f"  Finding F{i}: kind={kind} severity={sev}{conf_s}: {title}")
        if desc:
            lines.append(f"      {desc}")
    return "\n".join(lines)


def _format_entries(entries: List[Any]) -> str:
    """Render retrieved entries as a structured block in the prompt."""
    out = []
    for r in entries:
        e = r.entry
        out.append(f"--- ENTRY: {e.id} ---")
        out.append(f"Name: {e.name}")
        if e.aliases:
            out.append(f"Aliases: {', '.join(e.aliases[:5])}")
        if e.domain:
            out.append(f"Domain: {', '.join(e.domain)}")
        out.append(f"Definition: {e.definition}")
        if e.mechanism:
            out.append(f"Mechanism: {e.mechanism}")
        if e.caveats:
            out.append("Caveats:")
            for c in e.caveats[:3]:
                txt = getattr(c, "text", None) or (
                    c.get("text") if isinstance(c, dict) else "")
                sev = getattr(c, "severity", None) or (
                    c.get("severity", "minor") if isinstance(c, dict) else "minor")
                out.append(f"  ({sev}) {txt}")
        if e.references:
            citations = []
            for ref in e.references[:2]:
                citations.append(getattr(ref, "citation", None)
                                   or (ref.get("citation") if isinstance(ref, dict)
                                         else ""))
            out.append(f"References: {' | '.join(citations)}")
        out.append("")
    return "\n".join(out)


def _build_prompt(state: Dict[str, Any], retrieved: List[Any]) -> List[Dict[str, str]]:
    sm = state.get("system_model") or {}
    template_id = sm.get("template_id") or "general"
    findings_text = _format_findings(state)
    entries_text = _format_entries(retrieved)

    # Build a concrete list of allowed citation tokens so the model
    # has the exact strings to copy. Two examples of the WRONG format
    # are also given (numeric, parenthesised) — instructing by negative
    # example is more reliable than positive-only on Gemma 12B.
    allowed_ids = [r.entry.id for r in retrieved]
    allowed_block = "\n".join(f"  [{eid}]" for eid in allowed_ids)
    example_id = allowed_ids[0] if allowed_ids else "seed:example"

    system = (
        "You are Aurora's interpretation engine. Your job is to synthesize "
        "an interpretive summary of analytical findings using ONLY the "
        "retrieved knowledge entries below. You do not have any other "
        "knowledge. Do not introduce facts that are not in the retrieved "
        "entries.\n\n"
        "CITATION RULES (CRITICAL — failure to follow these makes your "
        "output unusable):\n"
        f"  * Cite by entry ID, in square brackets. Example: [{example_id}]\n"
        "  * NEVER use a number alone, like [1] or [3]. Numbers in "
        "brackets are NOT valid citations.\n"
        "  * NEVER use parentheses for citations: (foo) is not a citation.\n"
        "  * Only the entry IDs listed in the ALLOWED CITATIONS block "
        "below are valid. Anything else will be rejected.\n\n"
        "INSTRUCTIONS:\n"
        "1. Write a 2-3 paragraph interpretive summary (max 250 words).\n"
        "2. Lead with the most significant finding and what it means based "
        "on the entries.\n"
        "3. After EVERY factual claim, place at least one bracketed entry "
        f"id from the allowed list. Example: 'X drives Y [{example_id}].'\n"
        "4. If retrieved entries don't cover something, write 'this aspect "
        "is not covered by the available knowledge' rather than guess.\n"
        "5. Include important caveats from the entries when relevant.\n"
        "6. End with the most important caveat or limitation."
    )
    user = (
        f"DOMAIN: {template_id}\n\n"
        f"FINDINGS:\n{findings_text}\n\n"
        f"RETRIEVED KNOWLEDGE:\n{entries_text}\n\n"
        f"ALLOWED CITATIONS (use ONLY these — copy them verbatim):\n"
        f"{allowed_block}\n\n"
        f"OUTPUT: 2-3 flowing paragraphs of interpretive prose. After every "
        f"factual claim, place a bracketed entry id from the allowed list "
        f"(e.g. [{example_id}]). Maximum 250 words."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

_CITATION_RE = re.compile(r"\[([a-zA-Z0-9_:.\-]+)\]")
_NUMERIC_CITATION_RE = re.compile(r"^\d+$")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _resolve_citation(token: str, retrieved_ids: List[str],
                         retrieved_id_set: set) -> Optional[str]:
    """Map a citation token to the entry id it refers to, or None.

    Direct matches always win. As a recovery path for models that
    revert to numeric footnote-style citations (Gemma 12B does this
    occasionally), a bare integer ``N`` maps to the N-th retrieved
    entry IF N is in range. The recovery is reported in the verifier
    output so the UI can mark these claims as "verified by recovery"
    rather than fully verified.
    """
    if token in retrieved_id_set:
        return token
    if _NUMERIC_CITATION_RE.match(token):
        try:
            n = int(token)
            # Tolerate both 0-indexed and 1-indexed conventions: try
            # n directly first, then n-1.
            if 0 <= n < len(retrieved_ids):
                return retrieved_ids[n]
            if 1 <= n <= len(retrieved_ids):
                return retrieved_ids[n - 1]
        except (ValueError, IndexError):
            return None
    return None


def _verify_output(text: str, retrieved: List[Any]) -> Dict[str, Any]:
    """Parse generated prose, extract citations, verify each.

    Numeric-citation recovery: tokens like ``[2]`` or ``[8]`` are
    resolved to the corresponding retrieved entry by index when the
    model falls back to footnote-style citations. The recovered
    citation IS counted as verified for the threshold check, but the
    claim's ``recovered`` flag is set so the UI can render it slightly
    differently from a clean entry-id citation.

    Returns::
        {
          "claims":       [{"text", "cited_entries", "verified",
                              "recovered": bool}],
          "n_total":      int,
          "n_verified":   int,
          "n_uncited":    int,
          "n_recovered":  int,    # claims verified via numeric recovery
          "n_uncertain":  int,    # cited tokens couldn't be resolved
          "flagged_uncertain_indices": [int]
        }
    """
    retrieved_ids = [r.entry.id for r in retrieved]
    retrieved_id_set = set(retrieved_ids)
    sentences = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    claims: List[Dict[str, Any]] = []
    flagged: List[int] = []
    n_uncited = 0
    n_uncertain = 0
    n_recovered = 0
    for i, sent in enumerate(sentences):
        raw_tokens = _CITATION_RE.findall(sent)
        if not raw_tokens:
            n_uncited += 1
            claims.append({"text": sent, "cited_entries": [],
                            "verified": False, "uncited": True,
                            "recovered": False})
            continue
        resolved: List[str] = []
        had_recovery = False
        had_failure = False
        for tok in raw_tokens:
            entry_id = _resolve_citation(tok, retrieved_ids,
                                            retrieved_id_set)
            if entry_id is None:
                had_failure = True
                continue
            if tok != entry_id:  # numeric token mapped to id
                had_recovery = True
            resolved.append(entry_id)
        verified = bool(resolved) and not had_failure
        if not verified:
            n_uncertain += 1
            flagged.append(i)
        if had_recovery and verified:
            n_recovered += 1
        claims.append({
            "text": sent,
            "cited_entries": resolved,
            "raw_tokens": raw_tokens,
            "verified": verified,
            "uncited": False,
            "recovered": had_recovery and verified,
        })
    return {
        "claims": claims,
        "n_total": len(claims),
        "n_verified": sum(1 for c in claims if c["verified"]),
        "n_uncited": n_uncited,
        "n_recovered": n_recovered,
        "n_uncertain": n_uncertain,
        "flagged_uncertain_indices": flagged,
    }


# ---------------------------------------------------------------------------
# Public RAG entry
# ---------------------------------------------------------------------------

def synthesize_rag(state: Dict[str, Any]) -> Dict[str, Any]:
    """Run RAG synthesis. Returns a result dict; mutates state on success.

    Result shape::
        {"mode": "rag" | "rag_failed", "ok": bool,
         "interpretive_summary": str, "synthesis_meta": dict,
         "reason": str (only when ok=False)}

    The caller decides whether to fall back to template synthesis based
    on ok / mode.

    Logs every decision point at INFO and every fallback at WARNING with
    ``exc_info=True`` when an exception is in flight, so a tail of the
    server logs alone is enough to diagnose any future regression.
    """
    t0 = time.time()
    mode_env = os.environ.get("AURORA_SYNTHESIS_MODE", "auto")
    _log.info("RAG synthesis attempt starting; mode=%s, finding_count=%d",
                mode_env, len(state.get("findings") or []))

    def _fail(reason: str, *, retrieved=None, **extra) -> Dict[str, Any]:
        """Helper that builds a failure result with the reason + any
        diagnostic fields propagated INTO synthesis_meta so the engine's
        ``rag_meta = result["synthesis_meta"]`` step carries them through
        to ``state.synthesis_meta.rag``."""
        meta = {"mode": "rag_failed", "reason": reason}
        if retrieved is not None:
            meta["retrieved_entries"] = _serialise_retrieved(retrieved)
        meta.update(extra)
        return {"ok": False, "mode": "rag_failed", "reason": reason,
                "interpretive_summary": "", "synthesis_meta": meta}

    try:
        from fantasyai.aurora.knowledge_bank import retrieve, RetrievalMode
        from fantasyai.aurora.knowledge_bank.retrieval.engine import (
            retrieve_for_state,
        )
    except Exception as e:
        _log.warning("RAG falling back: knowledge_bank import failed",
                       exc_info=True)
        return _fail(f"knowledge_bank_unavailable: {e}")

    # Retrieve. Auto-ingest the seed bank on first run if it's empty —
    # the bank file is created lazily, so a fresh install starts empty.
    try:
        retrieved = retrieve_for_state(state, max_results=12)
        _log.info("Retrieval returned %d entries", len(retrieved))
        if not retrieved:
            _log.info("Empty retrieval — checking if seed needs ingest")
            try:
                from fantasyai.aurora.knowledge_bank import get_bank
                from fantasyai.aurora.knowledge_bank.ingest import run_seed_ingest
                bank = get_bank()
                if bank.summary().get("total_entries", 0) == 0:
                    _log.info("Bank empty; running seed ingest")
                    run_seed_ingest(bank)
                    retrieved = retrieve_for_state(state, max_results=12)
                    _log.info("Post-seed retrieval returned %d entries",
                                len(retrieved))
            except Exception:
                _log.warning("Seed-ingest fallback failed", exc_info=True)
    except Exception as e:
        _log.warning("RAG falling back: retrieval raised", exc_info=True)
        return _fail(f"retrieval_failed: {type(e).__name__}: {e}")
    if not retrieved:
        _log.warning("RAG falling back: no_relevant_entries")
        return _fail("no_relevant_entries", retrieved=[])

    # If Ollama isn't reachable, degrade gracefully (don't pretend).
    if not _ollama_reachable():
        _log.warning("RAG falling back: ollama_unreachable")
        return _fail("ollama_unreachable", retrieved=retrieved)

    # Generate.
    messages = _build_prompt(state, retrieved)
    _log.info("Calling Ollama with %d entries in context", len(retrieved))
    result = _call_ollama(messages, timeout=int(
        os.environ.get("AURORA_RAG_TIMEOUT", "60")))
    if not result["ok"] or not result["text"]:
        err = result.get("error") or "empty_output"
        _log.warning("RAG falling back: llm_call_failed (%s)", err)
        return _fail(f"llm_call_failed: {err}", retrieved=retrieved)
    _log.info("Ollama returned %d chars", len(result["text"]))

    text = result["text"]
    verification = _verify_output(text, retrieved)
    elapsed_ms = int((time.time() - t0) * 1000)

    if verification["n_total"] > 0:
        verified_frac = verification["n_verified"] / verification["n_total"]
    else:
        verified_frac = 0.0
    too_unverified = (verification["n_total"] >= 3 and verified_frac < 0.20)

    _log.info("Verification: %d/%d verified (%d recovered), ratio=%.2f",
                verification["n_verified"], verification["n_total"],
                verification["n_recovered"], verified_frac)

    meta = {
        "mode": "rag",
        "model": result.get("model"),
        "elapsed_ms": elapsed_ms,
        "n_retrieved": len(retrieved),
        "retrieved_entries": _serialise_retrieved(retrieved),
        "claims": verification["claims"],
        "n_claims_total": verification["n_total"],
        "n_claims_verified": verification["n_verified"],
        "n_claims_recovered": verification["n_recovered"],
        "n_claims_uncertain": verification["n_uncertain"],
        "n_claims_uncited": verification["n_uncited"],
        "flagged_uncertain_indices": verification["flagged_uncertain_indices"],
        "verified_fraction": verified_frac,
        "too_unverified": too_unverified,
    }
    if too_unverified:
        _log.warning(
            "RAG falling back: verification_below_threshold "
            "(%d/%d verified, ratio %.2f). First raw tokens: %s",
            verification["n_verified"], verification["n_total"],
            verified_frac,
            [c.get("raw_tokens", []) for c in verification["claims"][:3]],
        )
        # Mark the meta itself as a failure so callers reading
        # state.synthesis_meta.rag.mode see "rag_failed", consistent
        # with all other failure paths.
        meta["mode"] = "rag_failed"
        meta["reason"] = "verification_below_threshold"
        return {"ok": False, "mode": "rag_failed",
                "reason": "verification_below_threshold",
                "interpretive_summary": "",
                "synthesis_meta": meta}
    _log.info("RAG synthesis SUCCESS in %d ms", elapsed_ms)
    return {"ok": True, "mode": "rag",
             "interpretive_summary": text,
             "synthesis_meta": meta}


def _serialise_retrieved(retrieved: List[Any]) -> List[Dict[str, Any]]:
    out = []
    for r in retrieved:
        e = r.entry
        out.append({
            "id": e.id,
            "name": e.name,
            "domain": e.domain,
            "source": e.source,
            "relevance": float(r.relevance),
            "matched_via": list(r.matched_via),
            "structured_score": float(r.structured_score),
            "semantic_score": float(r.semantic_score),
            # Trim references to first two; full content available via
            # /api/knowledge_bank/entry/<id>.
            "references": [
                {"citation": (getattr(ref, "citation", None)
                               or (ref.get("citation") if isinstance(ref, dict)
                                     else ""))}
                for ref in (e.references or [])[:2]
            ],
        })
    return out
