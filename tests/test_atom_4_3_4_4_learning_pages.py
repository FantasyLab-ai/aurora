"""
Atom 4.3 + 4.4: /learning admin review + public /learning page +
RSS/JSON feeds.

Hermetic: monkeypatches the scheduler-module paths to a tmp directory.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Redirect priors + changelog roots to tmp_path.
    from fantasyai.aurora.scheduler import changelog as cl
    priors_root = tmp_path / "priors"
    log_root = tmp_path / "log"
    monkeypatch.setattr(cl, "DEFAULT_PRIORS_ROOT", priors_root)
    monkeypatch.setattr(cl, "DEFAULT_CHANGELOG_ROOT", log_root)
    # Set admin token.
    monkeypatch.setenv("AURORA_ADMIN_TOKEN", "test-token-123")
    import studio_api
    monkeypatch.setattr(studio_api, "DEFAULT_OUTPUTS", tmp_path / "outputs")
    studio_api.app.config["TESTING"] = True
    with studio_api.app.test_client() as c:
        c._tmp_priors = priors_root
        c._tmp_log = log_root
        yield c


def _make_draft(log_root: Path, date: str, n_events: int = 3) -> Path:
    draft = log_root / "draft"
    draft.mkdir(parents=True, exist_ok=True)
    p = draft / f"{date}.json"
    p.write_text(json.dumps({
        "date": date,
        "review_required": True,
        "published": False,
        "events": [
            {"kind": ["strengthened", "candidate", "promoted"][i % 3],
             "prior_id": f"law_{i}", "domain": "enviro",
             "display_name": f"Law {i}",
             "delta": "+0.04 confidence",
             "rationale": "test"}
            for i in range(n_events)
        ],
    }, indent=2), encoding="utf-8")
    return p


# ============================================================
# Admin gate (Atom 4.3 — manual review for launch)
# ============================================================

class TestAdminTokenGate:

    def test_admin_endpoints_require_token(self, client):
        # No header → 401.
        r = client.get("/api/learning/admin/list_drafts")
        assert r.status_code == 401

    def test_admin_endpoints_with_token_succeed(self, client):
        r = client.get("/api/learning/admin/list_drafts",
                        headers={"X-Aurora-Admin-Token": "test-token-123"})
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_admin_endpoints_reject_wrong_token(self, client):
        r = client.get("/api/learning/admin/list_drafts",
                        headers={"X-Aurora-Admin-Token": "wrong"})
        assert r.status_code == 401


class TestAdminListAndReadDraft:

    def test_list_drafts(self, client):
        _make_draft(client._tmp_log, "2026-05-06")
        _make_draft(client._tmp_log, "2026-05-05")
        r = client.get("/api/learning/admin/list_drafts",
                        headers={"X-Aurora-Admin-Token": "test-token-123"})
        j = r.get_json()
        assert j["ok"] is True
        dates = [e["date"] for e in j["drafts"]]
        assert "2026-05-06" in dates and "2026-05-05" in dates

    def test_read_specific_draft(self, client):
        _make_draft(client._tmp_log, "2026-05-06", n_events=4)
        r = client.get("/api/learning/admin/draft?date=2026-05-06",
                        headers={"X-Aurora-Admin-Token": "test-token-123"})
        j = r.get_json()
        assert j["ok"] is True
        assert len(j["draft"]["events"]) == 4

    def test_read_missing_draft_404(self, client):
        r = client.get("/api/learning/admin/draft?date=2026-01-01",
                        headers={"X-Aurora-Admin-Token": "test-token-123"})
        assert r.status_code == 404


# ============================================================
# Publish flow — drafts MUST go through admin before public
# ============================================================

class TestPublishFlow:

    def test_default_publish_promotes_all_events(self, client):
        _make_draft(client._tmp_log, "2026-05-06", n_events=3)
        r = client.post("/api/learning/admin/publish",
                         json={"date": "2026-05-06"},
                         headers={"X-Aurora-Admin-Token": "test-token-123"})
        j = r.get_json()
        assert j["ok"] is True
        assert j["n_events_published"] == 3
        # Public file now exists.
        pub = client._tmp_log / "published" / "2026-05-06.json"
        assert pub.exists()
        d = json.loads(pub.read_text(encoding="utf-8"))
        assert d["published"] is True
        assert d["review_required"] is False

    def test_reject_specific_events(self, client):
        _make_draft(client._tmp_log, "2026-05-06", n_events=3)
        r = client.post("/api/learning/admin/publish",
                         json={"date": "2026-05-06",
                               "rejected_event_indices": [1]},
                         headers={"X-Aurora-Admin-Token": "test-token-123"})
        j = r.get_json()
        assert j["ok"] is True
        assert j["n_events_published"] == 2
        assert j["n_events_rejected"] == 1

    def test_edit_event_rationale(self, client):
        _make_draft(client._tmp_log, "2026-05-06", n_events=2)
        r = client.post("/api/learning/admin/publish",
                         json={"date": "2026-05-06",
                               "edits": {"0": {"rationale": "manually reworded"}}},
                         headers={"X-Aurora-Admin-Token": "test-token-123"})
        assert r.status_code == 200
        pub = client._tmp_log / "published" / "2026-05-06.json"
        d = json.loads(pub.read_text(encoding="utf-8"))
        assert d["events"][0]["rationale"] == "manually reworded"

    def test_publish_requires_token(self, client):
        _make_draft(client._tmp_log, "2026-05-06")
        r = client.post("/api/learning/admin/publish",
                         json={"date": "2026-05-06"})
        assert r.status_code == 401


# ============================================================
# Public read endpoints — no token required, but only published
# ============================================================

class TestPublicChangelog:

    def test_no_published_returns_null_changelog(self, client):
        r = client.get("/api/learning/changelog")
        j = r.get_json()
        assert j["ok"] is True
        assert j["changelog"] is None

    def test_public_endpoint_only_serves_published(self, client):
        # Stage a draft only — should NOT be visible publicly.
        _make_draft(client._tmp_log, "2026-05-06")
        r = client.get("/api/learning/changelog")
        j = r.get_json()
        # No published file yet.
        assert j["changelog"] is None

    def test_after_publish_visible(self, client):
        _make_draft(client._tmp_log, "2026-05-06", n_events=2)
        client.post("/api/learning/admin/publish",
                     json={"date": "2026-05-06"},
                     headers={"X-Aurora-Admin-Token": "test-token-123"})
        r = client.get("/api/learning/changelog")
        j = r.get_json()
        assert j["changelog"]["date"] == "2026-05-06"
        assert len(j["changelog"]["events"]) == 2


class TestRSSAndJSONFeeds:

    def test_rss_returns_xml(self, client):
        r = client.get("/api/learning/feed.rss")
        assert r.status_code == 200
        assert "application/rss+xml" in r.headers.get("Content-Type", "")
        body = r.get_data(as_text=True)
        assert "<rss" in body and "<channel>" in body

    def test_json_feed_format(self, client):
        r = client.get("/api/learning/feed.json")
        assert r.status_code == 200
        j = r.get_json()
        # JSONFeed structure.
        assert j["version"].startswith("https://jsonfeed.org/")
        assert "items" in j

    def test_json_feed_only_includes_published(self, client):
        _make_draft(client._tmp_log, "2026-05-06")  # draft only
        r = client.get("/api/learning/feed.json")
        j = r.get_json()
        # Empty until published.
        assert j["items"] == []


class TestPublicHTMLPage:

    def test_learning_page_serves(self, client):
        r = client.get("/learning")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("Content-Type", "")
        body = r.get_data(as_text=True)
        assert "AURORA" in body and "LEARNING JOURNAL" in body


class TestLibraryEndpoint:

    def test_library_endpoint_returns_summary(self, client):
        r = client.get("/api/learning/library")
        j = r.get_json()
        assert j["ok"] is True
        assert "library" in j
        assert "total" in j["library"]
