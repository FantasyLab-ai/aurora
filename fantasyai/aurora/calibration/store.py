"""
Calibration corpus store — a shipped, versioned, integrity-hashed artifact.

The corpus is computed by a batch job (scripts/build_calibration_corpus.py)
and ships inside the package as ``corpus_<version>.json`` with a
``corpus_<version>.sha256`` sidecar. Runtime lookups load it from disk;
nothing is computed at analysis time.

The sha256 of the corpus FILE is the value that travels into findings and
bundles as ``corpus_sha256`` — a verifier can hash the shipped file and
confirm which table produced the claim.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Union

CORPUS_DIR = Path(__file__).resolve().parent
DEFAULT_CORPUS_VERSION = "v1"


class CorpusNotFoundError(FileNotFoundError):
    """No corpus artifact for the requested version. Callers translate
    this into calibration status 'not_yet_calibrated' — never a default."""


class CorpusIntegrityError(ValueError):
    """The corpus file does not match its recorded sha256. Callers
    translate this into 'unavailable' — a corrupted table must never be
    silently used."""


def corpus_path(version: str = DEFAULT_CORPUS_VERSION) -> Path:
    return CORPUS_DIR / f"corpus_{version}.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def save_corpus(corpus: Dict[str, Any], *, directory: Union[str, Path, None] = None
                ) -> Path:
    """Write the corpus JSON + sha256 sidecar. Returns the JSON path.

    Serialisation is canonical-ish (sorted keys, fixed separators) so the
    same corpus content always produces the same bytes and hash.
    """
    version = str(corpus.get("corpus_version") or DEFAULT_CORPUS_VERSION)
    out_dir = Path(directory) if directory is not None else CORPUS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"corpus_{version}.json"
    path.write_text(
        json.dumps(corpus, sort_keys=True, indent=1),
        encoding="utf-8", newline="\n",
    )
    digest = _sha256_file(path)
    (out_dir / f"corpus_{version}.sha256").write_text(
        digest + "\n", encoding="utf-8", newline="\n",
    )
    return path


def load_corpus(version: str = DEFAULT_CORPUS_VERSION, *,
                directory: Union[str, Path, None] = None,
                verify: bool = True) -> Dict[str, Any]:
    """Load a corpus, verifying the file hash against its sidecar.

    Raises CorpusNotFoundError / CorpusIntegrityError; never substitutes
    a default table.
    """
    base = Path(directory) if directory is not None else CORPUS_DIR
    path = base / f"corpus_{version}.json"
    if not path.exists():
        raise CorpusNotFoundError(
            f"no calibration corpus {version!r} at {path}"
        )
    digest = _sha256_file(path)
    if verify:
        sidecar = base / f"corpus_{version}.sha256"
        if not sidecar.exists():
            raise CorpusIntegrityError(
                f"corpus {version!r} has no sha256 sidecar at {sidecar}"
            )
        recorded = sidecar.read_text(encoding="utf-8").strip().split()[0]
        if recorded != digest:
            raise CorpusIntegrityError(
                f"corpus {version!r} hash mismatch: file {digest[:12]}… vs "
                f"recorded {recorded[:12]}… — refusing to use a modified table"
            )
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("format") != "aurora.calibration.corpus":
        raise CorpusIntegrityError(
            f"unexpected corpus format {doc.get('format')!r}"
        )
    doc["_sha256"] = digest  # convenience for lookup; not part of the file
    return doc


@lru_cache(maxsize=4)
def get_corpus_cached(version: str = DEFAULT_CORPUS_VERSION) -> Dict[str, Any]:
    """Cached loader for the runtime path. Same exceptions as load_corpus."""
    return load_corpus(version)
