"""
Custom knowledge-bank ingestion (v2.0 Stream C).

The Aurora v1 knowledge bank ships as a curated local catalogue +
optional downloadable packs (Climate / Finance / Biomed / Industrial).
v2.0 adds a third source: **your own PDFs and papers**.

Drop a folder of PDFs at `~/Research/aurora_kb/` and Aurora extracts
text, segments it into entries, embeds them into the same KB store
your synthesis layer queries. Per-workspace + opt-in: nothing is
ingested without an explicit call.

What this module ships:

  * **`pdf_ingester.py`** — extract text from a PDF using `pypdf` when
    installed; falls back to a "text-only file" recipe when not.
    Robust to broken/encrypted PDFs.

  * **`folder_ingester.py`** — walk a directory, ingest every
    matching file, produce a structured manifest of what was indexed.

  * **`storage.py`** — write the resulting KB entries to a per-
    workspace shard the synthesis retriever picks up alongside the
    canonical KB. Entries are tagged with their source path + content
    hash so re-ingestion is idempotent.

The synthesis engine doesn't change — the custom shard plugs into
the existing retrieval surface via the workspace dir.
"""
from __future__ import annotations

from .pdf_ingester import (
    extract_text_from_pdf,
    PDFIngestError,
    PDFTextChunk,
)
from .folder_ingester import (
    ingest_folder,
    IngestReport,
)
from .storage import (
    write_kb_entries,
    list_workspace_kb_entries,
    workspace_kb_dir,
)

__all__ = [
    "extract_text_from_pdf",
    "PDFIngestError",
    "PDFTextChunk",
    "ingest_folder",
    "IngestReport",
    "write_kb_entries",
    "list_workspace_kb_entries",
    "workspace_kb_dir",
]
