"""
Finding dedupe for streaming runs (Stream 1.4 Phase 2).

Phase 1 fired one ``new_finding`` event PER RUN with the full severity
rollup — including findings that hadn't actually changed since the
previous run. That's noisy: a subscriber listening for "tell me when
something's new" gets pinged every poll cycle even when nothing changed.

Phase 2 hashes each finding into a stable identity and only fires
``new_finding`` for the *delta* — the findings that genuinely emerged
since the previous run. The hash also feeds Decision Contracts so
contract triggers don't re-fire on the same finding across polls.

The identity hash is intentionally coarse (method + title + severity +
top-level evidence numeric keys) so a regression that re-emits the
same anomaly with slightly different formatting doesn't slip through.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple


def finding_identity(finding: Dict[str, Any]) -> str:
    """Compute a stable identity hash for a finding.

    Includes:
      * method
      * severity
      * title (full)
      * referent.primary_label if present (so anomalies at different
        rows hash differently even when the title text is identical)
      * key sortable evidence keys: status, n_obs, change_points,
        target_col, top_cross_coupling, regime_class

    Returns a 16-char hex digest — collision resistance is ample for
    the bounded dedupe window we maintain (<10k findings).
    """
    if not isinstance(finding, dict):
        return ""
    payload: Dict[str, Any] = {
        "method":   str(finding.get("method") or "").lower(),
        "title":    str(finding.get("title") or ""),
        "severity": str(finding.get("severity") or "").lower(),
    }
    ref = finding.get("referent") or {}
    if isinstance(ref, dict) and ref.get("primary_label"):
        payload["primary_label"] = str(ref["primary_label"])
    ev = finding.get("evidence") or {}
    if isinstance(ev, dict):
        # Pick a small set of stable identity-bearing evidence keys.
        for k in ("status", "n_obs", "target_col", "regime_class",
                   "change_points"):
            if k in ev:
                v = ev[k]
                # change_points: hash just the indices, not posteriors.
                if k == "change_points" and isinstance(v, list):
                    v = [
                        (cp.get("row_idx") if isinstance(cp, dict) else cp)
                        for cp in v
                    ]
                payload[f"ev_{k}"] = v
        tcc = ev.get("top_cross_coupling")
        if isinstance(tcc, dict):
            payload["ev_top_cross_coupling"] = (
                tcc.get("from_col"), tcc.get("to_col")
            )
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class FindingDedupeStore:
    """Bounded LRU of seen finding identities.

    Thread-safe (the streaming runner publishes from a watcher thread).
    """

    def __init__(self, max_size: int = 5_000):
        self.max_size = int(max_size)
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._lock = Lock()

    def is_new(self, identity: str) -> bool:
        if not identity:
            return False
        with self._lock:
            if identity in self._seen:
                # Refresh LRU position; same identity → not new.
                self._seen.move_to_end(identity)
                return False
            return True

    def mark_seen(self, identity: str, *, ts: float = 0.0) -> None:
        if not identity:
            return
        with self._lock:
            self._seen[identity] = float(ts)
            self._seen.move_to_end(identity)
            # Trim oldest if we're over capacity.
            while len(self._seen) > self.max_size:
                self._seen.popitem(last=False)

    def filter_new(
        self,
        findings: List[Dict[str, Any]],
        *,
        ts: float = 0.0,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Return ``(new_findings, all_identities)``.

        ``new_findings`` is the subset whose identity hasn't been seen
        before; ``all_identities`` is every finding's hash, in order
        (useful for downstream attestation / replay).

        Side effect: marks every identity as seen.
        """
        new: List[Dict[str, Any]] = []
        ids: List[str] = []
        for f in findings:
            ident = finding_identity(f)
            ids.append(ident)
            if self.is_new(ident):
                new.append(f)
            self.mark_seen(ident, ts=ts)
        return new, ids

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()
