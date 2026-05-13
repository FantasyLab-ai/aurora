"""
Per-source ingest modules. Each exposes ``run_ingest(bank,
checkpoint_dir, progress_cb)``. The pipeline orchestrator walks
selected modules with checkpointing; sources that aren't ready
(missing API key, missing source files, missing optional libs)
return a structured ``{ok: false, reason, fix}`` rather than crashing.
"""
from . import seed                  # always available
from . import fred_metadata          # Stage 1
from . import gene_ontology          # Stage 2A
from . import uniprot                # Stage 2B
from . import noaa_documentation     # Stage 3A
from . import usgs_geological        # Stage 3B
from . import nist_reference         # Stage 4
from . import wikidata               # Stage 5
from . import wikipedia_science      # Stage 6A
from . import textbook_extractor     # Stage 6B

__all__ = [
    "seed",
    "fred_metadata", "gene_ontology", "uniprot",
    "noaa_documentation", "usgs_geological",
    "nist_reference", "wikidata", "wikipedia_science",
    "textbook_extractor",
]
