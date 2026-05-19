"""
Aurora Knowledge Pack distribution layer.

A *pack* is a versioned bundle of cited knowledge entries shipped
outside the git repo (because committing 100MB+ to git is hostile).
Packs are organised by domain (finance, industrial, climate,
biomedical, ...) so users only download what their data actually
needs.

Architecture:

  * ``manifest.py``  — Pack / Manifest dataclasses, JSON load/validate
  * ``downloader.py`` — HTTP fetch + sha256 verify + primary/mirror failover
  * ``installer.py``  — Extract pack, ingest entries into local KB, atomic
  * ``registry.py``   — Track which packs are installed locally + when

Distribution model (intentionally redundant for reliability):

  * Primary:  Cloudflare R2 at  kb.aurora.fantasylab.ai/packs/{id}-v{n}.tar.gz
  * Mirror:   HuggingFace at    huggingface.co/datasets/fantasylab-ai/aurora-kb

Both serve the same byte-identical tarball; clients try primary first,
fall back to mirror on failure. sha256 in the manifest catches any
tampering.

Pack file format (a tarball):

    finance-v1.tar.gz
      ├── pack_meta.json    — id, version, entry count, embedder info
      ├── entries.jsonl     — one KnowledgeEntry per line (JSON)
      └── embeddings.npy    — pre-computed vectors aligned to entries

Why not parquet: gzipped JSONL is universally readable (every language
has gzip+json), streamable, easy to inspect by hand, and the size
difference vs parquet is negligible at <500MB pack sizes. We can
migrate to parquet via a schema_version bump later if needed.

Why a manifest at all: the manifest is *committed to the repo*. The
client always knows what packs exist + where to get them, even if our
hosting moves. To add a new pack we ship a code change (PR-reviewable),
not a server-side flag flip.
"""
from __future__ import annotations

from .manifest import (
    Pack,
    Manifest,
    ManifestError,
    PackSchemaError,
    SCHEMA_VERSION,
    load_manifest,
    load_manifest_from_str,
    DEFAULT_MANIFEST_PATH,
)
from .registry import (
    InstalledPack,
    PackRegistry,
    default_registry_path,
)
from .downloader import (
    DownloadError,
    download_pack,
)
from .installer import (
    InstallError,
    install_pack,
)
from .autodetect import (
    detect_domains_from_state,
    pick_packs_for_domains,
    maybe_autoinstall,
    is_autodownload_enabled,
)

__all__ = [
    # Schema
    "Pack",
    "Manifest",
    "ManifestError",
    "PackSchemaError",
    "SCHEMA_VERSION",
    "load_manifest",
    "load_manifest_from_str",
    "DEFAULT_MANIFEST_PATH",
    # Registry
    "InstalledPack",
    "PackRegistry",
    "default_registry_path",
    # Download / install
    "DownloadError",
    "download_pack",
    "InstallError",
    "install_pack",
    # Auto-detection
    "detect_domains_from_state",
    "pick_packs_for_domains",
    "maybe_autoinstall",
    "is_autodownload_enabled",
]
