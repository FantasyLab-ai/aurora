"""
fantasyai.aurora.knowledge_bank — Aurora's grounded knowledge layer.

Architecture:
  * SQLite-backed structured store of vetted concept entries (one row per
    entry, full provenance, version-tracked).
  * Pure-numpy vector index sidecar (fallback when sentence-transformers
    isn't available — uses TF-IDF-ish hashed embeddings).
  * Retrieval engine: structured trigger matching + semantic search +
    hybrid ranker.
  * Ingest pipeline: resumable, atomic, observable. Source-specific
    modules per ontology / data source.
  * RAG synthesis bridge: query → retrieved entries → grounded LLM prompt.

The knowledge bank is the data; retrieval is the read path; ingestion is
the write path. Synthesis builds on retrieval.

Public API (the most-used entrypoints):

  >>> from fantasyai.aurora.knowledge_bank import retrieve, get_bank
  >>> entries = retrieve("damped harmonic oscillator", domains=["bio"])
  >>> bank = get_bank(); bank.summary()
"""
from .schema import KnowledgeEntry, EntryReference, PatternSignature, validate_entry
from .storage.sqlite_store import KnowledgeBankStore, get_bank
from .retrieval.engine import retrieve, RetrievalMode, RetrievedEntry

__all__ = [
    "KnowledgeEntry",
    "EntryReference",
    "PatternSignature",
    "validate_entry",
    "KnowledgeBankStore",
    "get_bank",
    "retrieve",
    "RetrievalMode",
    "RetrievedEntry",
]
