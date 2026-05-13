"""
Stage 3A — NOAA NDBC station metadata + atmospheric concept docs.

Source: https://www.ndbc.noaa.gov/data/stations/ + https://repository.library.noaa.gov
License: Public Domain (US Government)

Maintainer setup:
  1. Download NDBC station list (auto from ``station_table.txt``).
  2. Run::
        python -m fantasyai.aurora.knowledge_bank.ingest --source noaa_documentation

This module pulls station metadata via the public NDBC station-table
endpoint (no API key needed). LLM-assisted extraction of atmospheric
docs is handled in ``textbook_extractor.py``.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from ...schema import (
    KnowledgeEntry, EntryReference, QuantitativeRange, attach_license,
)
from ...storage.sqlite_store import KnowledgeBankStore


SOURCE = "noaa_ndbc"
LICENSE = "Public Domain (US Government)"
STATION_TABLE_URL = "https://www.ndbc.noaa.gov/data/stations/station_table.txt"


def _stream_stations() -> Iterator[Dict[str, str]]:
    """Pull the NDBC station table and yield one dict per row."""
    try:
        import requests
    except Exception:
        return
    try:
        r = requests.get(STATION_TABLE_URL, timeout=60)
        r.raise_for_status()
        text = r.text
    except Exception:
        return
    # Format: pipe-delimited columns.
    # STATION_ID|OWNER|TTYPE|HULL|NAME|PAYLOAD|LOCATION|TIMEZONE|FORECAST|NOTE
    for raw in text.splitlines():
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split("|")
        if len(parts) < 7:
            continue
        yield {
            "station_id": parts[0].strip(),
            "owner": parts[1].strip(),
            "type": parts[2].strip(),
            "hull": parts[3].strip(),
            "name": parts[4].strip(),
            "payload": parts[5].strip(),
            "location": parts[6].strip(),
            "timezone": parts[7].strip() if len(parts) > 7 else "",
            "forecast": parts[8].strip() if len(parts) > 8 else "",
            "note": parts[9].strip() if len(parts) > 9 else "",
        }


def _station_to_entry(s: Dict[str, str], source_version: str) -> Optional[KnowledgeEntry]:
    sid = s.get("station_id")
    if not sid:
        return None
    name = s.get("name") or sid
    parts: List[str] = []
    parts.append(f"NDBC station {sid}.")
    if s.get("type"): parts.append(f"Type: {s['type']}.")
    if s.get("location"): parts.append(f"Location: {s['location']}.")
    if s.get("payload"): parts.append(f"Payload: {s['payload']}.")
    if s.get("owner"): parts.append(f"Owner: {s['owner']}.")
    if s.get("note"): parts.append(s["note"])
    definition = " ".join(parts)

    quant: List[QuantitativeRange] = []
    if s.get("location"):
        quant.append(QuantitativeRange(
            property="location", context=s["location"]))

    refs = [EntryReference(
        citation=f"NOAA NDBC station {sid}",
        url=f"https://www.ndbc.noaa.gov/station_page.php?station={sid}",
    )]

    mechanism_parts = []
    if s.get("payload"):
        mechanism_parts.append(
            f"Sensor payload type {s['payload']}; refer to NDBC payload "
            "documentation for measurement specs."
        )
    mechanism = " ".join(mechanism_parts)

    entry = KnowledgeEntry(
        id=f"noaa_station_{sid}",
        source=SOURCE,
        source_version=source_version,
        domain=["enviro"],
        concept_type="observation_station",
        review_status="auto_approved",
        ingest_method="api_etl",
        name=f"{name} (NDBC {sid})",
        aliases=[sid, s.get("hull") or ""],
        definition=definition,
        mechanism=mechanism,
        quantitative_ranges=quant,
        references=refs,
    )
    return attach_license(entry, LICENSE)


def run_ingest(bank: KnowledgeBankStore, checkpoint_dir: Path,
                 *, progress_cb: Optional[Callable[[Dict], None]] = None,
                 ) -> Dict[str, Any]:
    try:
        import requests  # noqa
    except Exception:
        return {"ok": False, "source": SOURCE,
                 "reason": "requests_not_installed",
                 "fix": "pip install requests", "n_inserted": 0}
    source_version = time.strftime("%Y-%m-%d")
    cp_path = checkpoint_dir / f"{SOURCE}.checkpoint.json"
    seen: set = set()
    if cp_path.exists():
        try:
            seen = set(json.loads(cp_path.read_text())["seen"])
        except Exception:
            pass
    BATCH = 200
    batch: List[KnowledgeEntry] = []
    n_inserted = 0
    n_skipped = 0
    n_seen_total = 0
    last_progress = time.time()

    for s in _stream_stations():
        n_seen_total += 1
        sid = s["station_id"]
        if sid in seen:
            continue
        entry = _station_to_entry(s, source_version)
        if entry is None:
            n_skipped += 1
            seen.add(sid)
            continue
        batch.append(entry)
        seen.add(sid)
        if len(batch) >= BATCH:
            res = bank.insert_many(batch)
            n_inserted += res["n_inserted"]
            batch = []
            if progress_cb and time.time() - last_progress > 5:
                progress_cb({"source": SOURCE, "n_done": n_inserted})
                last_progress = time.time()
            cp_path.write_text(json.dumps({"seen": list(seen)}),
                                 encoding="utf-8")
    if batch:
        res = bank.insert_many(batch)
        n_inserted += res["n_inserted"]
    if n_seen_total == 0:
        return {"ok": False, "source": SOURCE,
                 "reason": "ndbc_station_table_unreachable",
                 "fix": "Check network / NDBC server status",
                 "n_inserted": 0}
    return {"ok": True, "source": SOURCE, "source_version": source_version,
             "n_inserted": n_inserted, "n_skipped": n_skipped,
             "license": LICENSE}
