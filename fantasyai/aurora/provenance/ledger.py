"""
Provenance ledger — entry schema, writer, and reader.

The ledger lives at ``<run_dir>/_provenance_ledger.jsonl`` (JSON Lines, one
entry per line). Lazy-loaded by ``claim_id`` so we never pull the whole
thing into memory.

Entry schema
------------

::

    {
      "claim_id":       "anom-0001",                     # stable, used by frontend
      "claim_type":     "anomaly|regime|forecast|causal|physics|motif|finding",
      "claim_summary":  "row 47 flagged with score=5.67",  # 1-line headline
      "method":         "robust_zscore_isolation_forest_v1",
      "method_params":  {"top_k": 10, ...},
      "inputs_hash":    "sha256:abc123…",               # of the data subset used
      "input_subset":   {"rows": [47], "columns": ["precip_mm","temperature_c"]},
      "code_ref":       "fantasyai/aurora/math/deep_math.py:512",
      "code_version":   "git_sha:abc1234" | "unknown",
      "raw_result":     {...},                          # the actual output values
      "artifact_ref":   "deep_math.json:extensions.anomaly_attribution.points[0]",
      "timestamp":      "2026-05-02T16:24:17Z",
    }
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


LEDGER_FILENAME = "_provenance_ledger.jsonl"


@dataclass
class ProvenanceEntry:
    claim_id: str
    claim_type: str
    claim_summary: str
    method: str
    artifact_ref: str
    code_ref: str = ""
    method_params: Dict[str, Any] = field(default_factory=dict)
    inputs_hash: str = ""
    input_subset: Dict[str, Any] = field(default_factory=dict)
    code_version: str = "unknown"
    raw_result: Any = None
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if not d["timestamp"]:
            d["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return d


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------

def hash_payload(payload: Any) -> str:
    """Deterministic SHA-256 of a JSON-serializable payload.

    Sorted keys + UTF-8 + no whitespace = same hash on every machine.
    """
    try:
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                separators=(",", ":"), default=str)
    except Exception:
        serialized = repr(payload)
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def hash_file(path: Path) -> str:
    """SHA-256 of a file's contents (used for input-data hashing)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def detect_code_version(repo_root: Optional[Path] = None) -> str:
    """Return the current git SHA if available, else 'unknown'."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root) if repo_root else None,
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            return f"git_sha:{result.stdout.strip()}"
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def write_ledger(run_dir: Path, entries: List[ProvenanceEntry]) -> Path:
    """Write a fresh ledger to ``<run_dir>/_provenance_ledger.jsonl``.

    Replaces any existing ledger. Atomic via tmp + rename.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / LEDGER_FILENAME
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e.to_dict(), ensure_ascii=False, default=str) + "\n")
    os.replace(tmp, target)
    return target


def append_entry(run_dir: Path, entry: ProvenanceEntry) -> None:
    """Append a single entry to the run's ledger (creates if missing)."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / LEDGER_FILENAME
    with open(target, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry.to_dict(), ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def iter_ledger(run_dir: Path) -> Iterator[Dict[str, Any]]:
    """Iterate ledger entries for a run, one at a time. Empty if missing."""
    p = Path(run_dir) / LEDGER_FILENAME
    if not p.exists():
        return
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def read_ledger(run_dir: Path) -> List[Dict[str, Any]]:
    """Read all ledger entries (eager). Use sparingly — prefer iter_ledger."""
    return list(iter_ledger(run_dir))


def load_entry(run_dir: Path, claim_id: str) -> Optional[Dict[str, Any]]:
    """Look up a single entry by claim_id. Linear scan; ledgers stay small."""
    for e in iter_ledger(run_dir):
        if e.get("claim_id") == claim_id:
            return e
    return None
