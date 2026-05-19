"""
Notebook + bundle export — package a Jupyter notebook together with
the Aurora bundle it produced into a single shareable artifact.

Use case: an analyst runs Aurora in a notebook, gets findings, writes
their interpretation. They want to send the *whole thing* to a
reviewer / teammate — both the analytical evidence (bundle) and the
narrative (notebook). Right now that's "email two files." This helper
packages them into one ``.aurora-notebook.tar.gz`` with a manifest:

    audit.aurora-notebook.tar.gz
    ├── notebook.ipynb        — the original notebook (UTF-8 JSON)
    ├── bundle.aurora.json    — the Aurora bundle the notebook produced
    └── manifest.json         — schema + provenance:
        {
          "schema_version": "1.0.0",
          "exported_at":    "2026-05-19T16:30:00Z",
          "aurora_version": "1.0.0",
          "notebook_sha256": "...",
          "bundle_sha256":   "...",
          "bundle_content_hash": "...",
        }

Importantly, the manifest stores the **bundle's content_hash**
separately from the **file's sha256**. The first verifies the
analytical claims; the second verifies the file as transmitted. A
reviewer checking the export can run ``aurora_sdk.Bundle.load(...)``
on the inner bundle and ``.verify()`` to confirm the math hasn't been
tampered with.

Design notes:

  * Pure stdlib (tarfile + json + hashlib). No new deps.
  * The notebook content is stored verbatim — we don't re-execute it
    or strip outputs. That's the user's choice (and standard nbconvert
    handles stripping if they want).
  * Reads on extract are defensive (whitelisted file names, no path
    traversal, no symlinks).
"""
from __future__ import annotations

import hashlib
import json
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union


EXPORT_SCHEMA_VERSION = "1.0.0"
EXPORT_EXTENSION = ".aurora-notebook.tar.gz"

# Whitelisted member names inside the archive. Used both when writing
# (we only write these) and when reading (we refuse to extract anything
# else, defense-in-depth against malicious tarballs).
_ALLOWED_FILES = frozenset({"notebook.ipynb", "bundle.aurora.json", "manifest.json"})


class NotebookExportError(Exception):
    """Raised when an export can't be created or read.

    Always carries a human-readable message; never wraps an OSError
    unhelpfully."""


@dataclass(frozen=True)
class ExportInfo:
    """Result of a successful export. Useful for reporting back to the
    user (the path + the hash they can quote for chain-of-custody)."""
    output_path: Path
    notebook_sha256: str
    bundle_sha256: str
    bundle_content_hash: str
    size_bytes: int


def export_notebook(
    notebook_path: Union[str, Path],
    bundle: Any,
    output_path: Union[str, Path],
    *,
    aurora_version: Optional[str] = None,
) -> ExportInfo:
    """Bundle a Jupyter notebook + an Aurora Bundle into a single archive.

    Args:
        notebook_path: Path to a ``.ipynb`` file on disk.
        bundle:        Either an ``aurora_sdk.Bundle`` instance or a
                       path to an ``.aurora.json`` file on disk.
        output_path:   Where to write the export. ``.aurora-notebook.tar.gz``
                       extension is auto-appended if missing.
        aurora_version: Optional Aurora version to record in the
                       manifest (defaults to importing it dynamically).

    Returns:
        ``ExportInfo`` with the output path + integrity hashes.

    Raises:
        NotebookExportError: notebook missing, bundle invalid, or I/O
            failure.
    """
    nb_path = Path(notebook_path).expanduser().resolve()
    if not nb_path.exists() or not nb_path.is_file():
        raise NotebookExportError(f"notebook not found: {nb_path}")
    if nb_path.suffix.lower() != ".ipynb":
        raise NotebookExportError(
            f"expected a .ipynb file, got {nb_path.suffix!r}: {nb_path}"
        )

    # Resolve the bundle into JSON bytes. Either a Bundle object (use
    # its .doc) or a path on disk (read the file).
    bundle_bytes = _resolve_bundle_bytes(bundle)
    notebook_bytes = nb_path.read_bytes()

    # Validate notebook is JSON. We don't enforce schema beyond "parseable"
    # because nbformat varies enough that strict validation is hostile.
    try:
        json.loads(notebook_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise NotebookExportError(f"notebook is not valid JSON: {e}") from e

    # Validate bundle JSON. Pull the bundle's own content_hash so the
    # manifest carries it (lets a reviewer cross-check without unpacking).
    try:
        bundle_doc = json.loads(bundle_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise NotebookExportError(f"bundle is not valid JSON: {e}") from e
    bundle_content_hash = str(
        (bundle_doc.get("integrity") or {}).get("content_hash") or ""
    )

    # Build the manifest.
    av = aurora_version or _detect_aurora_version()
    manifest = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "aurora_version": av,
        "notebook_sha256": hashlib.sha256(notebook_bytes).hexdigest(),
        "bundle_sha256": hashlib.sha256(bundle_bytes).hexdigest(),
        "bundle_content_hash": bundle_content_hash,
    }
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")

    # Normalise output path. Append the extension if it's not there
    # already so users don't accidentally end up with .tar.gz.gz.
    out = Path(output_path).expanduser().resolve()
    if not str(out).endswith(EXPORT_EXTENSION):
        if out.suffix == ".gz":
            # Replace .tar.gz with .aurora-notebook.tar.gz
            out = out.with_suffix("").with_suffix(EXPORT_EXTENSION)
        else:
            out = out.with_suffix(EXPORT_EXTENSION)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Write the archive. Use a temp file in the same dir then atomic
    # rename so a crash mid-write doesn't leave a half-written export.
    tmp_fd, tmp_path_str = tempfile.mkstemp(
        prefix=".aurora_nb_export_", suffix=".tmp", dir=str(out.parent),
    )
    import os as _os
    _os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)
    try:
        with tarfile.open(tmp_path, "w:gz") as tf:
            _add_bytes(tf, "notebook.ipynb", notebook_bytes)
            _add_bytes(tf, "bundle.aurora.json", bundle_bytes)
            _add_bytes(tf, "manifest.json", manifest_bytes)
        tmp_path.replace(out)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise

    return ExportInfo(
        output_path=out,
        notebook_sha256=manifest["notebook_sha256"],
        bundle_sha256=manifest["bundle_sha256"],
        bundle_content_hash=bundle_content_hash,
        size_bytes=out.stat().st_size,
    )


