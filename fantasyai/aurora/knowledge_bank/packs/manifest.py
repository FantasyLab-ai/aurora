"""
Pack manifest — the index of available knowledge packs.

The manifest is a JSON file committed to the repo. Every Aurora install
ships with the canonical list of packs that exist, where to download
them, and what they cover. Clients consult the manifest at runtime; we
never embed pack URLs in code.

Schema (version 1.0.0):

    {
      "schema_version": "1.0.0",
      "manifest_revision": "2026-06-01",
      "packs": [
        {
          "id": "finance",
          "name": "Financial markets & quantitative finance",
          "description": "Cited entries covering volatility regimes, ...",
          "version": 1,
          "size_mb": 145,
          "entry_count": 8420,
          "embedder": "all-MiniLM-L6-v2",
          "embedding_dim": 384,
          "covers_methods": ["granger", "hmm_baum_welch", ...],
          "covers_domains": ["finance"],
          "url_primary": "https://kb.aurora.fantasylab.ai/packs/finance-v1.tar.gz",
          "url_mirror": "https://huggingface.co/datasets/fantasylab-ai/aurora-kb/resolve/main/finance-v1.tar.gz",
          "sha256": "a3f5...",
          "license": "CC-BY-4.0 (collection); per-entry licenses preserved"
        },
        ...
      ]
    }

Stability promise: schema_version follows semver. Minor bumps are
backward-compatible; major bumps require client updates.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


# Current schema version. Increment minor when adding optional fields,
# major when breaking compatibility.
SCHEMA_VERSION = "1.0.0"

# Canonical manifest path inside the repo. Distributed via git, not via
# any network call.
DEFAULT_MANIFEST_PATH = (
    Path(__file__).resolve().parents[4]
    / "fantasyai" / "aurora" / "knowledge_bank" / "packs"
    / "manifest.json"
)


class ManifestError(Exception):
    """Raised when a manifest cannot be loaded or doesn't conform to
    the expected shape. Always carries a human-readable message and
    never wraps a TypeError unhelpfully."""


class PackSchemaError(ManifestError):
    """Raised when an individual pack entry is malformed."""


# ---------------------------------------------------------------------------
# Pack — one downloadable knowledge pack
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Pack:
    """A single downloadable knowledge pack.

    Frozen because manifest entries should never mutate after load; if
    you want to change something, edit ``manifest.json`` and reload.

    Attributes:
        id              Stable identifier (snake_case, no spaces). Used
                        as directory name on disk.
        name            Human-readable name for UI.
        description     One-paragraph summary for UI.
        version         Integer version. Bumped when content changes
                        materially; the URL embeds it.
        size_mb         Approximate size of the download in megabytes.
                        Surfaced to the user before they confirm.
        entry_count     Number of knowledge entries inside.
        embedder        Name of the sentence-transformer model used to
                        produce the embeddings shipped in the pack.
                        Clients reject packs whose embedder doesn't
                        match their active embedder.
        embedding_dim   Vector dimension. Used as a quick mismatch check
                        before downloading.
        covers_methods  Aurora analytical methods this pack tends to
                        ground (e.g., "granger", "hmm_baum_welch").
                        Used by the auto-detector to pick which pack
                        to download for a given finding set.
        covers_domains  Domain hints (e.g., "finance", "industrial").
                        Used by the auto-detector based on Aurora's
                        domain classifier.
        url_primary     Cloudflare R2 URL (preferred, free-egress).
        url_mirror      HuggingFace mirror URL (failover).
        sha256          Hex digest of the .tar.gz file. Verified after
                        download; mismatch aborts install.
        license         License of the collection. Individual entries
                        carry their own per-entry license.
    """

    id: str
    name: str
    description: str
    version: int
    size_mb: float
    entry_count: int
    embedder: str
    embedding_dim: int
    covers_methods: tuple = field(default_factory=tuple)
    covers_domains: tuple = field(default_factory=tuple)
    url_primary: str = ""
    url_mirror: str = ""
    sha256: str = ""
    license: str = "CC-BY-4.0"

    # Optional human-facing fields. Empty by default.
    maintainer: str = ""
    homepage: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Pack":
        """Construct from a manifest JSON entry. Raises ``PackSchemaError``
        with a clear message on malformed input."""
        required = (
            "id", "name", "description", "version", "size_mb",
            "entry_count", "embedder", "embedding_dim", "sha256",
        )
        missing = [k for k in required if k not in d]
        if missing:
            raise PackSchemaError(
                f"pack entry missing required fields: {missing}"
            )
        # Coerce / validate types.
        try:
            return cls(
                id=str(d["id"]),
                name=str(d["name"]),
                description=str(d["description"]),
                version=int(d["version"]),
                size_mb=float(d["size_mb"]),
                entry_count=int(d["entry_count"]),
                embedder=str(d["embedder"]),
                embedding_dim=int(d["embedding_dim"]),
                covers_methods=tuple(d.get("covers_methods") or ()),
                covers_domains=tuple(d.get("covers_domains") or ()),
                url_primary=str(d.get("url_primary", "")),
                url_mirror=str(d.get("url_mirror", "")),
                sha256=str(d["sha256"]),
                license=str(d.get("license", "CC-BY-4.0")),
                maintainer=str(d.get("maintainer", "")),
                homepage=str(d.get("homepage", "")),
            )
        except (TypeError, ValueError) as e:
            raise PackSchemaError(
                f"pack entry {d.get('id', '?')!r} has bad type: {e}"
            ) from e

    def to_dict(self) -> Dict[str, Any]:
        """Serialise back to a manifest JSON entry."""
        out = asdict(self)
        # Tuples → lists for JSON.
        out["covers_methods"] = list(self.covers_methods)
        out["covers_domains"] = list(self.covers_domains)
        return out

    @property
    def archive_name(self) -> str:
        """Standard archive filename: ``{id}-v{version}.tar.gz``."""
        return f"{self.id}-v{self.version}.tar.gz"


# ---------------------------------------------------------------------------
# Manifest — the index containing all packs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Manifest:
    """Top-level manifest. Loaded once at startup, queried by ID or by
    domain when Aurora decides what to download."""

    schema_version: str
    manifest_revision: str
    packs: tuple  # tuple[Pack, ...]

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Manifest":
        if not isinstance(d, dict):
            raise ManifestError(
                f"manifest must be a JSON object, got {type(d).__name__}"
            )
        sv = d.get("schema_version")
        if not isinstance(sv, str) or not sv.strip():
            raise ManifestError("manifest.schema_version must be a non-empty string")
        # Only accept 1.x.x for now. We'll add migration on a major bump.
        if not sv.startswith("1."):
            raise ManifestError(
                f"manifest.schema_version {sv!r} is not supported by this "
                f"Aurora version (expected 1.x.x). Update Aurora."
            )
        rev = d.get("manifest_revision", "")
        if not isinstance(rev, str):
            raise ManifestError("manifest.manifest_revision must be a string")
        raw_packs = d.get("packs", [])
        if not isinstance(raw_packs, list):
            raise ManifestError("manifest.packs must be a JSON array")
        packs = []
        seen = set()
        for entry in raw_packs:
            if not isinstance(entry, dict):
                raise ManifestError("each pack entry must be a JSON object")
            p = Pack.from_dict(entry)
            if p.id in seen:
                raise ManifestError(f"duplicate pack id {p.id!r}")
            seen.add(p.id)
            packs.append(p)
        return cls(
            schema_version=sv,
            manifest_revision=rev,
            packs=tuple(packs),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest_revision": self.manifest_revision,
            "packs": [p.to_dict() for p in self.packs],
        }

    # ---- Query helpers --------------------------------------------------

    def get(self, pack_id: str) -> Optional[Pack]:
        """Find a pack by id. Returns None if not present."""
        for p in self.packs:
            if p.id == pack_id:
                return p
        return None

    def for_domain(self, domain: str) -> List[Pack]:
        """Find all packs that declare coverage of a given domain.
        Empty list if none. Order is the manifest's declaration order
        (deterministic for tests)."""
        if not domain:
            return []
        d = domain.lower()
        return [p for p in self.packs if d in (x.lower() for x in p.covers_domains)]

    def for_method(self, method: str) -> List[Pack]:
        """Find all packs that declare coverage of a given Aurora method."""
        if not method:
            return []
        m = method.lower()
        return [p for p in self.packs if m in (x.lower() for x in p.covers_methods)]

    def ids(self) -> List[str]:
        """Return all pack ids in manifest order."""
        return [p.id for p in self.packs]


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_manifest(path: Optional[Path] = None) -> Manifest:
    """Load the manifest from disk. Defaults to the canonical in-repo
    path (``DEFAULT_MANIFEST_PATH``)."""
    p = Path(path) if path is not None else DEFAULT_MANIFEST_PATH
    if not p.exists():
        raise ManifestError(f"manifest not found at {p}")
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise ManifestError(f"manifest at {p} is not valid JSON: {e}") from e
    return Manifest.from_dict(raw)


def load_manifest_from_str(s: str) -> Manifest:
    """Load a manifest from an in-memory JSON string. Convenient for
    tests and CLI tools that have the content in hand."""
    try:
        raw = json.loads(s)
    except json.JSONDecodeError as e:
        raise ManifestError(f"manifest text is not valid JSON: {e}") from e
    return Manifest.from_dict(raw)
