"""
PDF text extraction with graceful fallback.

We prefer ``pypdf`` when installed (small, pure-Python, MIT). If it
isn't available, we look for plain-text neighbours (`paper.txt`,
`paper.md`) the user might have produced via their own OCR.
Encrypted PDFs without a known password get a clear error.

Output: a list of ``PDFTextChunk`` records — one per page or per
section, with provenance back to the source path + offset.

We deliberately don't pull in pdfplumber / pdfminer.six / unstructured.
Those are heavy and bring transitive deps that change Aurora's
install footprint. Users who need OCR or rich layout extraction
should preprocess to text + drop the .txt next to the PDF.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


class PDFIngestError(RuntimeError):
    """Raised when a PDF can't be opened or has no extractable text."""


@dataclass
class PDFTextChunk:
    """One chunk of extracted text + its provenance."""
    source_path: str
    source_hash: str          # SHA-256 of the source file
    page_number: Optional[int]
    section_index: int
    text: str
    n_chars: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _chunk_long_text(
    text: str,
    *,
    source_path: str,
    source_hash: str,
    page_number: Optional[int],
    max_chars: int = 4000,
) -> List[PDFTextChunk]:
    """Split a long string into ~max_chars chunks at paragraph
    boundaries when possible."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [PDFTextChunk(
            source_path=source_path, source_hash=source_hash,
            page_number=page_number, section_index=0,
            text=text, n_chars=len(text),
        )]
    chunks: List[PDFTextChunk] = []
    idx = 0
    buf: List[str] = []
    buf_chars = 0
    section = 0
    for para in text.split("\n\n"):
        if not para.strip():
            continue
        if buf_chars + len(para) + 2 > max_chars and buf:
            chunks.append(PDFTextChunk(
                source_path=source_path, source_hash=source_hash,
                page_number=page_number, section_index=section,
                text="\n\n".join(buf), n_chars=buf_chars,
            ))
            section += 1
            buf, buf_chars = [], 0
        buf.append(para)
        buf_chars += len(para) + 2
    if buf:
        chunks.append(PDFTextChunk(
            source_path=source_path, source_hash=source_hash,
            page_number=page_number, section_index=section,
            text="\n\n".join(buf), n_chars=buf_chars,
        ))
    return chunks


def extract_text_from_pdf(path: Any,
                           *,
                           max_chars_per_chunk: int = 4000) -> List[PDFTextChunk]:
    """Extract text from a PDF and return a list of PDFTextChunk.

    Strategy:
        1. If ``pypdf`` is installed, parse the PDF page by page; chunk
           each page's text.
        2. If a plain-text neighbour exists (`<stem>.txt` or
           `<stem>.md`), use it.
        3. Else raise PDFIngestError.

    Raises:
        PDFIngestError on unreadable / encrypted / empty PDFs.
    """
    p = Path(path)
    if not p.exists():
        raise PDFIngestError(f"file does not exist: {p}")
    src_hash = _file_sha256(p)

    # Strategy 1: pypdf.
    try:
        from pypdf import PdfReader
    except Exception:
        PdfReader = None  # type: ignore

    if PdfReader is not None and p.suffix.lower() == ".pdf":
        try:
            reader = PdfReader(str(p))
            if getattr(reader, "is_encrypted", False):
                # Try empty-password unlock; otherwise refuse.
                try:
                    reader.decrypt("")
                except Exception:
                    raise PDFIngestError(
                        f"PDF is password-protected: {p}"
                    )
            all_chunks: List[PDFTextChunk] = []
            for page_idx, page in enumerate(reader.pages):
                try:
                    txt = page.extract_text() or ""
                except Exception:
                    txt = ""
                if not txt.strip():
                    continue
                all_chunks.extend(_chunk_long_text(
                    txt,
                    source_path=str(p), source_hash=src_hash,
                    page_number=page_idx + 1,
                    max_chars=max_chars_per_chunk,
                ))
            if all_chunks:
                return all_chunks
            # Empty PDF — fall through to text-neighbour strategy.
        except PDFIngestError:
            raise
        except Exception as e:
            # Not a fatal error — try the text-neighbour fallback.
            last_err = f"pypdf extract failed: {type(e).__name__}: {e}"
        else:
            last_err = "no extractable text in PDF (likely image-only / OCR needed)"
    else:
        last_err = "pypdf not installed and file isn't .pdf"

    # Strategy 2: plain-text neighbour.
    for ext in (".txt", ".md"):
        sibling = p.with_suffix(ext)
        if sibling.exists():
            try:
                txt = sibling.read_text(encoding="utf-8", errors="replace")
                return _chunk_long_text(
                    txt,
                    source_path=str(sibling),
                    source_hash=_file_sha256(sibling),
                    page_number=None,
                    max_chars=max_chars_per_chunk,
                )
            except Exception:
                continue

    raise PDFIngestError(
        f"could not extract text from {p}; {last_err}. "
        f"Install pypdf (`pip install pypdf`) or place a "
        f"plain-text version next to the PDF (e.g. {p.with_suffix('.txt').name})."
    )
