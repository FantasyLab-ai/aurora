"""
Connector framework — base class + result types + caching.

Every connector implements two operations:

  * ``search(query)``  → list of ``ConnectorSearchResult`` previews (no full
                          download yet — just metadata to show the user)
  * ``fetch(item_id)`` → ``ConnectorResult`` with the actual CSV path written
                          to ``outputs/_data_cache/<connector_id>/...``

Caching: every fetched file is stored under
``outputs/_data_cache/<connector_id>/<item_id>__<sha256_short>.csv``.
On repeat fetch, if the file exists and is younger than ``cache_ttl_days``,
we skip the network call and return the cached path.

Provenance: every fetch writes a sidecar ``.provenance.json`` next to the
CSV with: source, query, item_id, fetched_at, response_hash. State_builder
can later attach this provenance to every analysis run that uses the file.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


# ----------------------------------------------------------------------
# Result dataclasses
# ----------------------------------------------------------------------

@dataclass
class ConnectorSearchResult:
    """A preview of a single dataset returned by a connector search."""
    item_id: str                      # connector-specific id used to fetch
    title: str                        # human-readable title
    description: str                  # one-line summary
    rows_estimate: Optional[int]      # estimated row count (None if unknown)
    cols_estimate: Optional[int]
    domain: str                       # connector's domain hint (finance|enviro|research|...)
    source: str                       # connector id (world_bank|usgs_earthquake|openml)
    extras: Dict[str, Any] = field(default_factory=dict)   # connector-specific metadata

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConnectorResult:
    """The result of a successful fetch — a CSV on disk + provenance."""
    item_id: str
    csv_path: str
    rows: int
    cols: int
    source: str
    cache_hit: bool                   # True if served from local cache
    fetched_at: str
    response_hash: str
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ----------------------------------------------------------------------
# Cache layout
# ----------------------------------------------------------------------

def _cache_root() -> Path:
    """Resolve the data-cache directory. Honors the same root-detection
    logic as the rest of Aurora (worktree-aware)."""
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "outputs" / "_data_cache"


def _cache_path_for(connector_id: str, item_id: str, suffix: str = ".csv") -> Path:
    """Where a fetched item lives on disk."""
    safe_item = item_id.replace("/", "_").replace("\\", "_")[:120]
    short_hash = hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:8]
    d = _cache_root() / connector_id
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe_item}__{short_hash}{suffix}"


def _provenance_path(csv_path: Path) -> Path:
    return csv_path.with_suffix(".provenance.json")


def _is_cache_fresh(csv_path: Path, ttl_days: int) -> bool:
    if not csv_path.exists():
        return False
    age_days = (time.time() - csv_path.stat().st_mtime) / 86400.0
    return age_days < ttl_days


# ----------------------------------------------------------------------
# Base class
# ----------------------------------------------------------------------

class BaseConnector(ABC):
    """ABC for public-data connectors."""

    # Subclass overrides
    id: str = "base"
    display_name: str = "Base connector"
    domain: str = "general"
    description: str = ""
    requires_api_key: bool = False
    api_key_env_var: Optional[str] = None
    homepage_url: Optional[str] = None
    cache_ttl_days: int = 7

    # ---- key handling ----
    def has_api_key(self) -> bool:
        if not self.requires_api_key:
            return True
        if not self.api_key_env_var:
            return False
        return bool(os.environ.get(self.api_key_env_var))

    def _api_key(self) -> Optional[str]:
        if self.api_key_env_var:
            return os.environ.get(self.api_key_env_var)
        return None

    # ---- abstract surface ----
    @abstractmethod
    def search(self, query: str, limit: int = 10) -> List[ConnectorSearchResult]:
        """Search for matching datasets. Returns previews, not data."""
        ...

    @abstractmethod
    def _do_fetch(self, item_id: str) -> "FetchPayload":
        """Subclass implementation — actually pulls the data and writes a CSV.
        Returns the path written + row/col count + a hash of the response.
        """
        ...

    # ---- concrete fetch with caching ----
    def fetch(self, item_id: str, force: bool = False) -> ConnectorResult:
        """Fetch with caching. Idempotent within ``cache_ttl_days``."""
        csv_path = _cache_path_for(self.id, item_id)
        if not force and _is_cache_fresh(csv_path, self.cache_ttl_days):
            # Cache hit. Read provenance to recover row/col counts.
            try:
                prov = json.loads(_provenance_path(csv_path).read_text(encoding="utf-8"))
                return ConnectorResult(
                    item_id=item_id, csv_path=str(csv_path),
                    rows=prov.get("rows", 0), cols=prov.get("cols", 0),
                    source=self.id, cache_hit=True,
                    fetched_at=prov.get("fetched_at", ""),
                    response_hash=prov.get("response_hash", ""),
                    extras=prov.get("extras", {}),
                )
            except Exception:
                pass  # cache provenance broken; re-fetch

        # Cache miss → live call.
        payload = self._do_fetch(item_id)
        # Write CSV
        csv_path.write_bytes(payload.csv_bytes)
        # Write provenance sidecar.
        prov = {
            "source": self.id,
            "item_id": item_id,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "response_hash": payload.response_hash,
            "rows": payload.rows,
            "cols": payload.cols,
            "extras": payload.extras or {},
            "homepage_url": self.homepage_url,
        }
        _provenance_path(csv_path).write_text(
            json.dumps(prov, indent=2), encoding="utf-8"
        )
        return ConnectorResult(
            item_id=item_id, csv_path=str(csv_path),
            rows=payload.rows, cols=payload.cols,
            source=self.id, cache_hit=False,
            fetched_at=prov["fetched_at"],
            response_hash=payload.response_hash,
            extras=payload.extras or {},
        )

    # ---- introspection metadata for the API ----
    def to_metadata(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "domain": self.domain,
            "description": self.description,
            "requires_api_key": self.requires_api_key,
            "api_key_env_var": self.api_key_env_var,
            "has_api_key": self.has_api_key(),
            "homepage_url": self.homepage_url,
            "cache_ttl_days": self.cache_ttl_days,
        }


@dataclass
class FetchPayload:
    """Internal: what _do_fetch returns to base.fetch()."""
    csv_bytes: bytes
    rows: int
    cols: int
    response_hash: str
    extras: Dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------
# Generic HTTP helper used by subclasses
# ----------------------------------------------------------------------

def http_get_json(url: str, params: Optional[Dict[str, Any]] = None,
                   headers: Optional[Dict[str, str]] = None,
                   timeout: int = 20) -> Any:
    """Minimal HTTP GET helper that raises on non-OK and returns parsed JSON.

    Subclasses use this; we keep it small to avoid bloating the connector
    surface with a custom HTTP layer.
    """
    r = requests.get(url, params=params, headers=headers or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def http_get_bytes(url: str, params: Optional[Dict[str, Any]] = None,
                    headers: Optional[Dict[str, str]] = None,
                    timeout: int = 30) -> bytes:
    r = requests.get(url, params=params, headers=headers or {}, timeout=timeout)
    r.raise_for_status()
    return r.content


def hash_bytes(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()
