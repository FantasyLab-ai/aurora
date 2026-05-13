"""
Stage 4 — NIST physical constants + chemistry reference (~10-15K entries).

Sources:
  * Physical constants: https://physics.nist.gov/cuu/Constants/Table/allascii.txt
  * Chemistry WebBook (selective): https://webbook.nist.gov
License: Public Domain (US Government)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ...schema import (
    KnowledgeEntry, EntryReference, QuantitativeRange, attach_license,
)
from ...storage.sqlite_store import KnowledgeBankStore


SOURCE = "nist"
LICENSE = "Public Domain (US Government)"
CONSTANTS_URL = "https://physics.nist.gov/cuu/Constants/Table/allascii.txt"


def _stream_constants() -> List[Dict[str, str]]:
    """Pull the NIST CODATA constants table (small, ~350 entries)."""
    try:
        import requests
        r = requests.get(CONSTANTS_URL, timeout=30)
        r.raise_for_status()
        raw = r.text
    except Exception:
        return []
    out = []
    in_data = False
    for line in raw.splitlines():
        if line.startswith("------"):
            in_data = True
            continue
        if not in_data or len(line) < 60:
            continue
        # Format: name (60) | value (25) | uncertainty (25) | unit
        # Exact column widths vary; split on multiple spaces.
        parts = [p.strip() for p in line.split("  ") if p.strip()]
        if len(parts) < 2:
            continue
        out.append({
            "name": parts[0],
            "value": parts[1] if len(parts) > 1 else "",
            "uncertainty": parts[2] if len(parts) > 2 else "",
            "unit": parts[3] if len(parts) > 3 else "",
        })
    return out


def run_ingest(bank: KnowledgeBankStore, checkpoint_dir: Path,
                 *, progress_cb: Optional[Callable[[Dict], None]] = None,
                 ) -> Dict[str, Any]:
    constants = _stream_constants()
    if not constants:
        return {"ok": False, "source": SOURCE,
                 "reason": "nist_constants_unreachable",
                 "fix": "pip install requests; check network",
                 "n_inserted": 0}
    source_version = time.strftime("%Y-%m-%d")
    entries: List[KnowledgeEntry] = []
    for c in constants:
        sid = c["name"].lower().replace(" ", "_").replace(",", "").replace(".", "")[:80]
        if not sid:
            continue
        defn = f"{c['name']}"
        if c.get("value"):
            defn += f" — value {c['value']}"
            if c.get("unit"):
                defn += f" {c['unit']}"
            if c.get("uncertainty"):
                defn += f" (uncertainty {c['uncertainty']})"
            defn += "."
        quant = []
        if c.get("value") and c.get("unit"):
            quant.append(QuantitativeRange(
                property=c["name"], context=c["value"], unit=c["unit"],
            ))
        entry = KnowledgeEntry(
            id=f"nist_constant_{sid}",
            source=SOURCE,
            source_version=source_version,
            domain=["physics"],
            concept_type="physical_constant",
            review_status="auto_approved",
            ingest_method="api_etl",
            name=c["name"],
            definition=defn,
            quantitative_ranges=quant,
            references=[EntryReference(
                citation="NIST Reference on Constants, Units, and Uncertainty",
                url="https://physics.nist.gov/cuu/Constants/",
            )],
        )
        entries.append(attach_license(entry, LICENSE))
    res = bank.insert_many(entries)
    if progress_cb:
        progress_cb({"source": SOURCE, "n_done": res["n_inserted"]})
    return {"ok": True, "source": SOURCE, "source_version": source_version,
             "n_inserted": res["n_inserted"], "license": LICENSE}
