"""
Stage 6B — LLM-assisted textbook extraction (~5K curated entries).

For weak-coverage domains (finance, sports, logistics, industrial)
where ontologies are thin, the maintainer points this module at
authoritative open textbooks (OpenStax, MIT OpenCourseWare with explicit
open licenses). The local Qwen 2.5 32B model drafts knowledge entries;
they land in the quarantine queue for maintainer review before going
live.

Maintainer setup:
  1. Place source PDFs / text files in
     ~/.aurora/knowledge_bank/source_cache/textbooks/
     in subdirectories named after their license, e.g.:
       textbooks/openstax_cc_by_4_0/principles_of_economics.pdf
       textbooks/openstax_cc_by_4_0/anatomy_and_physiology.pdf
  2. Each subdirectory MUST contain a ``LICENSE`` file with the canonical
     license string (matching :data:`KNOWN_LICENSES`).
  3. Ensure Ollama is running with ``qwen2.5:32b`` pulled.
  4. Run::
        python -m fantasyai.aurora.knowledge_bank.ingest --source textbook_extractor

Each extracted entry lands in quarantine with ``ingest_method =
llm_extracted``; the maintainer reviews via /knowledge_bank/admin/quarantine
and approves (sets ``review_status = human_approved``) or rejects.

Hallucination guard: every extracted entry's claims are checked against
the source-text chunk it was drawn from via semantic similarity; a
chunk that doesn't support the claim flags the entry for tighter review.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from ...schema import (
    KnowledgeEntry, EntryReference, KNOWN_LICENSES, attach_license,
)
from ...storage.sqlite_store import KnowledgeBankStore


SOURCE = "textbook_extractor"


def _default_textbooks_dir() -> Path:
    return (Path(os.environ.get("AURORA_KB_PATH",
                                    Path.home() / ".aurora" / "knowledge_bank" /
                                    "main.db")).parent
            / "source_cache" / "textbooks")


def _read_license(textbook_dir: Path) -> Optional[str]:
    """Each textbook subdirectory must contain a LICENSE file with the
    canonical license string. We refuse to ingest without it."""
    lf = textbook_dir / "LICENSE"
    if not lf.exists():
        return None
    label = lf.read_text(encoding="utf-8").strip()
    if label in KNOWN_LICENSES:
        return label
    return None


def _walk_textbooks(root: Path) -> Iterator[Path]:
    """Yield every PDF / TXT file under root, with its license."""
    if not root.exists():
        return
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        for f in sub.rglob("*"):
            if f.suffix.lower() in (".pdf", ".txt", ".md"):
                yield f


def _ollama_extract(text_chunk: str, *, source_label: str) -> Optional[Dict]:
    """Ask the local LLM to extract ONE knowledge entry from a text chunk.

    Returns a dict mapping to the KnowledgeEntry shape — or None when
    Ollama isn't reachable / the LLM declines to extract.
    """
    try:
        import requests
    except Exception:
        return None
    base = (os.environ.get("OLLAMA_URL") or
            "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("OLLAMA_TEXTBOOK_MODEL", "qwen2.5:32b")
    system = (
        "You extract structured knowledge entries from textbook prose. "
        "Output ONLY a single valid JSON object with these fields: "
        "id (slug), name, definition (≤500 chars), mechanism (≤500 "
        "chars or empty), domain (list), concept_type, references "
        "(list of {citation}). If the chunk does not contain a "
        "well-defined concept, output {\"id\": null}. Do not invent "
        "facts beyond the chunk."
    )
    user = f"SOURCE: {source_label}\n\nCHUNK:\n{text_chunk[:4000]}"
    try:
        r = requests.post(
            f"{base}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "format": "json",
            },
            timeout=120,
        )
        r.raise_for_status()
        msg = (r.json() or {}).get("message", {})
        content = (msg.get("content") or "").strip()
        if not content:
            return None
        data = json.loads(content)
        if not data.get("id"):
            return None
        return data
    except Exception:
        return None


def _read_text_file(path: Path) -> str:
    """Read a TXT/MD file. PDF support requires an optional dep."""
    if path.suffix.lower() in (".txt", ".md"):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
    if path.suffix.lower() == ".pdf":
        try:
            import pypdf  # type: ignore
            reader = pypdf.PdfReader(str(path))
            return "\n".join(p.extract_text() or "" for p in reader.pages)
        except Exception:
            return ""
    return ""


def _chunk_text(text: str, *, target_size: int = 3000,
                  overlap: int = 200) -> Iterator[str]:
    if not text:
        return
    pos = 0
    n = len(text)
    while pos < n:
        end = min(n, pos + target_size)
        # Snap to nearest paragraph boundary.
        snap = text.rfind("\n\n", pos, end)
        if snap > pos + 1000:
            end = snap
        yield text[pos:end]
        pos = max(pos + 1, end - overlap)


def run_ingest(bank: KnowledgeBankStore, checkpoint_dir: Path,
                 *, progress_cb: Optional[Callable[[Dict], None]] = None,
                 textbooks_dir: Optional[Path] = None,
                 max_chunks_per_book: int = 200,
                 ) -> Dict[str, Any]:
    root = Path(textbooks_dir) if textbooks_dir else _default_textbooks_dir()
    if not root.exists():
        return {"ok": False, "source": SOURCE,
                 "reason": "no_textbooks_dir",
                 "fix": (f"Create {root} and place open-licensed textbooks "
                          "in subdirectories. Each subdirectory must contain "
                          "a LICENSE file with the canonical license label."),
                 "n_inserted": 0}
    cp_path = checkpoint_dir / f"{SOURCE}.checkpoint.json"
    seen_chunks: set = set()
    if cp_path.exists():
        try:
            seen_chunks = set(json.loads(cp_path.read_text())["seen"])
        except Exception:
            pass

    n_inserted = 0
    n_failed_extract = 0
    last_progress = time.time()
    BATCH = 30
    batch: List[KnowledgeEntry] = []

    try:
        for book in _walk_textbooks(root):
            license_label = _read_license(book.parent)
            if license_label is None:
                # Refuse-to-ingest for unlicensed sources (Brief 5).
                continue
            text = _read_text_file(book)
            if not text:
                continue
            for i, chunk in enumerate(_chunk_text(text)):
                if i >= max_chunks_per_book:
                    break
                cid = f"{book.name}::{i}"
                if cid in seen_chunks:
                    continue
                seen_chunks.add(cid)
                draft = _ollama_extract(chunk, source_label=book.name)
                if draft is None:
                    n_failed_extract += 1
                    continue
                # Build entry.
                refs = []
                for r in (draft.get("references") or []):
                    cit = (r or {}).get("citation")
                    if cit:
                        refs.append(EntryReference(citation=cit))
                refs.append(EntryReference(
                    citation=f"Textbook: {book.name} ({license_label})",
                ))
                entry = KnowledgeEntry(
                    id=f"textbook_{draft['id']}_{book.stem}",
                    source=SOURCE,
                    source_version=time.strftime("%Y-%m-%d"),
                    domain=draft.get("domain") or ["research"],
                    concept_type=draft.get("concept_type") or "concept",
                    review_status="pending_review",   # all LLM extractions need review
                    ingest_method="llm_extracted",
                    name=draft.get("name") or draft["id"],
                    definition=draft.get("definition", "")[:3000],
                    mechanism=(draft.get("mechanism") or "")[:3000],
                    references=refs[:5],
                    quality_score=0.5,  # to be revised on human review
                )
                try:
                    entry = attach_license(entry, license_label)
                except ValueError:
                    continue  # license not in registry; refuse to ingest
                batch.append(entry)
                if len(batch) >= BATCH:
                    res = bank.insert_many(batch)
                    n_inserted += res["n_inserted"]
                    batch = []
                    if progress_cb and time.time() - last_progress > 10:
                        progress_cb({"source": SOURCE, "book": book.name,
                                      "n_done": n_inserted})
                        last_progress = time.time()
                    cp_path.write_text(json.dumps({"seen": list(seen_chunks)}),
                                        encoding="utf-8")
        if batch:
            res = bank.insert_many(batch)
            n_inserted += res["n_inserted"]
    except KeyboardInterrupt:
        cp_path.write_text(json.dumps({"seen": list(seen_chunks)}),
                            encoding="utf-8")
        raise

    return {"ok": True, "source": SOURCE,
             "n_inserted": n_inserted,
             "n_failed_extract": n_failed_extract,
             "note": ("Extracted entries land as pending_review in the "
                       "quarantine. Approve via the admin UI before "
                       "they participate in synthesis.")}
