"""
Stage 5 — Wikidata selective slice (~500K entities target).

Source: https://dumps.wikimedia.org/wikidatawiki/entities/latest-all.json.gz
License: CC0 1.0

Maintainer setup:
  1. Download latest-all.json.gz (~100 GB compressed) — large.
  2. ``pip install ijson`` for streaming JSON parsing.
  3. Set ``WIKIDATA_DUMP_PATH`` env var to the dump file.
  4. Run::
        python -m fantasyai.aurora.knowledge_bank.ingest --source wikidata

Filtering strategy: include entities matching Aurora-relevant
``instance_of`` (P31) classes; require ≥10 statements; require an
English Wikipedia sitelink. Exclude persons / fictional entities /
artworks.
"""
from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set

from ...schema import (
    KnowledgeEntry, EntryReference, attach_license,
)
from ...storage.sqlite_store import KnowledgeBankStore


SOURCE = "wikidata"
LICENSE = "CC0 1.0"

# Aurora-relevant instance_of classes (Wikidata Q-IDs).
# This curated set is the maintainer's policy — adjust as the
# domain priorities evolve. Each entry has a domain mapping.
RELEVANT_CLASSES: Dict[str, List[str]] = {
    # Scientific concepts
    "Q11862829":   ["physics"],            # academic discipline
    "Q1047113":    ["bio"],                 # biological process
    "Q11173":      ["physics", "bio"],     # chemical compound
    "Q41217":      ["physics"],            # chemical element
    "Q170790":     ["physics"],            # physical quantity
    "Q12136":      ["medical"],            # disease
    "Q12140":      ["medical"],            # medication
    "Q11173":      ["medical", "bio"],     # chemical compound (medical)
    "Q8054":       ["bio"],                 # protein
    "Q7187":       ["bio"],                 # gene
    # Geographic features
    "Q23397":      ["enviro"],             # lake
    "Q4022":       ["enviro"],             # river
    "Q8502":       ["enviro"],             # mountain
    "Q165":        ["enviro"],             # sea
    "Q165":        ["enviro"],             # ocean
    "Q63131":      ["enviro"],             # peninsula
    # Climate / atmosphere
    "Q7942":       ["enviro"],             # cyclone
    "Q8004":       ["enviro"],             # hurricane
    "Q35958":      ["enviro"],             # tornado
    # Economic / financial
    "Q4830453":    ["economics"],           # business
    "Q4287":       ["economics"],           # economy
    "Q188784":     ["economics"],           # commodity
    # Statistical / methodological
    "Q4101074":    ["research"],            # mathematical structure
    "Q1207505":    ["research"],            # statistical method
    "Q12483":      ["research"],            # algorithm
}

# Excluded classes — explicit reject list.
EXCLUDED_CLASSES: Set[str] = {
    "Q5",         # human
    "Q15632617",  # fictional human
    "Q838948",    # work of art
    "Q15416",     # television program (specific)
    "Q11424",     # film
}


# ---------------------------------------------------------------------------
# Streaming parser
# ---------------------------------------------------------------------------

def _iter_entities(dump_path: Path) -> Iterator[Dict[str, Any]]:
    """Stream-parse the Wikidata dump. Yields raw entity dicts."""
    try:
        import ijson  # type: ignore
    except Exception:
        return
    if not dump_path.exists():
        return
    opener = gzip.open if str(dump_path).endswith(".gz") else open
    with opener(dump_path, "rb") as f:
        # Wikidata dumps are top-level JSON arrays of entities.
        for entity in ijson.items(f, "item"):
            yield entity


def _instance_of_qids(entity: Dict[str, Any]) -> List[str]:
    claims = entity.get("claims", {}) or {}
    p31 = claims.get("P31") or []
    out = []
    for c in p31:
        try:
            qid = c["mainsnak"]["datavalue"]["value"]["id"]
            out.append(qid)
        except Exception:
            continue
    return out


