"""
Public-data connectors — pull real-world datasets directly into Aurora.

Aurora's positioning depends on this: instead of "I have to find a CSV first,"
the user types or browses → Aurora pulls the data → full pipeline runs.
This is the moat against generic notebook+LLM tools that have no awareness
of the world's public data infrastructure.

Local-first invariants
----------------------

* All fetches are cached aggressively in ``outputs/_data_cache/``
* Each cached file has provenance (source, query, fetched_at, hash)
* Aurora **never** requires internet to operate on already-cached data
* Connectors that need API keys gracefully report missing keys; no crash

Public API
----------

    from fantasyai.aurora.connectors import (
        list_connectors,         # → all registered connectors with metadata
        get_connector,           # → connector instance by id
        BaseConnector,           # → ABC for new connectors
        ConnectorResult,         # → fetched-data wrapper
    )

V1 connectors (no API keys required):
    * ``world_bank``   — World Bank Open Data Indicators API
    * ``usgs_earthquake`` — USGS Earthquake Catalog
    * ``openml``       — OpenML datasets
"""
from .base import BaseConnector, ConnectorResult, ConnectorSearchResult
from .registry import list_connectors, get_connector

__all__ = [
    "BaseConnector", "ConnectorResult", "ConnectorSearchResult",
    "list_connectors", "get_connector",
]
