"""
Pack registry — local record of which packs the user has installed.

Persisted to ``~/.aurora/knowledge_bank/packs/_installed.json``. The
registry is the source of truth for "is finance pack already on this
machine?" — we consult it before downloading anything.

The format is intentionally simple JSON so users can inspect / edit it
without an Aurora process running:

    {
      "schema_version": "1.0.0",
      "installed": [
        {
          "id": "finance",
          "version": 1,
          "installed_at": "2026-06-01T12:34:56Z",
          "sha256": "a3f5...",
          "entry_count": 8420,
          "pack_dir": "/Users/.../knowledge_bank/packs/finance-v1"
        },
        ...
      ]
    }

If two Aurora processes write to the registry concurrently the last
writer wins. That's fine — Aurora isn't a multi-user server.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


REGISTRY_SCHEMA_VERSION = "1.0.0"


def default_registry_path() -> Path:
    """Canonical registry location in the user's home dir.

    Mirrors the existing ``.aurora/knowledge_bank/`` layout used by the
    rest of the KB code so everything user-facing is in one place.
    """
    return Path.home() / ".aurora" / "knowledge_bank" / "packs" / "_installed.json"


@dataclass(frozen=True)
class InstalledPack:
    """One row in the installed-packs registry."""
    id: str
    version: int
    installed_at: str  # ISO 8601 UTC
    sha256: str
    entry_count: int
    pack_dir: str

    @classmethod
    def from_dict(cls, d: Dict) -> "InstalledPack":
        return cls(
            id=str(d["id"]),
            version=int(d["version"]),
            installed_at=str(d.get("installed_at", "")),
            sha256=str(d.get("sha256", "")),
            entry_count=int(d.get("entry_count", 0)),
            pack_dir=str(d.get("pack_dir", "")),
        )

    def to_dict(self) -> Dict:
        return asdict(self)


class PackRegistry:
    """In-memory + on-disk view of installed packs.

    Use ``PackRegistry.load()`` to read, then call ``mark_installed`` or
    ``remove`` to update + ``save()`` to persist. Or use the context
    manager: ``with PackRegistry.open() as reg:`` (auto-saves on exit).
    """

    def __init__(self, path: Path, installed: Optional[List[InstalledPack]] = None):
        self.path = path
        self._installed: List[InstalledPack] = list(installed or [])

    # ---- Construction --------------------------------------------------

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "PackRegistry":
        """Read the registry from disk. If the file doesn't exist or is
        unreadable, return an empty registry (so first-run users don't
        see an error)."""
        p = Path(path) if path is not None else default_registry_path()
        if not p.exists():
            return cls(p, [])
        try:
            with open(p, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            # Treat corrupt registry as empty rather than crashing the
            # whole KB. Worst case: the user re-downloads a pack.
            return cls(p, [])
        entries = []
        for row in (raw.get("installed") or []):
            try:
                entries.append(InstalledPack.from_dict(row))
            except (KeyError, ValueError, TypeError):
                continue  # skip malformed row
        return cls(p, entries)

    def save(self) -> None:
        """Atomically persist the registry to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "installed": [p.to_dict() for p in self._installed],
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        tmp.replace(self.path)

    # ---- Query ---------------------------------------------------------

    def is_installed(self, pack_id: str, *, version: Optional[int] = None) -> bool:
        """True if the pack is installed. When ``version`` is set, only
        returns True for that exact version (allows the auto-detector to
        force a re-install on a new pack version)."""
        for p in self._installed:
            if p.id != pack_id:
                continue
            if version is not None and p.version != version:
                continue
            return True
        return False

    def get(self, pack_id: str) -> Optional[InstalledPack]:
        for p in self._installed:
            if p.id == pack_id:
                return p
        return None

    def installed_ids(self) -> List[str]:
        return [p.id for p in self._installed]

    def all(self) -> List[InstalledPack]:
        return list(self._installed)

    # ---- Mutation ------------------------------------------------------

    def mark_installed(self, pack: InstalledPack) -> None:
        """Add or replace the row for ``pack.id``. Always overwrites
        previous version for the same id (only one version installed at
        a time)."""
        self._installed = [p for p in self._installed if p.id != pack.id]
        self._installed.append(pack)

    def remove(self, pack_id: str) -> bool:
        """Drop the row for ``pack_id``. Returns True if a row existed."""
        before = len(self._installed)
        self._installed = [p for p in self._installed if p.id != pack_id]
        return len(self._installed) < before


def make_installed_pack(
    pack_id: str,
    version: int,
    sha256: str,
    entry_count: int,
    pack_dir: Path,
) -> InstalledPack:
    """Helper for the installer — produces a fresh ``InstalledPack`` row
    stamped with the current UTC time."""
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return InstalledPack(
        id=pack_id,
        version=version,
        installed_at=now,
        sha256=sha256,
        entry_count=entry_count,
        pack_dir=str(pack_dir),
    )
