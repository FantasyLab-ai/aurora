"""
Walk a folder of PDFs (or text files) and ingest them all.

Public API: ``ingest_folder(path, ..., workspace_id=...)`` returns an
``IngestReport`` summarising what was ingested + skipped + failed.

We accept these file types::

    .pdf  → pypdf extraction (or .txt sibling fallback)
    .txt  → straight read
    .md   → straight read

Anything else is skipped with a reason.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .pdf_ingester import (
    extract_text_from_pdf,
    PDFIngestError,
    PDFTextChunk,
)
from .storage import write_kb_entries


@dataclass
class IngestReport:
    ok:           bool
    folder:       str
    workspace:    str
    n_files_seen: int = 0
    n_ingested:   int = 0
    n_skipped:    int = 0
    n_failed:     int = 0
    n_chunks_written: int = 0
    out_path:     Optional[str] = None
    files:        List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _ingest_text_file(p: Path) -> List[PDFTextChunk]:
    """Treat a .txt or .md as one chunk per ~4000-char section."""
    from .pdf_ingester import _chunk_long_text, _file_sha256
    text = p.read_text(encoding="utf-8", errors="replace")
    return _chunk_long_text(
        text,
        source_path=str(p),
        source_hash=_file_sha256(p),
        page_number=None,
    )


def ingest_folder(
    folder: Any,
    *,
    workspace_id: Optional[str] = None,
    glob_pattern: str = "**/*",
    max_files: Optional[int] = 200,
    append: bool = True,
) -> IngestReport:
    """Ingest every supported file under ``folder``.

    Args:
        folder: directory to walk.
        workspace_id: which workspace's KB to write to.
        glob_pattern: defaults to "**/*" (recursive).
        max_files: defensive cap; refuse to ingest huge trees in one go.
        append: when True (default), merge with existing entries; when
            False, replace the workspace's custom KB entirely.

    Returns:
        IngestReport.
    """
    f = Path(folder)
    if not f.exists() or not f.is_dir():
        return IngestReport(
            ok=False, folder=str(f),
            workspace=str(workspace_id or "default"),
        )
    report = IngestReport(
        ok=True, folder=str(f),
        workspace=str(workspace_id or "default"),
    )
    all_chunks: List[PDFTextChunk] = []
    for p in sorted(f.glob(glob_pattern)):
        if not p.is_file():
            continue
        report.n_files_seen += 1
        suffix = p.suffix.lower()
        if max_files is not None and report.n_files_seen > max_files:
            report.files.append({"path": str(p), "status": "skipped",
                                  "reason": f"max_files cap ({max_files}) reached"})
            report.n_skipped += 1
            continue
        if suffix not in (".pdf", ".txt", ".md"):
            report.files.append({"path": str(p), "status": "skipped",
                                  "reason": f"unsupported extension {suffix}"})
            report.n_skipped += 1
            continue
        try:
            if suffix == ".pdf":
                chunks = extract_text_from_pdf(p)
            else:
                chunks = _ingest_text_file(p)
            if not chunks:
                report.files.append({"path": str(p), "status": "skipped",
                                      "reason": "no extractable text"})
                report.n_skipped += 1
                continue
            all_chunks.extend(chunks)
            report.n_ingested += 1
            report.n_chunks_written += len(chunks)
            report.files.append({"path": str(p), "status": "ingested",
                                  "n_chunks": len(chunks)})
        except PDFIngestError as e:
            report.files.append({"path": str(p), "status": "failed",
                                  "reason": str(e)})
            report.n_failed += 1
        except Exception as e:
            report.files.append({
                "path": str(p), "status": "failed",
                "reason": f"{type(e).__name__}: {e}",
            })
            report.n_failed += 1
    if all_chunks:
        summary = write_kb_entries(
            all_chunks,
            workspace_id=workspace_id,
            append=append,
        )
        report.out_path = summary["out_path"]
    return report