def _entity_to_entry(entity: Dict[str, Any]
                       ) -> Optional[KnowledgeEntry]:
    qid = entity.get("id")
    if not qid or not qid.startswith("Q"):
        return None
    p31s = _instance_of_qids(entity)
    if not p31s:
        return None
    if any(c in EXCLUDED_CLASSES for c in p31s):
        return None
    domains: List[str] = []
    for c in p31s:
        if c in RELEVANT_CLASSES:
            domains.extend(RELEVANT_CLASSES[c])
    if not domains:
        return None
    domains = sorted(set(domains))

    n_statements = sum(len(v) for v in (entity.get("claims") or {}).values())
    if n_statements < 10:
        return None

    sitelinks = entity.get("sitelinks") or {}
    if "enwiki" not in sitelinks:
        return None

    labels = entity.get("labels") or {}
    name = (labels.get("en") or {}).get("value") or qid

    descriptions = entity.get("descriptions") or {}
    desc_en = (descriptions.get("en") or {}).get("value") or ""
    if not desc_en or len(desc_en) < 10:
        return None

    aliases_block = (entity.get("aliases") or {}).get("en") or []
    aliases = [a.get("value") for a in aliases_block if a.get("value")]

    refs = [EntryReference(
        citation=f"Wikidata {qid}",
        url=f"https://www.wikidata.org/wiki/{qid}",
    )]
    if "enwiki" in sitelinks:
        title = sitelinks["enwiki"].get("title")
        if title:
            url_title = title.replace(" ", "_")
            refs.append(EntryReference(
                citation=f"Wikipedia: {title}",
                url=f"https://en.wikipedia.org/wiki/{url_title}",
            ))

    review = "auto_approved" if n_statements >= 30 else "pending_review"
    entry = KnowledgeEntry(
        id=f"wikidata_{qid}",
        source=SOURCE,
        source_version=time.strftime("%Y-%m-%d"),
        domain=domains,
        concept_type=p31s[0],
        review_status=review,
        ingest_method="wikidata_extract",
        name=name,
        aliases=aliases[:10],
        definition=desc_en[:2000],
        references=refs,
        quality_score=min(1.0, 0.5 + n_statements / 200),
    )
    return attach_license(entry, LICENSE)


def run_ingest(bank: KnowledgeBankStore, checkpoint_dir: Path,
                 *, progress_cb: Optional[Callable[[Dict], None]] = None,
                 dump_path: Optional[Path] = None,
                 max_entries: Optional[int] = None,
                 ) -> Dict[str, Any]:
    dump_env = os.environ.get("WIKIDATA_DUMP_PATH")
    if dump_path is None and dump_env:
        dump_path = Path(dump_env)
    if not dump_path or not Path(dump_path).exists():
        return {"ok": False, "source": SOURCE,
                 "reason": "missing_dump",
                 "fix": ("Download Wikidata dump and set "
                          "WIKIDATA_DUMP_PATH env var:\n"
                          "  https://dumps.wikimedia.org/wikidatawiki/entities/\n"
                          "  pip install ijson"),
                 "n_inserted": 0}
    try:
        import ijson  # noqa
    except Exception:
        return {"ok": False, "source": SOURCE,
                 "reason": "ijson_not_installed",
                 "fix": "pip install ijson", "n_inserted": 0}

    cp_path = checkpoint_dir / f"{SOURCE}.checkpoint.json"
    seen: Set[str] = set()
    if cp_path.exists():
        try:
            seen = set(json.loads(cp_path.read_text())["seen"])
        except Exception:
            pass
    BATCH = 500
    batch: List[KnowledgeEntry] = []
    n_inserted = 0
    n_skipped = 0
    n_examined = 0
    last_progress = time.time()
    try:
        for entity in _iter_entities(Path(dump_path)):
            n_examined += 1
            qid = entity.get("id")
            if not qid or qid in seen:
                continue
            seen.add(qid)
            entry = _entity_to_entry(entity)
            if entry is None:
                n_skipped += 1
                continue
            batch.append(entry)
            if len(batch) >= BATCH:
                res = bank.insert_many(batch)
                n_inserted += res["n_inserted"]
                batch = []
                if progress_cb and time.time() - last_progress > 10:
                    progress_cb({"source": SOURCE,
                                  "n_examined": n_examined,
                                  "n_done": n_inserted,
                                  "n_skipped": n_skipped})
                    last_progress = time.time()
                cp_path.write_text(json.dumps({
                    "seen": list(seen)[-500000:],
                    "n_inserted": n_inserted,
                    "n_examined": n_examined,
                }), encoding="utf-8")
            if max_entries and n_inserted >= max_entries:
                break
        if batch:
            res = bank.insert_many(batch)
            n_inserted += res["n_inserted"]
    except KeyboardInterrupt:
        cp_path.write_text(json.dumps({
            "seen": list(seen)[-500000:],
            "n_inserted": n_inserted,
            "n_examined": n_examined,
        }), encoding="utf-8")
        raise

    return {"ok": True, "source": SOURCE,
             "n_inserted": n_inserted, "n_skipped": n_skipped,
             "n_examined": n_examined, "license": LICENSE}
