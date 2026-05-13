"""
Connector smoke tests: registry shape + key-required behaviour.

We do NOT make live HTTP calls — those happen at user-fetch time. These
tests just verify the connector classes plug into the registry, expose the
right metadata, and refuse to operate when their required key is absent.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ============================================================
# Registry shape
# ============================================================

class TestRegistry:

    def test_registry_loads(self):
        from fantasyai.aurora.connectors import list_connectors, get_connector
        meta = list_connectors()
        ids = {m["id"] for m in meta}
        # All 6 connectors should be registered.
        assert "world_bank" in ids
        assert "usgs_earthquake" in ids
        assert "openml" in ids
        assert "pubmed" in ids
        assert "noaa_cdo" in ids
        assert "fred" in ids

    def test_get_connector_by_id(self):
        from fantasyai.aurora.connectors import get_connector
        c = get_connector("noaa_cdo")
        assert c is not None
        assert c.id == "noaa_cdo"
        assert c.requires_api_key is True

    def test_unknown_connector_returns_none(self):
        from fantasyai.aurora.connectors import get_connector
        assert get_connector("not_a_real_connector") is None


# ============================================================
# Key-required gating
# ============================================================

class TestKeyGating:

    def test_noaa_search_returns_empty_without_key(self, monkeypatch):
        monkeypatch.delenv("NOAA_CDO_API_KEY", raising=False)
        from fantasyai.aurora.connectors.noaa import NOAAConnector
        c = NOAAConnector()
        assert c.has_api_key() is False
        # search() should silently return [] when no key (avoids leaking errors to UI).
        assert c.search("New York") == []

    def test_noaa_fetch_raises_without_key(self, monkeypatch):
        monkeypatch.delenv("NOAA_CDO_API_KEY", raising=False)
        from fantasyai.aurora.connectors.noaa import NOAAConnector
        c = NOAAConnector()
        with pytest.raises(RuntimeError, match="NOAA_CDO_API_KEY"):
            c._do_fetch("GHCND:USW00094728")

    def test_fred_search_returns_empty_without_key(self, monkeypatch):
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        from fantasyai.aurora.connectors.fred import FREDConnector
        c = FREDConnector()
        assert c.has_api_key() is False
        assert c.search("GDP") == []

    def test_fred_fetch_raises_without_key(self, monkeypatch):
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        from fantasyai.aurora.connectors.fred import FREDConnector
        c = FREDConnector()
        with pytest.raises(RuntimeError, match="FRED_API_KEY"):
            c._do_fetch("GDP")

    def test_pubmed_works_without_key(self, monkeypatch):
        """PubMed marks key as optional — should report has_api_key=True (no key
        required). search() will still attempt the live call so we DON'T invoke it
        here; just verify the gating logic."""
        monkeypatch.delenv("NCBI_API_KEY", raising=False)
        from fantasyai.aurora.connectors.pubmed import PubMedConnector
        c = PubMedConnector()
        assert c.requires_api_key is False
        assert c.has_api_key() is True   # because requires_api_key=False


# ============================================================
# Metadata shape — every connector exposes the same fields
# ============================================================

class TestMetadataShape:

    def test_metadata_has_all_required_fields(self):
        from fantasyai.aurora.connectors import list_connectors
        for m in list_connectors():
            for k in ("id", "display_name", "domain", "description",
                      "requires_api_key", "has_api_key", "homepage_url"):
                assert k in m, f"connector {m.get('id')} missing field {k}"

    def test_key_required_connectors_have_homepage(self):
        from fantasyai.aurora.connectors import list_connectors
        for m in list_connectors():
            if m.get("requires_api_key"):
                assert m.get("api_key_env_var"), \
                    f"{m['id']}: requires key but no api_key_env_var declared"
                assert m.get("homepage_url"), \
                    f"{m['id']}: requires key but no homepage_url for sign-up"
