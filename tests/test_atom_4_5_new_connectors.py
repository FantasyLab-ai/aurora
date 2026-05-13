"""
Atom 4.5: BLS + sports connectors registered, importable, and the
scheduler can dispatch to them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ============================================================
# Connector module imports
# ============================================================

class TestImports:

    def test_bls_module_loads(self):
        from fantasyai.aurora.connectors.bls import BLSConnector
        c = BLSConnector()
        assert c.id == "bls"
        assert c.domain == "economics"
        assert c.requires_api_key is False

    def test_sports_module_loads(self):
        from fantasyai.aurora.connectors.sports import SportsConnector
        c = SportsConnector()
        assert c.id == "sports"
        assert c.domain == "sports"
        assert c.requires_api_key is False


# ============================================================
# Registry includes them
# ============================================================

class TestRegistry:

    def test_registry_lists_all_atom_45_sources(self):
        from fantasyai.aurora.connectors.registry import list_connectors
        ids = {c["id"] for c in list_connectors()}
        # Existing — connector class ids (slightly different from scheduler
        # source ids, e.g. noaa_cdo not noaa).
        assert {"world_bank", "usgs_earthquake", "openml",
                "noaa_cdo", "fred"} <= ids
        # Atom 4.5 additions
        assert "bls" in ids
        assert "sports" in ids


# ============================================================
# Search() — both connectors return curated results without HTTP
# ============================================================

class TestSearch:

    def test_bls_search_returns_curated_series(self):
        from fantasyai.aurora.connectors.bls import BLSConnector
        rs = BLSConnector().search("unemployment", limit=5)
        assert any("LNS14000000" in r.item_id for r in rs)

    def test_bls_search_empty_query_returns_curated(self):
        from fantasyai.aurora.connectors.bls import BLSConnector
        rs = BLSConnector().search("", limit=10)
        assert len(rs) >= 3

    def test_sports_search_returns_sample(self):
        from fantasyai.aurora.connectors.sports import SportsConnector
        rs = SportsConnector().search("", limit=5)
        assert any(r.item_id == "sample" for r in rs)


# ============================================================
# Sports bundled fallback — no network
# ============================================================

class TestSportsFallback:

    def test_sample_query_returns_bundled_csv_payload(self):
        from fantasyai.aurora.connectors.sports import SportsConnector
        c = SportsConnector()
        payload = c._do_fetch("sample")
        # Bundled sample is non-empty + parseable.
        assert payload.csv_bytes
        first_line = payload.csv_bytes.split(b"\n")[0].decode()
        for col in ("player", "team", "opponent", "minutes", "pts"):
            assert col in first_line

    def test_unknown_id_falls_back_to_sample(self):
        from fantasyai.aurora.connectors.sports import SportsConnector
        c = SportsConnector()
        payload = c._do_fetch("not_a_real_id_404")
        assert payload.csv_bytes


# ============================================================
# Scheduler dispatch routes to BLS / sports
# ============================================================

class TestSchedulerDispatch:

    def test_bls_dispatch(self, tmp_path, monkeypatch):
        from fantasyai.aurora.scheduler import overnight as ov
        # Stub the BLSConnector to avoid live HTTP.
        csv_path = tmp_path / "bls_fake.csv"
        csv_path.write_text("year,period,value,series_id\n2024,M01,3.7,LNS14000000\n",
                             encoding="utf-8")
        # Return a simple object with .csv_path attribute (matches the
        # ConnectorResult shape that BaseConnector.fetch() produces).
        class FakeResult:
            def __init__(self, p): self.csv_path = str(p)
        class FakeBLS:
            id = "bls"
            def fetch(self, q):
                return FakeResult(csv_path)
        monkeypatch.setattr(ov, "_connector_for",
                             lambda sid: FakeBLS if sid == "bls" else None)
        captured = {}
        def fake_trigger(**k):
            captured.update(k)
            return {"ok": True, "run_dir": str(tmp_path / "run")}
        out = ov.fetch_and_run_one_source(
            ov.SourceConfig(id="bls", query="LNS14000000"),
            trigger_run_fn=fake_trigger,
        )
        assert out["ok"] is True
        assert captured.get("dataset") == str(csv_path)
        assert captured.get("also_llm") is False     # overnight-determinism

    def test_sports_dispatch(self, tmp_path, monkeypatch):
        from fantasyai.aurora.scheduler import overnight as ov
        csv_path = tmp_path / "sports_fake.csv"
        csv_path.write_text("player,team,pts\nA,DEN,42\n", encoding="utf-8")
        class FakeSports:
            id = "sports"
            def fetch(self, q):
                # Return raw path (the other supported shape).
                return csv_path
        monkeypatch.setattr(ov, "_connector_for",
                             lambda sid: FakeSports if sid == "sports" else None)
        captured = {}
        def fake_trigger(**k):
            captured.update(k)
            return {"ok": True, "run_dir": str(tmp_path / "run2")}
        out = ov.fetch_and_run_one_source(
            ov.SourceConfig(id="sports", query="sample"),
            trigger_run_fn=fake_trigger,
        )
        assert out["ok"] is True
        assert captured.get("dataset") == str(csv_path)


# ============================================================
# Trusted-source whitelist includes the new sources
# ============================================================

class TestTrustedSourceList:

    def test_bls_is_trusted(self):
        from fantasyai.aurora.scheduler.changelog import TRUSTED_SOURCES
        assert "bls" in TRUSTED_SOURCES

    def test_sports_is_trusted(self):
        from fantasyai.aurora.scheduler.changelog import TRUSTED_SOURCES
        assert "sports" in TRUSTED_SOURCES

    def test_user_upload_NEVER_trusted(self):
        from fantasyai.aurora.scheduler.changelog import TRUSTED_SOURCES
        # The launch invariant: user uploads must NEVER contribute to priors.
        assert "user_upload" not in TRUSTED_SOURCES
        assert "user" not in TRUSTED_SOURCES
