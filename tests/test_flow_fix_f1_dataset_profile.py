"""
Sub-atom F.1 regression net.

Pins the new behaviours:
  - /api/upload no longer triggers /api/run by default.
  - POST /api/dataset/profile runs Tier 0 and returns runtime estimates.
  - The estimated_runtime map carries the three required keys.
  - The auto_recommendation rule matches the spec table:
      <100K rows → full
      100K-10M    → standard
      ≥10M        → quick
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fantasyai.aurora import run_registry as _reg
    _reg._clear_for_tests()
    import studio_api
    monkeypatch.setattr(studio_api, "DEFAULT_OUTPUTS", tmp_path / "outputs")
    studio_api.app.config["TESTING"] = True
    with studio_api.app.test_client() as c:
        yield c


def _write_tiny_csv(tmp_path: Path, n: int = 50) -> Path:
    p = tmp_path / "tiny.csv"
    pd.DataFrame({
        "ts": pd.date_range("2024-01-01", periods=n, freq="h"),
        "v":  np.random.RandomState(0).normal(0, 1, n),
    }).to_csv(p, index=False)
    return p


# ============================================================
# /api/upload no longer triggers /api/run by default
# ============================================================

class TestUploadDoesNotAutorun:

    def test_upload_default_does_not_run_analysis(self, client, tmp_path,
                                                    monkeypatch):
        # Watch for any call to _trigger_run during upload.
        import studio_api
        called = {"count": 0}
        original = studio_api._trigger_run
        def spy(*a, **k):
            called["count"] += 1
            return original(*a, **k)
        monkeypatch.setattr(studio_api, "_trigger_run", spy)

        csv_path = _write_tiny_csv(tmp_path)
        with open(csv_path, "rb") as f:
            r = client.post("/api/upload",
                              data={"file": (f, "tiny.csv")},
                              content_type="multipart/form-data")
        j = r.get_json()
        assert j["ok"] is True
        assert j["auto_run"] is False
        # CRITICAL: _trigger_run was NEVER called from the upload path.
        assert called["count"] == 0

    def test_upload_with_explicit_auto_run_still_works(self, client, tmp_path,
                                                        monkeypatch):
        # Legacy CLI scripts pass auto_run=1; that path must still work.
        import studio_api
        called = {"count": 0}
        def fake_trigger(**k):
            called["count"] += 1
            return {"ok": True, "run_dir": str(tmp_path / "run")}
        monkeypatch.setattr(studio_api, "_trigger_run", fake_trigger)

        csv_path = _write_tiny_csv(tmp_path)
        with open(csv_path, "rb") as f:
            r = client.post("/api/upload",
                              data={"file": (f, "tiny.csv"),
                                     "auto_run": "1"},
                              content_type="multipart/form-data")
        j = r.get_json()
        assert j["auto_run"] is True
        assert called["count"] == 1


# ============================================================
# /api/dataset/profile — happy path + shape
# ============================================================

class TestDatasetProfileShape:

    def test_returns_required_fields(self, client, tmp_path):
        csv = _write_tiny_csv(tmp_path)
        r = client.post("/api/dataset/profile",
                          json={"path": str(csv)})
        j = r.get_json()
        assert j["ok"] is True
        # Required top-level fields.
        for k in ("path", "size_bytes", "size_mb",
                   "tier0_profile", "estimated_runtime",
                   "auto_recommendation", "template_id",
                   "template_confidence", "warnings"):
            assert k in j, f"missing field: {k}"

    def test_estimated_runtime_keys(self, client, tmp_path):
        csv = _write_tiny_csv(tmp_path)
        j = client.post("/api/dataset/profile",
                          json={"path": str(csv)}).get_json()
        rt = j["estimated_runtime"]
        for k in ("quick", "standard", "full"):
            assert k in rt, f"estimated_runtime missing {k}"
            # All values are human-readable strings.
            assert isinstance(rt[k], str)
            assert "~" in rt[k] or "second" in rt[k] or "minute" in rt[k] or "hour" in rt[k]

    def test_tier0_profile_has_columns(self, client, tmp_path):
        csv = _write_tiny_csv(tmp_path, n=200)
        j = client.post("/api/dataset/profile",
                          json={"path": str(csv)}).get_json()
        prof = j["tier0_profile"]
        assert prof["rows_total"] == 200
        assert prof["cols_total"] == 2
        assert isinstance(prof["columns"], list)


# ============================================================
# /api/dataset/profile — auto_recommendation rule
# ============================================================

class TestAutoRecommendation:

    def test_full_for_small_data(self, client, tmp_path):
        csv = _write_tiny_csv(tmp_path, n=100)
        j = client.post("/api/dataset/profile",
                          json={"path": str(csv)}).get_json()
        # 100 rows is well under 100K → full.
        assert j["auto_recommendation"] == "full"

    def test_rule_unit(self):
        # Pure-function test of the recommendation rule.
        from studio_api import _auto_recommendation
        assert _auto_recommendation(50_000)      == "full"
        assert _auto_recommendation(99_999)      == "full"
        assert _auto_recommendation(100_000)     == "standard"
        assert _auto_recommendation(5_000_000)   == "standard"
        assert _auto_recommendation(9_999_999)   == "standard"
        assert _auto_recommendation(10_000_000)  == "quick"
        assert _auto_recommendation(100_000_000) == "quick"


# ============================================================
# /api/dataset/profile — runtime estimator
# ============================================================

class TestRuntimeEstimator:

    def test_estimator_keys(self):
        from studio_api import _estimate_runtimes
        rt = _estimate_runtimes(rows=1_000_000, cols=10, shape="time_series")
        assert set(rt.keys()) == {"quick", "standard", "full"}

    def test_quick_under_60s(self):
        from studio_api import _estimate_runtimes
        rt = _estimate_runtimes(rows=10_000_000, cols=10, shape="time_series")
        # Quick is capped at 60s by design.
        assert "second" in rt["quick"] or "minute" in rt["quick"]
        # Just confirm it parses to a number ≤ 60s when in seconds.
        if "second" in rt["quick"]:
            n = int(rt["quick"].replace("~", "").replace("seconds", "").strip())
            assert n <= 60

    def test_standard_capped_at_3min(self):
        from studio_api import _estimate_runtimes
        rt = _estimate_runtimes(rows=10_000_000, cols=10, shape="time_series")
        # Standard is capped at 180s.
        if "second" in rt["standard"]:
            n = int(rt["standard"].replace("~", "").replace("seconds", "").strip())
            assert n <= 180

    def test_full_grows_with_rows(self):
        from studio_api import _estimate_runtimes
        small = _estimate_runtimes(rows=10_000, cols=5, shape="time_series")
        big = _estimate_runtimes(rows=10_000_000, cols=5, shape="time_series")
        # Full should be longer for bigger data.
        # We compare humanised strings by extracting integers.
        def _to_seconds(s):
            n = int(''.join(c for c in s if c.isdigit()) or 0)
            return n * 60 if "minute" in s else (n * 3600 if "hour" in s else n)
        assert _to_seconds(big["full"]) > _to_seconds(small["full"])


# ============================================================
# /api/dataset/profile — error paths
# ============================================================

class TestProfileErrors:

    def test_missing_path_400(self, client):
        r = client.post("/api/dataset/profile", json={})
        assert r.status_code == 400

    def test_nonexistent_path_404(self, client):
        r = client.post("/api/dataset/profile",
                          json={"path": "/not/a/real/file.csv"})
        assert r.status_code == 404

    def test_directory_400(self, client, tmp_path):
        r = client.post("/api/dataset/profile",
                          json={"path": str(tmp_path)})
        assert r.status_code == 400


# ============================================================
# Idempotence — same file → same answer
# ============================================================

class TestIdempotence:

    def test_two_calls_match(self, client, tmp_path):
        csv = _write_tiny_csv(tmp_path)
        a = client.post("/api/dataset/profile",
                          json={"path": str(csv)}).get_json()
        b = client.post("/api/dataset/profile",
                          json={"path": str(csv)}).get_json()
        # Modulo elapsed_s, the answers must match.
        a["tier0_profile"].pop("elapsed_s", None)
        b["tier0_profile"].pop("elapsed_s", None)
        assert a["tier0_profile"] == b["tier0_profile"]
        assert a["estimated_runtime"] == b["estimated_runtime"]
        assert a["auto_recommendation"] == b["auto_recommendation"]
