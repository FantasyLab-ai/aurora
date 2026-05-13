"""
Ingest pipeline orchestrator.

Properties required by the brief:
  * Resumable — checkpoints per source under <checkpoint_dir>/<source>.json.
  * Atomic per batch — either an entry (with vector) lands or it doesn't.
  * Observable — progress callback fires after every batch.
  * Interruptible — KeyboardInterrupt cleanly checkpoints and re-raises.

Source modules register themselves under :mod:`.sources`. To add a new
source, drop a module exposing ``run_ingest(bank, checkpoint_dir,
progress_cb)`` and add it to ``DEFAULT_SOURCES``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..storage.sqlite_store import KnowledgeBankStore, get_bank
from . import sources


# Brief 5: full ingest stage list. Sources that lack credentials or
# downloaded source files return a structured "skipped" result rather
# than crashing the pipeline. The maintainer enables each stage by
# providing the prerequisites documented in each source module.
DEFAULT_SOURCES: List[str] = [
    "seed",                  # always available
    # Stage 1
    "fred_metadata",         # needs FRED_API_KEY
    # Stage 2
    "gene_ontology",         # needs go.obo download
    "uniprot",               # needs uniprot_human.dat.gz + biopython
    # Stage 3
    "noaa_documentation",    # public; works if requests is installed
    "usgs_geological",       # ships with curated baseline
    # Stage 4
    "nist_reference",        # public; needs requests
    # Stage 5
    "wikidata",              # needs WIKIDATA_DUMP_PATH + ijson
    # Stage 6
    "wikipedia_science",     # public Wikipedia API
    "textbook_extractor",    # needs textbooks/ + Ollama qwen2.5:32b
]


# Stage groupings, surfaced via the admin UI.
INGEST_STAGES: Dict[str, Dict[str, Any]] = {
    "stage_1_fred": {
        "label": "Stage 1 — FRED metadata",
        "sources": ["fred_metadata"],
        "expected_entries": "~800K",
    },
    "stage_2_bio": {
        "label": "Stage 2 — Gene Ontology + UniProt",
        "sources": ["gene_ontology", "uniprot"],
        "expected_entries": "~70K",
    },
    "stage_3_enviro": {
        "label": "Stage 3 — NOAA + USGS",
        "sources": ["noaa_documentation", "usgs_geological"],
        "expected_entries": "~8K",
    },
    "stage_4_nist": {
        "label": "Stage 4 — NIST reference",
        "sources": ["nist_reference"],
        "expected_entries": "~10K",
    },
    "stage_5_wikidata": {
        "label": "Stage 5 — Wikidata selective",
        "sources": ["wikidata"],
        "expected_entries": "~500K",
    },
    "stage_6_wikipedia_textbook": {
        "label": "Stage 6 — Wikipedia + textbook extraction",
        "sources": ["wikipedia_science", "textbook_extractor"],
        "expected_entries": "~105K",
    },
}


# ---------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------

def _checkpoint_path(checkpoint_dir: Path, source: str) -> Path:
    return Path(checkpoint_dir) / f"{source}.checkpoint.json"


def _read_checkpoint(checkpoint_dir: Path, source: str) -> Dict[str, Any]:
    p = _checkpoint_path(checkpoint_dir, source)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_checkpoint(checkpoint_dir: Path, source: str,
                        data: Dict[str, Any]) -> None:
    p = _checkpoint_path(checkpoint_dir, source)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

ProgressCb = Callable[[Dict[str, Any]], None]


def run_ingest(
    selected_sources: Optional[Sequence[str]] = None,
    *,
    bank: Optional[KnowledgeBankStore] = None,
    checkpoint_dir: Optional[Path] = None,
    progress_cb: Optional[ProgressCb] = None,
) -> Dict[str, Any]:
    """Run the ingest pipeline.

    Parameters
    ----------
    selected_sources
        Names of source modules to run. Defaults to :data:`DEFAULT_SOURCES`.
    bank
        Override the singleton bank (mostly for tests).
    checkpoint_dir
        Where to write per-source checkpoints. Defaults to the bank's
        ``<bank_path>.checkpoints/`` directory.
    progress_cb
        Optional callback invoked with ``{source, stage, n_done, n_total}``
        dicts as the pipeline progresses.
    """
    bank = bank or get_bank()
    if checkpoint_dir is None:
        checkpoint_dir = bank.path.parent / "checkpoints"
    checkpoint_dir = Path(checkpoint_dir)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    sources_to_run = list(selected_sources or DEFAULT_SOURCES)
    started_at = time.time()

    summary: Dict[str, Any] = {
        "started_at": started_at,
        "sources": {},
        "total_inserted": 0,
        "total_errors": 0,
    }

    # Vector-index dim guard (Brief: schema-migration follow-up). When
    # the embedder dim has changed since the index was last persisted
    # (e.g. TF-IDF 384 → neural 768), inserts will crash inside
    # ``add_batch`` with a shape error. Rebuild before any source runs.
    if getattr(bank, "needs_vector_rebuild", False):
        old_dim = bank._vector_index.dim
        new_dim = bank._embedder.dim
        if progress_cb:
            try:
                progress_cb({"source": "vector_index", "stage": "rebuild_start",
                              "old_dim": old_dim, "new_dim": new_dim})
            except Exception:
                pass
        rb = bank.rebuild_vector_index(progress_cb=progress_cb)
        summary["vector_index_rebuild"] = rb
        if progress_cb:
            try:
                progress_cb({"source": "vector_index", "stage": "rebuild_done",
                              "n_re_embedded": rb.get("n_re_embedded")})
            except Exception:
                pass

    for src in sources_to_run:
        if progress_cb:
            try: progress_cb({"source": src, "stage": "start"})
            except Exception: pass
        module = getattr(sources, src, None)
        if module is None or not hasattr(module, "run_ingest"):
            summary["sources"][src] = {"ok": False,
                                          "reason": "module_not_found"}
            continue
        try:
            res = module.run_ingest(bank, checkpoint_dir,
                                       progress_cb=progress_cb)
            summary["sources"][src] = res
            summary["total_inserted"] += int(res.get("n_inserted") or 0)
            summary["total_errors"] += int(res.get("n_errors") or 0)
        except KeyboardInterrupt:
            summary["sources"][src] = {"ok": False,
                                          "reason": "interrupted"}
            # Save bank's vector index before re-raising so we don't
            # lose progress.
            try: bank.save_vector_index()
            except Exception: pass
            raise
        except Exception as e:
            summary["sources"][src] = {"ok": False,
                                          "reason": f"{type(e).__name__}: {e}"}

    bank.save_vector_index()
    summary["completed_at"] = time.time()
    summary["elapsed_seconds"] = summary["completed_at"] - started_at
    return summary


def run_seed_ingest(bank: Optional[KnowledgeBankStore] = None) -> Dict[str, Any]:
    """Convenience wrapper — run only the seed source. Idempotent."""
    return run_ingest(["seed"], bank=bank)


def run_stage(stage_id: str, *,
                bank: Optional[KnowledgeBankStore] = None,
                progress_cb: Optional[ProgressCb] = None) -> Dict[str, Any]:
    """Run all sources for a named stage (Brief 5 staging).

    Looks up ``stage_id`` in :data:`INGEST_STAGES`, runs each source
    in order with the standard checkpointing, and returns the merged
    summary.
    """
    info = INGEST_STAGES.get(stage_id)
    if info is None:
        return {"ok": False, "error": f"unknown_stage: {stage_id}"}
    return run_ingest(info["sources"], bank=bank, progress_cb=progress_cb)
