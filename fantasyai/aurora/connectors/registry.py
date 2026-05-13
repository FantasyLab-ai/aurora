"""
Connector registry — central discovery for all loaded connectors.

Adding a new connector: import it here, append to ``_REGISTRY``. That's the
entire integration; the API endpoints + UI introspect this list.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .base import BaseConnector
from .world_bank import WorldBankConnector
from .usgs_earthquake import USGSEarthquakeConnector
from .openml import OpenMLConnector
from .noaa import NOAAConnector
from .fred import FREDConnector
from .pubmed import PubMedConnector
from .bls import BLSConnector
from .sports import SportsConnector


# Registry order matters for UI ordering — keyless first, then key-required.
_REGISTRY: List[BaseConnector] = [
    WorldBankConnector(),       # keyless
    USGSEarthquakeConnector(),  # keyless
    OpenMLConnector(),          # keyless
    PubMedConnector(),          # keyless (key-optional)
    BLSConnector(),             # Atom 4.5 — keyless v1, key-optional v2
    SportsConnector(),          # Atom 4.5 — keyless, has bundled fallback
    NOAAConnector(),            # NOAA_CDO_API_KEY required
    FREDConnector(),            # FRED_API_KEY required
]
_BY_ID: Dict[str, BaseConnector] = {c.id: c for c in _REGISTRY}


def list_connectors() -> List[Dict]:
    """Return metadata for every loaded connector."""
    return [c.to_metadata() for c in _REGISTRY]


def get_connector(connector_id: str) -> Optional[BaseConnector]:
    return _BY_ID.get(connector_id)
