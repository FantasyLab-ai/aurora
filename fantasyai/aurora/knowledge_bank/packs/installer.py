"""
Pack installer — given a downloaded, sha-verified pack tarball, extract
it to disk and register it as installed.

What "install" means concretely:

  1. Extract the tarball into ``~/.aurora/knowledge_bank/packs/{id}-v{n}/``
  2. Validate the contents (pack_meta.json + entries.jsonl + embeddings.npy)
  3. Validate the embedder + dimension match what the manifest declared
  4. Mark the install in the registry (``_installed.json``)

What "install" does NOT do (intentionally):

  - It does not merge entries into the user's primary SQLite KB. The
    KB code does that on demand when retrieval runs, by scanning the
    packs directory and including matching packs. This keeps install
    fast (no embedding rebuild) and lets the user uninstall a pack
    just by deleting its directory.
  - It does not delete previous versions of the same pack. Cleanup is
    explicit so users can roll back to a known-good version.

Threat model: the .tar.gz file is sha-verified before this function
runs (see downloader.py). We still extract defensively (no absolute
paths, no parent-directory escapes) because layered defenses are
cheap and one day someone will ship a malicious manifest.
"""
from __future__ import annotations

import json
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .manifest import Pack
from .registry import PackRegistry, InstalledPack, make_installed_pack


# Whitelisted files inside a pack tarball. Anything else triggers a refusal.
_PACK_ALLOWED_FILES = frozenset({
    "pack_meta.json",
    "entries.jsonl",
    "embeddings.npy",
})


class InstallError(Exception):
    """Raised when a pack can't be installed. Always carries a clear,
    actionable message."""


@dataclass(frozen=True)
class InstallResult:
    """Outcome of a successful install."""
    pack_id: str
    version: int
    pack_dir: Path
    entry_count: int


def default_packs_root() -> Path:
    """Where installed packs live on disk. Sibling of the main KB DB."""
    return Path.home() / ".aurora" / "knowledge_bank" / "packs"


