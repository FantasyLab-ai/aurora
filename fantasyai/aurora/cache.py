"""
Artifact cache — instant-replay for runs that have already been computed.

When a user re-runs a dataset they've already analyzed (same content, same seed,
same flags), Aurora should serve the cached run rather than spawning the
30-second-to-3-minute subprocess all over again.

Design
------

Cache key = SHA-256 of:

  {input_data_hash, seed, flags_string}

* ``input_data_hash`` — SHA-256 of the source CSV's bytes (already computed
  by the provenance ledger as ``inputs_hash``).
* ``seed`` — the runner seed (default 42).
* ``flags_string`` — the canonical sorted string of pipeline flags
  (``--also-math --also-llm --also-motif --also-orchestrator --deep-math --math-v2``).

Cache write
-----------
After the runner completes, we write ``_CACHE_KEY.json`` at the run dir:

    {"cache_key": "sha256:...", "input_hash": "sha256:...",
     "seed": 42, "flags": ["--also-math", ...],
     "created_at": "2026-05-02T18:00:00Z"}

Cache read
----------
Walk recent runs in ``outputs/aurora_dataset_runs/`` (newest first), read each
``_CACHE_KEY.json``, return the first one whose key matches. Capped to a
configurable max age so very-old runs aren't served as fresh.

Why not a separate cache directory?
-----------------------------------
Run dirs already contain everything we need. Adding a side-cache would
double the storage. Side benefit: the user sees their cached run in the
session list (with a "(cached)" marker) — no surprise data hiding in
unexpected locations.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .provenance.ledger import hash_file


CACHE_KEY_FILENAME = "_CACHE_KEY.json"
DEFAULT_MAX_AGE_DAYS = 30


@dataclass(frozen=True)
class CacheKey:
    """Triple uniquely identifying a runnable analysis."""
    input_hash: str
    seed: int
    flags: tuple

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_hash": self.input_hash,
            "seed": self.seed,
            "flags": list(self.flags),
            "cache_key": self.compute_hash(),
        }

    def compute_hash(self) -> str:
        flags_str = ",".join(sorted(self.flags))
        material = f"{self.input_hash}|seed={self.seed}|flags={flags_str}"
        return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def normalize_flags(*,
                    also_math: bool, math_v2: bool, deep_math: bool,
                    also_llm: bool, also_motif: bool, also_orchestrator: bool) -> tuple:
    """Canonicalize the runner flag set into a sorted tuple."""
    flags: List[str] = []
    if also_math:         flags.append("--also-math")
    if math_v2:           flags.append("--math-v2")
    if deep_math:         flags.append("--deep-math")
    if also_llm:          flags.append("--also-llm")
    if also_motif:        flags.append("--also-motif")
    if also_orchestrator: flags.append("--also-orchestrator")
    return tuple(sorted(flags))


def compute_cache_key(dataset_path: Path, seed: int, flags: tuple) -> CacheKey:
    """Build a CacheKey for the given (dataset, seed, flags) triple."""
    p = Path(dataset_path)
    if not p.exists() or not p.is_file():
        # Hash the path string as a fallback — better than crashing.
        input_hash = "sha256:" + hashlib.sha256(str(p).encode("utf-8")).hexdigest()
    else:
        try:
            input_hash = hash_file(p)
        except Exception:
            input_hash = "sha256:" + hashlib.sha256(str(p).encode("utf-8")).hexdigest()
    return CacheKey(input_hash=input_hash, seed=int(seed), flags=tuple(flags))


def write_cache_key(run_dir: Path, key: CacheKey) -> Path:
    """Stamp the run dir with its cache key for later lookup."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / CACHE_KEY_FILENAME
    payload = key.to_dict()
    payload["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def find_cached_run(outputs_root: Path, key: CacheKey,
                    max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> Optional[Path]:
    """Find an existing run dir matching ``key``, newest first.

    Returns the matching ``Path`` or None. Skips run dirs older than
    ``max_age_days``.
    """
    outputs_root = Path(outputs_root)
    if not outputs_root.exists():
        return None
    target_hash = key.compute_hash()
    cutoff = time.time() - max_age_days * 86400

    candidates: List[Path] = []
    for p in outputs_root.iterdir():
        if not p.is_dir():
            continue
        if p.stat().st_mtime < cutoff:
            continue
        candidates.append(p)
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    for run_dir in candidates:
        ck_path = run_dir / CACHE_KEY_FILENAME
        if not ck_path.exists():
            continue
        try:
            ck = json.loads(ck_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if ck.get("cache_key") == target_hash:
            return run_dir
    return None


def list_cache_entries(outputs_root: Path,
                       max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> List[Dict[str, Any]]:
    """List every (run_dir, cache_key) pair under outputs_root, newest first."""
    outputs_root = Path(outputs_root)
    if not outputs_root.exists():
        return []
    cutoff = time.time() - max_age_days * 86400
    entries: List[Dict[str, Any]] = []
    for p in sorted(outputs_root.iterdir(), key=lambda x: x.stat().st_mtime if x.exists() else 0,
                    reverse=True):
        if not p.is_dir() or p.stat().st_mtime < cutoff:
            continue
        ck_path = p / CACHE_KEY_FILENAME
        if not ck_path.exists():
            continue
        try:
            ck = json.loads(ck_path.read_text(encoding="utf-8"))
            ck["run_dir"] = str(p)
            entries.append(ck)
        except Exception:
            continue
    return entries
