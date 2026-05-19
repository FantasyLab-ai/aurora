"""
Per-workspace KB storage for ingested entries.

Custom-ingested entries live at::

    <workspace_dir>/kb/custom_entries.jsonl

Each line is one entry::

    {
        "seed_id":     "custom:<source_hash_short>:<section_idx>",
        "title":       "...",
        "summary":     "...",          # first paragraph or section heading
        "content":     "...",          # the chunk's full text
        "source":      "...",          # source file path
        "source_hash": "...",          # SHA-256
        "page":        12,
        "ingested_at": 1716130800.0,
        "domain":      "custom",
        "license":     "user_provided",
    }

The synthesis retriever discovers the file via the workspace layout
and merges its entries into the candidate pool alongside the
canonical KB. We don't pre-compute embeddings here — the retriever
will (lazily, on first query) using whatever embedding model the
workspace has configured.

Idempotency: re-ingesting the same source file produces the same
seed_ids, so a second `write_kb_entries` is a no-op rewrite (the
file is truncated + rewritten in workspace order).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def workspace_kb_dir(workspace_id: Optional[str] = None) -> Path:
    """Resolve <workspace_dir>/kb/ , creating it as needed."""
    try:
        from fantasyai.aurora.auth import workspace_dir
        base = workspace_dir(workspace_id)
    except Exception:
        env = os.environ.get("AURORA_DATA_ROOT", "").strip()
        if env:
            base = Path(env).expanduser() / "workspaces" / (workspace_id or "default")
        else:
            base = Path.home() / ".aurora"
    d = base / "kb"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _short_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def _entry_from_chunk(chunk: Any) -> Dict[str, Any]:
    """Map a PDFTextChunk → KB entry dict. Accepts either a
    PDFTextChunk dataclass or a pre-built dict."""
    def _pluck(key, default=None):
        if hasattr(chunk, key):
            return getattr(chunk, key)
        if isinstance(chunk, dict):
            return chunk.get(key, default)
        return default

    # Title: first line, or first 80 chars.
    text = _pluck("text", "") or ""
    first_line = (text.splitlines()[0] if text else "").strip()
    title = (first_line[:100] if first_line else
              (text[:80] + "…" if text else "(empty section)"))
    summary = text[:400] + ("…" if len(text) > 400 else "")
    src_path = _pluck("source_path", "") or ""
    src_hash = _pluck("source_hash", "") or ""
    page = _pluck("page_number")
    section_idx = _pluck("section_index", 0) or 0
    seed_id = (f"custom:{_short_hash(src_path)}:{src_hash[:8]}:"
                f"{page or 0}:{section_idx}")
    return {
        "seed_id":     seed_id,
        "title":       title,
        "summary":     summary,
        "content":     text,
        "source":      src_path,
        "source_hash": src_hash,
        "page":        page,
        "section":     section_idx,
        "ingested_at": time.time(),
        "domain":      "custom",
        "license":     "user_provided",
    }


def write_kb_entries(
    chunks: Iterable[Any],
    *,
    workspace_id: Optional[str] = None,
    append: bool = False,
) -> Dict[str, Any]:
    """Write a sequence of chunks (or pre-built dicts) to the custom
    KB jsonl. Returns a summary dict.

    Args:
        chunks: iterable of PDFTextChunk objects or entry dicts.
        workspace_id: target workspace; defaults to single-tenant.
        append: when False (default), rewrites the file. When True,
            appends to it (useful for incremental ingest runs).
    """
    out_dir = workspace_kb_dir(workspace_id)
    out_path = out_dir / "custom_entries.jsonl"
    entries: List[Dict[str, Any]] = []
    seen_ids: set = set()
    if append and out_path.exists():
        try:
            for line in out_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    e = json.loads(line)
                    entries.append(e)
                    seen_ids.add(e["seed_id"])
        except Exception:
            entries, seen_ids = [], set()
    new_count = 0
    for c in chunks:
        # Accept either PDFTextChunk or a dict.
        if hasattr(c, "to_dict") or hasattr(c, "source_path"):
            entry = _entry_from_chunk(c)
        elif isinstance(c, dict):
            entry = c if "seed_id" in c else _entry_from_chunk(c)
        else:
            continue
        if entry["seed_id"] in seen_ids:
            continue
        entries.append(entry)
        seen_ids.add(entry["seed_id"])
        new_count += 1
    with open(out_path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
    return {
        "ok":          True,
        "out_path":    str(out_path),
        "n_total":     len(entries),
        "n_new":       new_count,
        "workspace":   workspace_id or "default",
    }


def list_workspace_kb_entries(
    workspace_id: Optional[str] = None,
    *,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Read the custom KB entries for a workspace. Newest-first when
    multiple ingest runs accumulated."""
    out_path = workspace_kb_dir(workspace_id) / "custom_entries.jsonl"
    if not out_path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    rows.sort(key=lambda e: float(e.get("ingested_at") or 0), reverse=True)
    if limit is not None and limit > 0:
        return rows[:int(limit)]
    return rows