def install_pack(
    pack: Pack,
    archive_path: Path,
    *,
    packs_root: Optional[Path] = None,
    registry: Optional[PackRegistry] = None,
    overwrite: bool = True,
) -> InstallResult:
    """Install a pre-downloaded pack archive.

    Args:
        pack:         Pack manifest entry (used to validate the archive's
                      claimed identity matches).
        archive_path: Local path to the .tar.gz produced by ``download_pack``.
        packs_root:   Where to install. Defaults to ``~/.aurora/knowledge_bank/packs/``.
        registry:     Registry to update. Defaults to the canonical one.
        overwrite:    If True (default), an existing pack dir is removed
                      and replaced. Set False to refuse to clobber.

    Returns:
        InstallResult with the installed path + verified entry count.

    Raises:
        InstallError: archive is malformed, embedder mismatch, manifest /
                      pack_meta disagreement, or any I/O failure.
    """
    packs_root = packs_root or default_packs_root()
    packs_root.mkdir(parents=True, exist_ok=True)

    dest = packs_root / f"{pack.id}-v{pack.version}"
    if dest.exists():
        if not overwrite:
            raise InstallError(
                f"pack {pack.id}-v{pack.version} already installed at {dest}; "
                f"pass overwrite=True to replace"
            )
        # Wipe + reinstall is safer than merging — guarantees no orphan files.
        shutil.rmtree(dest, ignore_errors=True)

    # Extract into a staging dir, validate, then atomically rename to
    # the final destination. Failures leave nothing behind.
    staging = packs_root / f".{pack.id}-v{pack.version}.staging"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        _safe_extract(archive_path, staging)
        _validate_pack_dir(staging, pack)
        # Move to final location.
        # On Windows, replace() is atomic for files but a directory rename
        # is best-effort. We accept that here — at worst the user sees a
        # partially-renamed pack which we'd treat as not-installed on the
        # next run.
        staging.rename(dest)
    except Exception:
        # Cleanup staging on any failure.
        shutil.rmtree(staging, ignore_errors=True)
        raise

    # Update registry.
    reg = registry or PackRegistry.load()
    entry_count = _read_entry_count(dest)
    reg.mark_installed(make_installed_pack(
        pack_id=pack.id,
        version=pack.version,
        sha256=pack.sha256,
        entry_count=entry_count,
        pack_dir=dest,
    ))
    reg.save()

    return InstallResult(
        pack_id=pack.id,
        version=pack.version,
        pack_dir=dest,
        entry_count=entry_count,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _safe_extract(archive: Path, dest: Path) -> None:
    """Extract ``archive`` into ``dest``, refusing any tar entry that
    tries to escape via absolute paths or ``..`` segments. Whitelists
    file names — only ``pack_meta.json``, ``entries.jsonl``, and
    ``embeddings.npy`` are accepted."""
    if not archive.exists():
        raise InstallError(f"archive not found: {archive}")
    try:
        with tarfile.open(archive, "r:gz") as tf:
            members = tf.getmembers()
            for m in members:
                if m.isdir():
                    continue
                if not m.isfile():
                    raise InstallError(
                        f"archive contains non-regular entry {m.name!r}; refused"
                    )
                # Reject absolute paths and parent-traversal.
                if m.name.startswith("/") or ".." in Path(m.name).parts:
                    raise InstallError(
                        f"archive entry {m.name!r} would escape extraction dir; refused"
                    )
                base = Path(m.name).name
                if base not in _PACK_ALLOWED_FILES:
                    raise InstallError(
                        f"archive contains unexpected file {m.name!r}; "
                        f"only {sorted(_PACK_ALLOWED_FILES)} allowed"
                    )
            # All members validated — extract.
            # Python 3.12+ adds `filter="data"` for defense in depth.
            try:
                tf.extractall(path=dest, filter="data")  # type: ignore[arg-type]
            except TypeError:
                # Older Python: extractall doesn't support filter.
                tf.extractall(path=dest)
    except tarfile.TarError as e:
        raise InstallError(f"archive is not a valid tar.gz: {e}") from e


def _validate_pack_dir(pack_dir: Path, manifest_pack: Pack) -> None:
    """Confirm an extracted pack directory contains the expected files
    and that pack_meta.json agrees with the manifest entry."""
    # All three files must be present and non-empty.
    for fname in _PACK_ALLOWED_FILES:
        # Files may have been extracted into a subdirectory if the tarball
        # used one; flatten by globbing.
        matches = list(pack_dir.rglob(fname))
        if not matches:
            raise InstallError(
                f"pack {manifest_pack.id} missing required file {fname!r}"
            )
        if matches[0].stat().st_size == 0:
            raise InstallError(
                f"pack {manifest_pack.id} has empty file {fname!r}"
            )

    meta_path = next(iter(pack_dir.rglob("pack_meta.json")))
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise InstallError(f"pack_meta.json could not be read: {e}") from e

    # The pack_meta should agree with the manifest on identity. If the
    # uploader shipped a 'finance' pack mislabeled as 'industrial' in
    # the manifest, we'd catch it here.
    if str(meta.get("id")) != manifest_pack.id:
        raise InstallError(
            f"pack_meta.id {meta.get('id')!r} disagrees with manifest id "
            f"{manifest_pack.id!r}"
        )
    if int(meta.get("version", -1)) != manifest_pack.version:
        raise InstallError(
            f"pack_meta.version {meta.get('version')} disagrees with manifest "
            f"version {manifest_pack.version}"
        )
    # Embedder + dim must match. A user with a different embedder loaded
    # would get garbage retrieval if we accepted mismatched vectors.
    if str(meta.get("embedder")) != manifest_pack.embedder:
        raise InstallError(
            f"pack embedder {meta.get('embedder')!r} != manifest "
            f"embedder {manifest_pack.embedder!r}"
        )
    if int(meta.get("embedding_dim", -1)) != manifest_pack.embedding_dim:
        raise InstallError(
            f"pack embedding_dim {meta.get('embedding_dim')} != manifest "
            f"embedding_dim {manifest_pack.embedding_dim}"
        )


def _read_entry_count(pack_dir: Path) -> int:
    """Count lines in entries.jsonl. Returns 0 on any error so the
    install still proceeds — count is a registry stat, not a precondition."""
    try:
        entries = next(iter(pack_dir.rglob("entries.jsonl")))
        with open(entries, "rb") as f:
            return sum(1 for _ in f)
    except (StopIteration, OSError):
        return 0
