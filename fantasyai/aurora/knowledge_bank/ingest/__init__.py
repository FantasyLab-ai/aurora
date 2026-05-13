"""
Ingest pipeline + source modules.

Each source module under ``sources/`` exposes a ``run_ingest(bank,
checkpoint_dir, progress_cb=None)`` callable. The pipeline orchestrator
in :mod:`pipeline` walks all enabled sources, invokes them with
checkpointing, and tracks progress.

For the ship-now build, only the ``seed`` source is fully implemented
— it loads a curated, hand-authored set of ~50 entries that gives RAG
synthesis something to ground itself in on first run. The other source
modules are stubs that document where the maintainer would plug in
real downloads (Gene Ontology OBO, FRED API, Wikipedia dumps, etc.).

Real ingest is a maintainer operation — pulls hundreds of GB and runs
for hours/days on a workstation. Aurora ships with the seed bank
plus the ingest pipeline so the maintainer can grow the bank over time.
"""
from .pipeline import run_ingest, run_seed_ingest, run_stage, INGEST_STAGES

__all__ = ["run_ingest", "run_seed_ingest"]
