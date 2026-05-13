"""
Stage 2A — Gene Ontology ingestion (~50K terms).

Source: http://current.geneontology.org/ontology/go.obo
License: CC BY 4.0

Maintainer setup:
  1. Download go.obo (~150 MB) from current.geneontology.org/ontology/go.obo
     to ~/.aurora/knowledge_bank/source_cache/go.obo
  2. ``pip install obonet``  (small dep — pure Python OBO parser)
  3. Run::
        python -m fantasyai.aurora.knowledge_bank.ingest --source gene_ontology

Schema mapping per GO term:
  id:               go_<term_id>            (e.g. go_0007623)
  source:           "gene_ontology"
  source_version:   data-version field from the OBO header
  domain:           ["bio", "medical"]
  concept_type:     namespace (biological_process | molecular_function |
                              cellular_component)
  name:             GO name
  aliases:          synonyms
  definition:       def: field (with quote-escaping handled)
  related_concepts: is_a + part_of + regulates → other GO IDs
  references:       cross-references with PMID prefix → EntryReference
  ingest_metadata:  auto_approved, ontology_etl, license CC BY 4.0
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from ...schema import (
    KnowledgeEntry, EntryReference, attach_license,
)
from ...storage.sqlite_store import KnowledgeBankStore


SOURCE = "gene_ontology"
LICENSE = "CC BY 4.0"


def _default_obo_path() -> Path:
    return (Path(os.environ.get("AURORA_KB_PATH",
                                    Path.home() / ".aurora" / "knowledge_bank" /
                                    "main.db")).parent
            / "source_cache" / "go.obo")


# ---------------------------------------------------------------------------
# OBO parser — small streaming implementation so we don't pull obonet
# as a hard dependency. Handles the GO subset we need.
# ---------------------------------------------------------------------------

_DEF_RE = re.compile(r'^def: "(?P<text>(?:[^"\\]|\\.)*)"(?:\s+\[(?P<refs>[^\]]*)\])?')
_TERM_REF_RE = re.compile(r"^(?P<prefix>[A-Za-z]+):(?P<id>[\w.-]+)")


def _stream_terms(obo_path: Path) -> Iterator[Dict[str, Any]]:
    """Yield one dict per [Term] block in the OBO file. Header parsed
    for ``data-version`` which we surface as the source_version."""
    if not obo_path.exists():
        return
    block: Optional[Dict[str, Any]] = None
    with obo_path.open("r", encoding="utf-8", errors="replace") as f:
        in_term = False
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("[Term]"):
                if block is not None:
                    yield block
                block = {"id": None, "name": None, "namespace": None,
                          "def": None, "synonyms": [], "is_a": [],
                          "part_of": [], "regulates": [], "xrefs": [],
                          "alt_id": [], "is_obsolete": False}
                in_term = True
                continue
            if line.startswith("[") and line.endswith("]"):
                # Some other stanza (e.g. [Typedef])
                if block is not None:
                    yield block
                block = None
                in_term = False
                continue
            if not in_term or block is None:
                continue
            if line.startswith("id: "):
                block["id"] = line[4:].strip()
            elif line.startswith("name: "):
                block["name"] = line[6:].strip()
            elif line.startswith("namespace: "):
                block["namespace"] = line[11:].strip()
            elif line.startswith("def: "):
                m = _DEF_RE.match(line)
                if m:
                    block["def"] = m.group("text").replace("\\\"", "\"")
                    if m.group("refs"):
                        for ref in m.group("refs").split(","):
                            block["xrefs"].append(ref.strip())
            elif line.startswith("synonym: "):
                # synonym: "alpha-glucosyltransferase activity" RELATED [GOC:dph]
                m = re.match(r'synonym: "([^"]+)"', line)
                if m:
                    block["synonyms"].append(m.group(1))
            elif line.startswith("is_a: "):
                target = line[6:].split("!", 1)[0].strip()
                if target.startswith("GO:"):
                    block["is_a"].append(target)
            elif line.startswith("relationship: "):
                rest = line[14:].split("!", 1)[0].strip()
                parts = rest.split()
                if len(parts) >= 2 and parts[1].startswith("GO:"):
                    rel = parts[0]
                    if rel == "part_of":
                        block["part_of"].append(parts[1])
                    elif rel.startswith("regulates"):
                        block["regulates"].append(parts[1])
            elif line.startswith("xref: "):
                xref = line[6:].strip()
                # Trim trailing comments.
                xref = xref.split("!", 1)[0].strip()
                if xref:
                    block["xrefs"].append(xref)
            elif line.startswith("alt_id: "):
                block["alt_id"].append(line[8:].strip())
            elif line.startswith("is_obsolete: true"):
                block["is_obsolete"] = True
        # Final block.
        if block is not None:
            yield block


def _parse_data_version(obo_path: Path) -> str:
    """Extract data-version from the OBO header (first ~30 lines)."""
    if not obo_path.exists():
        return time.strftime("%Y-%m-%d")
    with obo_path.open("r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i > 30:
                break
            if line.startswith("data-version: "):
                return line[14:].strip()
    return time.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Schema mapping
# ---------------------------------------------------------------------------

def _term_to_entry(term: Dict[str, Any], source_version: str
                     ) -> Optional[KnowledgeEntry]:
    if term.get("is_obsolete"):
        return None
    tid = term.get("id")
    if not tid or not tid.startswith("GO:"):
        return None
    if not term.get("name") or not term.get("def"):
        return None
    namespace = term.get("namespace") or "biological_process"
    related: List[str] = []
    for kind in ("is_a", "part_of", "regulates"):
        for r in term.get(kind, []):
            if r.startswith("GO:"):
                related.append(f"go_{r.split(':', 1)[1]}")
    refs: List[EntryReference] = []
    for x in term.get("xrefs", []):
        m = _TERM_REF_RE.match(x)
        if m and m.group("prefix") in ("PMID", "DOI"):
            refs.append(EntryReference(
                citation=f"{m.group('prefix')}:{m.group('id')}",
                doi=m.group("id") if m.group("prefix") == "DOI" else None,
            ))
    entry = KnowledgeEntry(
        id=f"go_{tid.split(':', 1)[1]}",
        source=SOURCE,
        source_version=source_version,
        domain=["bio", "medical"],
        concept_type=namespace,
        review_status="auto_approved",
        ingest_method="obo_parse",
        name=term["name"],
        aliases=term.get("synonyms") or [],
        definition=term["def"],
        related_concepts=related[:20],
        references=refs[:5],
        quality_score=0.95 if refs else 0.85,
    )
    return attach_license(entry, LICENSE)


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------

def run_ingest(bank: KnowledgeBankStore, checkpoint_dir: Path,
                 *, progress_cb: Optional[Callable[[Dict], None]] = None,
                 obo_path: Optional[Path] = None,
                 ) -> Dict[str, Any]:
    obo_path = Path(obo_path) if obo_path else _default_obo_path()
    if not obo_path.exists():
        return {
            "ok": False, "source": SOURCE,
            "reason": "missing_obo_file",
            "fix": (f"Download go.obo to {obo_path}:\n"
                     f"  curl -o {obo_path} http://current.geneontology.org/ontology/go.obo"),
            "n_inserted": 0,
        }

    source_version = _parse_data_version(obo_path)
    cp_path = checkpoint_dir / f"{SOURCE}.checkpoint.json"
    seen_ids: set = set()
    if cp_path.exists():
        try:
            seen_ids = set(json.loads(cp_path.read_text())["seen_ids"])
        except Exception:
            pass

    BATCH = 500
    batch: List[KnowledgeEntry] = []
    n_inserted = 0
    n_skipped = 0
    last_progress = time.time()

    try:
        for term in _stream_terms(obo_path):
            tid = term.get("id")
            if not tid or tid in seen_ids:
                continue
            entry = _term_to_entry(term, source_version)
            if entry is None:
                n_skipped += 1
                seen_ids.add(tid)
                continue
            batch.append(entry)
            seen_ids.add(tid)
            if len(batch) >= BATCH:
                res = bank.insert_many(batch)
                n_inserted += res["n_inserted"]
                batch = []
                if progress_cb and time.time() - last_progress > 5:
                    progress_cb({"source": SOURCE, "stage": "ingesting",
                                  "n_done": n_inserted, "n_skipped": n_skipped})
                    last_progress = time.time()
                # Periodic checkpoint.
                tmp = cp_path.with_suffix(".tmp")
                tmp.write_text(json.dumps({
                    "seen_ids": list(seen_ids),
                    "source_version": source_version,
                    "n_inserted": n_inserted,
                }), encoding="utf-8")
                tmp.replace(cp_path)
        if batch:
            res = bank.insert_many(batch)
            n_inserted += res["n_inserted"]
    except KeyboardInterrupt:
        cp_path.write_text(json.dumps({
            "seen_ids": list(seen_ids),
            "source_version": source_version,
            "n_inserted": n_inserted,
        }), encoding="utf-8")
        raise

    return {"ok": True, "source": SOURCE, "source_version": source_version,
             "n_inserted": n_inserted, "n_skipped": n_skipped,
             "license": LICENSE, "obo_path": str(obo_path)}
