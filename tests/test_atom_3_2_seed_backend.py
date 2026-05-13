"""
Atom 3.2: Backend wiring for seed_text — /api/run accepts it,
run_registry persists it, run_meta surfaces it, state_builder invokes
the SeedRanker.

Acceptance criteria from the brief:
  1. Same dataset + 3 different seeds → identical findings, different
     relevant_findings ordering.
  2. No seed → no behavioural change.
  3. POST /api/run/seed updates the seed without re-running analysis.
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

from fantasyai.aurora import run_registry as _reg   # noqa: E402

# Reuse the run-dir factory from earlier tests.
sys.path.insert(0, str(Path(__file__).parent))
from test_state_json_clean import _write_run_dir   # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    _reg._clear_for_tests()
    import studio_api
    monkeypatch.setattr(studio_api, "DEFAULT_OUTPUTS", tmp_path / "outputs")
    studio_api.app.config["TESTING"] = True
    with studio_api.app.test_client() as c:
        yield c


# ============================================================
# /api/run accepts seed_text
# ============================================================

class TestRunAcceptsSeedText:

    def test_seed_text_persists_on_record(self, client, tmp_path):
        df = pd.DataFrame({
            "ts": pd.date_range("2024-01-01", periods=50, freq="h"),
            "v":  np.random.RandomState(0).normal(0, 1, 50),
        })
        csv = tmp_path / "tiny.csv"
        df.to_csv(csv, index=False)
        r = client.post("/api/run", json={
            "dataset": str(csv), "seed_text": "hurricane impact",
            "also_llm": False, "also_motif": False,
            "also_orchestrator": False, "math_v2": False,
            "deep_math": False, "also_math": False,
        })
        j = r.get_json()
        assert j["ok"] is True
        run_id = j["run_id"]
        snap = _reg.snapshot(run_id)
        assert snap["seed_text"] == "hurricane impact"
        assert snap["seed_source"] == "user"

    def test_no_seed_text_leaves_field_none(self, client, tmp_path):
        df = pd.DataFrame({"v": [1.0, 2.0, 3.0]})
        csv = tmp_path / "tiny.csv"
        df.to_csv(csv, index=False)
        r = client.post("/api/run", json={
            "dataset": str(csv),
            "also_llm": False, "also_motif": False,
            "also_orchestrator": False, "math_v2": False,
            "deep_math": False, "also_math": False,
        })
        j = r.get_json()
        snap = _reg.snapshot(j["run_id"])
        assert snap.get("seed_text") is None

    def test_whitespace_only_seed_treated_as_empty(self, client, tmp_path):
        df = pd.DataFrame({"v": [1.0, 2.0, 3.0]})
        csv = tmp_path / "tiny.csv"
        df.to_csv(csv, index=False)
        r = client.post("/api/run", json={
            "dataset": str(csv), "seed_text": "   ",
            "also_llm": False, "also_motif": False,
            "also_orchestrator": False, "math_v2": False,
            "deep_math": False, "also_math": False,
        })
        j = r.get_json()
        snap = _reg.snapshot(j["run_id"])
        assert snap.get("seed_text") is None


# ============================================================
# /api/run/seed — update seed without re-running
# ============================================================

class TestApiRunSeed:

    def test_update_seed_persists(self, client):
        run_id = "test_seed_update"
        _reg.register(run_id, "/tmp/x.csv")
        r = client.post("/api/run/seed", json={
            "run_id": run_id, "seed_text": "regime shift",
        })
        j = r.get_json()
        assert j["ok"] is True
        assert j["seed_text"] == "regime shift"
        assert _reg.snapshot(run_id)["seed_text"] == "regime shift"

    def test_clear_seed_with_empty_string(self, client):
        run_id = "test_seed_clear"
        _reg.register(run_id, "/tmp/x.csv", seed_text="initial")
        client.post("/api/run/seed", json={
            "run_id": run_id, "seed_text": "",
        })
        snap = _reg.snapshot(run_id)
        assert snap["seed_text"] is None

    def test_unknown_run_id_404(self, client):
        r = client.post("/api/run/seed", json={
            "run_id": "nonexistent", "seed_text": "x",
        })
        assert r.status_code == 404

    def test_source_recorded_for_audit(self, client):
        run_id = "test_seed_source"
        _reg.register(run_id, "/tmp/x.csv")
        client.post("/api/run/seed", json={
            "run_id": run_id, "seed_text": "x", "source": "copilot",
        })
        snap = _reg.snapshot(run_id)
        assert snap["seed_source"] == "copilot"


# ============================================================
# state_builder integration: same data + different seeds = different ranking
# ============================================================

class TestStateBuilderSeedIntegration:

    def test_state_carries_seed_in_run_meta(self, client, tmp_path):
        df = pd.DataFrame({
            "ts": pd.date_range("2024-01-01", periods=200, freq="h"),
            "v":  np.random.RandomState(0).normal(0, 1, 200),
        })
        run_dir = _write_run_dir(tmp_path, csv_df=df, target_col="v",
                                  time_col="ts")
        # Pre-stage the registry so run_meta picks up seed_text.
        _reg.register(run_dir.name, str(tmp_path / "data.csv"),
                       seed_text="anomaly in temperature")
        _reg.set_run_dir(run_dir.name, str(run_dir))
        r = client.get(f"/api/state?run_dir={run_dir}")
        j = r.get_json()
        meta = j["state"]["run_meta"]
        assert meta["seed_text"] == "anomaly in temperature"
        # Seed source is "user" by default from register().
        assert meta["seed_source"] == "user"

    def test_dual_sections_appear_when_seed_set(self, client, tmp_path):
        df = pd.DataFrame({
            "ts": pd.date_range("2024-01-01", periods=300, freq="h"),
            "v":  np.random.RandomState(0).normal(0, 1, 300),
        })
        run_dir = _write_run_dir(tmp_path, csv_df=df, target_col="v",
                                  time_col="ts")
        _reg.register(run_dir.name, str(tmp_path / "data.csv"),
                       seed_text="anomaly in temperature")
        _reg.set_run_dir(run_dir.name, str(run_dir))
        j = client.get(f"/api/state?run_dir={run_dir}").get_json()
        s = j["state"]
        assert s.get("seed_ranker", {}).get("active") is True
        assert "relevant_findings" in s
        assert "surprise_findings" in s

    def test_no_seed_no_dual_sections(self, client, tmp_path):
        df = pd.DataFrame({
            "ts": pd.date_range("2024-01-01", periods=200, freq="h"),
            "v":  np.random.RandomState(0).normal(0, 1, 200),
        })
        run_dir = _write_run_dir(tmp_path, csv_df=df, target_col="v",
                                  time_col="ts")
        # No seed registered for this run_dir.
        j = client.get(f"/api/state?run_dir={run_dir}").get_json()
        s = j["state"]
        # Either seed_ranker absent OR active=False.
        assert (s.get("seed_ranker") is None
                or s.get("seed_ranker", {}).get("active") is False)
        assert "relevant_findings" not in s

    def test_three_different_seeds_yield_different_orderings(self, client, tmp_path):
        df = pd.DataFrame({
            "ts": pd.date_range("2024-01-01", periods=300, freq="h"),
            "value":  np.random.RandomState(0).normal(0, 1, 300),
        })
        run_dir = _write_run_dir(tmp_path, csv_df=df, target_col="value",
                                  time_col="ts")
        # First: seed about anomalies.
        _reg.register(run_dir.name, str(tmp_path / "data.csv"),
                       seed_text="anomaly in value")
        _reg.set_run_dir(run_dir.name, str(run_dir))
        j1 = client.get(f"/api/state?run_dir={run_dir}").get_json()
        rel1 = [r["finding"]["title"]
                for r in (j1["state"].get("relevant_findings") or [])]

        # Second: seed about regimes (update without re-run).
        client.post("/api/run/seed", json={
            "run_id": run_dir.name, "seed_text": "regime shift in value",
        })
        j2 = client.get(f"/api/state?run_dir={run_dir}").get_json()
        rel2 = [r["finding"]["title"]
                for r in (j2["state"].get("relevant_findings") or [])]

        # Third: seed about forecasting.
        client.post("/api/run/seed", json={
            "run_id": run_dir.name, "seed_text": "forecast peak prediction",
        })
        j3 = client.get(f"/api/state?run_dir={run_dir}").get_json()
        rel3 = [r["finding"]["title"]
                for r in (j3["state"].get("relevant_findings") or [])]

        # The CRITICAL invariant: state["findings"] is identical across runs.
        # Underlying analysis didn't re-run; only surface ordering changed.
        f1 = sorted([f["title"] for f in j1["state"]["findings"]])
        f2 = sorted([f["title"] for f in j2["state"]["findings"]])
        f3 = sorted([f["title"] for f in j3["state"]["findings"]])
        assert f1 == f2 == f3, "underlying findings must NOT change with seed"

        # And the relevant orderings should differ for at least two of three.
        unique_orderings = {tuple(rel1), tuple(rel2), tuple(rel3)}
        assert len(unique_orderings) >= 2, \
            f"three seeds produced same ordering: {unique_orderings}"
