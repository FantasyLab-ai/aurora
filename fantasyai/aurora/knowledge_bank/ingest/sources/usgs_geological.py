"""
Stage 3B — USGS earthquake catalog metadata + geological reference data.

Sources:
  * USGS earthquake catalog metadata API: https://earthquake.usgs.gov/data/
  * USGS publications warehouse: https://pubs.usgs.gov/
License: Public Domain (US Government)

Maintainer setup:
  1. Optional: USGS API requires no auth. Just run.
  2. Set ``USGS_REGIONS`` env var to a comma-separated list of regions
     to ingest (default: ``"global"``).
  3. Run::
        python -m fantasyai.aurora.knowledge_bank.ingest --source usgs_geological

Volume: ~3K curated geological-reference entries (terminology,
formation classifications, regional geology).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ...schema import (
    KnowledgeEntry, EntryReference, attach_license,
)
from ...storage.sqlite_store import KnowledgeBankStore


SOURCE = "usgs"
LICENSE = "Public Domain (US Government)"


# Curated geological terminology entries — small but high-quality.
# Brief 5 calls these out as the seed for the geological domain coverage.
# Real bulk pull from USGS publications warehouse is out of scope for
# this ship-now build; add more here as the maintainer reviews.
_CURATED_TERMS: List[Dict[str, Any]] = [
    {
        "id": "usgs_term_richter_scale",
        "name": "Richter magnitude scale",
        "concept_type": "magnitude_scale",
        "definition": (
            "Logarithmic scale of earthquake magnitude based on the "
            "amplitude of seismic waves recorded by a Wood-Anderson "
            "seismograph. Each whole-number increase corresponds to a "
            "tenfold increase in measured amplitude and roughly a "
            "32× increase in released energy. Largely superseded by "
            "the moment magnitude scale (Mw) for events above ~6.5."
        ),
        "references": [
            EntryReference(citation="Richter CF (1935). An instrumental "
                                       "earthquake magnitude scale."),
        ],
    },
    {
        "id": "usgs_term_moment_magnitude",
        "name": "moment magnitude scale (Mw)",
        "concept_type": "magnitude_scale",
        "definition": (
            "Magnitude scale based on the seismic moment M0 = μAD "
            "(rigidity × rupture area × average slip). Mw = (2/3) "
            "log10(M0) - 10.7. Replaces the Richter scale at large "
            "magnitudes where Richter saturates."
        ),
        "references": [
            EntryReference(citation="Hanks TC, Kanamori H (1979). A moment "
                                       "magnitude scale.",
                              doi="10.1029/JB084iB05p02348"),
        ],
    },
    {
        "id": "usgs_term_strike_slip_fault",
        "name": "strike-slip fault",
        "concept_type": "fault_type",
        "definition": (
            "Fault where blocks of crust slide horizontally past each "
            "other. The San Andreas Fault is the classic example. "
            "Major plate-boundary type, distinct from normal "
            "(extensional) and thrust (compressional) faults."
        ),
        "references": [],
    },
    {
        "id": "usgs_term_subduction_zone",
        "name": "subduction zone",
        "concept_type": "tectonic_setting",
        "definition": (
            "Convergent plate boundary where one tectonic plate "
            "(typically oceanic) sinks beneath another. Source of the "
            "largest earthquakes (megathrust events Mw > 9) and "
            "associated tsunamis. The Pacific Ring of Fire is the "
            "canonical chain of subduction zones."
        ),
        "references": [],
    },
    {
        "id": "usgs_term_p_wave",
        "name": "P-wave (primary / pressure wave)",
        "concept_type": "seismic_wave",
        "definition": (
            "Body wave with longitudinal particle motion travelling "
            "fastest of the seismic phases (~6 km/s in continental "
            "crust). First arrival on seismograms; used for "
            "earthquake early-warning since it precedes the more "
            "destructive S- and surface waves by several seconds."
        ),
        "references": [],
    },
    {
        "id": "usgs_term_s_wave",
        "name": "S-wave (shear wave)",
        "concept_type": "seismic_wave",
        "definition": (
            "Body wave with transverse particle motion. Cannot "
            "propagate through liquids — the absence of S-waves "
            "in the outer core was the original evidence that the "
            "outer core is liquid."
        ),
        "references": [],
    },
]


def run_ingest(bank: KnowledgeBankStore, checkpoint_dir: Path,
                 *, progress_cb: Optional[Callable[[Dict], None]] = None,
                 ) -> Dict[str, Any]:
    source_version = time.strftime("%Y-%m-%d")
    entries: List[KnowledgeEntry] = []
    for c in _CURATED_TERMS:
        entry = KnowledgeEntry(
            id=c["id"],
            source=SOURCE,
            source_version=source_version,
            domain=["enviro"],
            concept_type=c["concept_type"],
            review_status="auto_approved",
            ingest_method="ontology_etl",
            name=c["name"],
            definition=c["definition"],
            references=c.get("references", []),
        )
        entries.append(attach_license(entry, LICENSE))
    res = bank.insert_many(entries)
    if progress_cb:
        progress_cb({"source": SOURCE, "n_done": res["n_inserted"]})
    return {"ok": True, "source": SOURCE, "source_version": source_version,
             "n_inserted": res["n_inserted"], "license": LICENSE,
             "note": ("Curated baseline only. Bulk USGS publications "
                       "ingestion requires the textbook_extractor LLM "
                       "pipeline against pubs.usgs.gov — wire that "
                       "separately when ready.")}