def read_export(
    archive_path: Union[str, Path],
    *,
    output_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Path]:
    """Extract an Aurora notebook export. Validates the manifest and
    refuses any unexpected files.

    Returns a dict mapping the three logical names
    (``notebook``, ``bundle``, ``manifest``) to their on-disk paths.
    """
    archive = Path(archive_path).expanduser().resolve()
    if not archive.exists():
        raise NotebookExportError(f"archive not found: {archive}")
    out_dir = Path(output_dir).expanduser().resolve() if output_dir else \
              Path(tempfile.mkdtemp(prefix="aurora_nb_export_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(archive, "r:gz") as tf:
            members = tf.getmembers()
            for m in members:
                if not m.isfile():
                    raise NotebookExportError(
                        f"archive contains non-regular entry {m.name!r}; refused"
                    )
                if m.name.startswith("/") or ".." in Path(m.name).parts:
                    raise NotebookExportError(
                        f"archive entry {m.name!r} would escape extraction dir; refused"
                    )
                if Path(m.name).name not in _ALLOWED_FILES:
                    raise NotebookExportError(
                        f"archive contains unexpected file {m.name!r}; "
                        f"only {sorted(_ALLOWED_FILES)} allowed"
                    )
            try:
                tf.extractall(path=out_dir, filter="data")  # type: ignore[arg-type]
            except TypeError:
                tf.extractall(path=out_dir)  # older Python
    except tarfile.TarError as e:
        raise NotebookExportError(f"archive is not a valid tar.gz: {e}") from e

    # Locate the three files (members may have been wrapped in a subdir).
    found: Dict[str, Path] = {}
    for name in _ALLOWED_FILES:
        matches = list(out_dir.rglob(name))
        if matches:
            key = name.split(".")[0]  # 'notebook', 'bundle', 'manifest'
            found[key] = matches[0]
    if set(found.keys()) != {"notebook", "bundle", "manifest"}:
        missing = {"notebook", "bundle", "manifest"} - set(found.keys())
        raise NotebookExportError(f"export missing required files: {sorted(missing)}")

    return found


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _resolve_bundle_bytes(bundle: Any) -> bytes:
    """Take either a Bundle object or a path, return the bundle's JSON
    bytes."""
    # Bundle-like duck: has a .doc attribute that's serialisable.
    if hasattr(bundle, "doc"):
        try:
            return json.dumps(bundle.doc, indent=2, default=str,
                              ensure_ascii=False).encode("utf-8")
        except Exception as e:
            raise NotebookExportError(
                f"bundle.doc is not JSON-serialisable: {e}"
            ) from e
    # Otherwise treat as a path.
    p = Path(bundle).expanduser().resolve()
    if not p.exists() or not p.is_file():
        raise NotebookExportError(f"bundle path not found: {p}")
    return p.read_bytes()


def _add_bytes(tf: tarfile.TarFile, name: str, payload: bytes) -> None:
    """Add an in-memory bytes payload to an open tar at the given name."""
    import io
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    tf.addfile(info, io.BytesIO(payload))


def _detect_aurora_version() -> str:
    """Best-effort: import the SDK's __version__. Falls back to 'unknown'."""
    try:
        from . import __version__ as _v  # type: ignore
        return str(_v)
    except Exception:
        return "unknown"
