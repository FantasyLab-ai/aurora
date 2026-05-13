"""
Stage 2B — UniProt human Swiss-Prot ingestion (~20K entries).

Source: https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/proteomes/UP000005640_9606.dat.gz
License: CC BY 4.0

Maintainer setup:
  1. Download UP000005640_9606.dat.gz (~50 MB) to
     ~/.aurora/knowledge_bank/source_cache/uniprot_human.dat.gz
  2. ``pip install biopython`` (Swiss-Prot parser)
  3. Run::
        python -m fantasyai.aurora.knowledge_bank.ingest --source uniprot

Schema:
  id:              uniprot_<accession>
  source:          "uniprot"
  domain:          ["bio", "medical"]
  concept_type:    "protein"
  name:            recommended name
  aliases:         alt names + gene names
  definition:      function annotations
  mechanism:       subcellular location + PTMs + tissue expression
  related_concepts: cross-referenced GO IDs (links UniProt → GO)
  references:      PubMed citations
"""
from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from ...schema import (
    KnowledgeEntry, EntryReference, attach_license,
)
from ...storage.sqlite_store import KnowledgeBankStore


SOURCE = "uniprot"
LICENSE = "CC BY 4.0"


def _default_path() -> Path:
    return (Path(os.environ.get("AURORA_KB_PATH",
                                    Path.home() / ".aurora" / "knowledge_bank" /
                                    "main.db")).parent
            / "source_cache" / "uniprot_human.dat.gz")


# ---------------------------------------------------------------------------
# Streaming Swiss-Prot record parser (graceful when biopython absent)
# ---------------------------------------------------------------------------

def _stream_records(dat_path: Path) -> Iterator[Any]:
    """Yield Bio.SwissProt.Record objects. Lazy import; if biopython
    isn't installed, yields nothing (caller handles)."""
    try:
        from Bio import SwissProt
    except Exception:
        return
    opener = gzip.open if str(dat_path).endswith(".gz") else open
    try:
        with opener(dat_path, "rt", encoding="utf-8", errors="replace") as f:
            for record in SwissProt.parse(f):
                yield record
    except Exception:
        return


def _record_to_entry(rec: Any, source_version: str) -> Optional[KnowledgeEntry]:
    accession = (rec.accessions or [None])[0]
    if not accession:
        return None
    name = rec.entry_name or accession
    # Recommended name from DE field.
    desc = (rec.description or "").split(";")[0].strip()
    if "RecName: Full=" in desc:
        rec_name = desc.split("RecName: Full=", 1)[1].split(";")[0].strip()
        rec_name = rec_name.rstrip("{}").strip()
    else:
        rec_name = desc
    if not rec_name:
        rec_name = accession

    aliases: List[str] = []
    # Alt names + gene names.
    for line in (rec.gene_name or "").split(";"):
        if "Name=" in line:
            gn = line.split("Name=")[1].split("{")[0].split(",")[0].strip()
            if gn:
                aliases.append(gn)

    # Definition: function comments (CC FUNCTION).
    function_lines: List[str] = []
    mechanism_lines: List[str] = []
    for c in (rec.comments or []):
        if c.startswith("FUNCTION:"):
            function_lines.append(c[len("FUNCTION:"):].strip())
        elif c.startswith("SUBCELLULAR LOCATION:"):
            mechanism_lines.append(c.strip())
        elif c.startswith("PTM:"):
            mechanism_lines.append(c.strip())
        elif c.startswith("TISSUE SPECIFICITY:"):
            mechanism_lines.append(c.strip())
    definition = " ".join(function_lines) or rec_name
    mechanism = " ".join(mechanism_lines)

    # GO cross-references.
    related: List[str] = []
    refs: List[EntryReference] = []
    for xr in (rec.cross_references or []):
        if not xr:
            continue
        db = xr[0]
        if db == "GO" and len(xr) >= 2:
            go_id = xr[1].replace("GO:", "")
            related.append(f"go_{go_id}")
        elif db == "PubMed" and len(xr) >= 2:
            refs.append(EntryReference(citation=f"PMID:{xr[1]}"))
    refs.append(EntryReference(
        citation=f"UniProt {accession}",
        url=f"https://www.uniprot.org/uniprotkb/{accession}",
    ))

    entry = KnowledgeEntry(
        id=f"uniprot_{accession}",
        source=SOURCE,
        source_version=source_version,
        domain=["bio", "medical"],
        concept_type="protein",
        review_status="auto_approved",
        ingest_method="swissprot_parse",
        name=rec_name,
        aliases=aliases[:8],
        definition=definition[:3000],
        mechanism=mechanism[:3000],
        related_concepts=related[:30],
        references=refs[:5],
    )
    return attach_license(entry, LICENSE)


def run_ingest(bank: KnowledgeBankStore, checkpoint_dir: Path,
                 *, progress_cb: Optional[Callable[[Dict], None]] = None,
                 dat_path: Optional[Path] = None,
                 ) -> Dict[str, Any]:
    dat_path = Path(dat_path) if dat_path else _default_path()
    if not dat_path.exists():
        return {"ok": False, "source": SOURCE,
                 "reason": "missing_uniprot_dat_file",
                 "fix": (f"Download UP000005640_9606.dat.gz to {dat_path}\n"
                          f"  curl -o {dat_path} https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/proteomes/UP000005640_9606.dat.gz\n"
                          f"  pip install biopython"),
                 "n_inserted": 0}
    try:
        import Bio  # noqa
    except Exception:
        return {"ok": False, "source": SOURCE,
                 "reason": "biopython_not_installed",
                 "fix": "pip install biopython",
                 "n_inserted": 0}

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
    last_progress = time.time()
    try:
        for rec in _stream_records(dat_path):
            acc = (rec.accessions or [None])[0]
            if not acc or acc in seen:
                continue
            entry = _record_to_entry(rec, source_version)
            if entry is None:
                seen.add(acc)
                continue
            batch.append(entry)
            seen.add(acc)
            if len(batch) >= BATCH:
                res = bank.insert_many(batch)
                n_inserted += res["n_inserted"]
                batch = []
                if progress_cb and time.time() - last_progress > 5:
                    progress_cb({"source": SOURCE, "n_done": n_inserted})
                    last_progress = time.time()
                cp_path.write_text(json.dumps({"seen": list(seen),
                                                  "n_inserted": n_inserted}),
                                    encoding="utf-8")
        if batch:
            res = bank.insert_many(batch)
            n_inserted += res["n_inserted"]
    except KeyboardInterrupt:
        cp_path.write_text(json.dumps({"seen": list(seen),
                                          "n_inserted": n_inserted}),
                            encoding="utf-8")
        raise

    return {"ok": True, "source": SOURCE, "source_version": source_version,
             "n_inserted": n_inserted, "license": LICENSE}
