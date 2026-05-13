"""
Provenance ledger — Aurora's glass-box verification layer.

Every claim Aurora emits (anomaly, regime, forecast, causal effect, physics
fit, motif) gets a corresponding ledger entry so the user can inspect *exactly*
how it was produced: which rows, which method, which code path, which artifact
field, which inputs hash. This is the structural difference between Aurora and
any cloud LLM tool: cloud LLMs paraphrase; Aurora cites the operation.

Public API
----------

    from fantasyai.aurora.provenance import (
        ProvenanceEntry, write_ledger, read_ledger,
        load_entry, build_provenance_for_run,
    )

Storage layout
--------------

Each run directory gets a single ``_provenance_ledger.jsonl`` file:

    <run_dir>/_provenance_ledger.jsonl

One JSON object per line, one line per claim. Lazy-loaded by ``claim_id``;
the whole ledger is never read into memory at once.
"""
from .ledger import (
    ProvenanceEntry,
    LEDGER_FILENAME,
    write_ledger,
    read_ledger,
    load_entry,
    iter_ledger,
)
from .builder import build_provenance_for_run

__all__ = [
    "ProvenanceEntry",
    "LEDGER_FILENAME",
    "write_ledger",
    "read_ledger",
    "load_entry",
    "iter_ledger",
    "build_provenance_for_run",
]
