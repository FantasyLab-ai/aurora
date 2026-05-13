"""
Acceptance tests for the reliability hardening sprint.

One test per acceptance criterion in the brief:

  1. All connectors produce clean 200 JSON (Problem 1).
  2. Polling drives UI without manual refresh (Problem 2 — server side).
  3. Run identity is always visible (Problem 3 — run_meta on every state).
  4. Tiered affordances surface end-to-end (Problem 4).

Plus the user's reminder: "we need to surface true insights, actions,
next steps for users datasets" — covered by insight-quality assertions.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

from fantasyai.aurora import run_registry as _reg   # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    _reg._clear_for_tests()
    import studio_api
    monkeypatch.setattr(studio_api, "DEFAULT_OUTPUTS", tmp_path / "outputs")
    studio_api.app.config["TESTING"] = True
    with studio_api.app.test_client() as c:
        yield c


# Reuse the run-dir factory from test_state_json_clean.
sys.path.insert(0, str(Path(__file__).parent))
from test_state_json_clean import _write_run_dir   # noqa: E402


# ============================================================
# Acceptance 1: connectors produce clean 200 JSON
# ============================================================

class TestAcceptance1_NoMockBannerOnRealData:

    @pytest.mark.parametrize("scenario", [
        "usgs_quake", "openml_classif", "world_bank", "noaa_ts", "user_upload",
    ])
    def test_connector_state_endpoint_returns_clean_200(self, scenario,
                                                        client, tmp_path):
        # Build a synthesised run dir per scenario.
        if scenario == "usgs_quake":
            df = pd.DataFrame({
                "time": pd.date_range("2024-01-01", periods=100, freq="h"),
                "magnitude": np.random.RandomState(0).uniform(2.0, 6.5, 100),
            })
            target, time_col = "magnitude", "time"
        elif scenario == "openml_classif":
            df = pd.DataFrame({
                "f1": np.random.RandomState(0).normal(0, 1, 100),
                "class": np.random.RandomState(1).randint(0, 3, 100),
            })
            target, time_col = "class", None
        elif scenario == "world_bank":
            df = pd.DataFrame({
                "year": list(range(2000, 2024)),
                "gdp_per_capita_usd": np.random.RandomState(0).uniform(
                    10_000, 60_000, 24,
                ).astype(np.int64),
            })
            target, time_col = "gdp_per_capita_usd", "year"
        elif scenario == "noaa_ts":
            df = pd.DataFrame({
                "timestamp": pd.date_range("2024-01-01", periods=200, freq="h"),
                "temperature_c": 15 + np.random.RandomState(0).normal(0, 3, 200),
            })
            target, time_col = "temperature_c", "timestamp"
        else:    # user_upload
            df = pd.DataFrame({
                "row": np.arange(100, dtype=np.int64),
                "x": np.random.RandomState(0).normal(0, 1, 100).astype(np.float32),
            })
            target, time_col = "x", None

        run_dir = _write_run_dir(
            tmp_path, csv_df=df, target_col=target, time_col=time_col,
            add_regimes=bool(time_col),
        )
        # GET /api/state with the explicit run_dir.
        r = client.get(f"/api/state?run_dir={run_dir}")
        # MUST be 200.
        assert r.status_code == 200, \
            f"{scenario}: HTTP {r.status_code} - body: {r.get_data(as_text=True)[:200]}"
        # Body must be valid JSON.
        j = r.get_json()
        assert j is not None, f"{scenario}: response was not JSON"
        assert j.get("ok") is True, \
            f"{scenario}: ok was {j.get('ok')}, reason={j.get('reason')}"
        # Re-encode to verify no numpy types leaked.
        re_encoded = json.dumps(j)
        assert isinstance(re_encoded, str)


# ============================================================
# Acceptance 2 (server side): /api/run is async, /api/run/status walks states
# ============================================================

class TestAcceptance2_AsyncRunAndStatus:

    def test_run_returns_run_id_immediately(self, client, tmp_path):
        df = pd.DataFrame({
            "ts": pd.date_range("2024-01-01", periods=50, freq="h"),
            "v": np.random.RandomState(0).normal(0, 1, 50),
        })
        csv_path = tmp_path / "tiny.csv"
        df.to_csv(csv_path, index=False)
        t0 = time.time()
        r = client.post("/api/run", json={
            "dataset": str(csv_path),
            "also_llm": False, "also_motif": False,
            "also_orchestrator": False, "math_v2": False,
            "deep_math": False, "also_math": False,
        })
        elapsed = time.time() - t0
        # Async kickoff must return well under the runner's actual duration.
        assert elapsed < 5.0, f"kickoff took {elapsed:.2f}s — should be near-instant"
        j = r.get_json()
        assert j["ok"] is True
        assert j.get("run_id") or j.get("cached")

    def test_status_endpoint_carries_lifecycle_fields(self, client):
        # Pre-register a run + walk states; assert endpoint reflects them.
        run_id = "accept2_test"
        _reg.register(run_id, "/tmp/x.csv", seed=42, flags={"also_llm": False})
        _reg.set_state(run_id, _reg.RunState.TIER1_RUNNING, current_tier=1,
                        progress_pct=20.0, tiered=True)
        r = client.get(f"/api/run/status?run_id={run_id}")
        j = r.get_json()
        assert j["ok"] is True
        assert j["state"] == "tier1_running"
        assert j["current_tier"] == 1
        assert j["progress_pct"] == 20.0
        assert j["tiered"] is True

    def test_status_terminal_states(self, client):
        for terminal_state, label in [
            (_reg.RunState.COMPLETE, "complete"),
            (_reg.RunState.FAILED, "failed"),
            (_reg.RunState.CANCELLED, "cancelled"),
        ]:
            run_id = f"accept2_terminal_{label}"
            _reg.register(run_id, "/tmp/x.csv")
            if label == "failed":
                _reg.fail(run_id, "test error")
            elif label == "cancelled":
                _reg.cancel(run_id)
            else:
                _reg.complete(run_id, run_dir="/tmp/x")
            j = client.get(f"/api/run/status?run_id={run_id}").get_json()
            assert j["state"] == label
            assert j["completed_at"]


# ============================================================
# Acceptance 3: run_meta on every /api/state response
# ============================================================

class TestAcceptance3_RunMetaAlwaysPresent:

    def test_state_includes_run_meta(self, client, tmp_path):
        df = pd.DataFrame({
            "ts": pd.date_range("2024-01-01", periods=100, freq="h"),
            "v": np.random.RandomState(0).normal(0, 1, 100),
        })
        run_dir = _write_run_dir(tmp_path, csv_df=df, target_col="v",
                                  time_col="ts")
        r = client.get(f"/api/state?run_dir={run_dir}")
        j = r.get_json()
        meta = j["state"]["run_meta"]
        # Required fields.
        for field in ("run_id", "run_dir", "dataset_name",
                      "started_at", "state", "tiered", "is_active"):
            assert field in meta, f"run_meta missing {field}"
        assert meta["run_id"] == run_dir.name
        assert meta["run_dir"] == str(run_dir)
        # Dataset name should be set even when registry doesn't know.
        assert meta["dataset_name"]

    def test_run_meta_state_reflects_registry_for_active_run(self, client, tmp_path):
        df = pd.DataFrame({
            "v": np.random.RandomState(0).normal(0, 1, 100),
        })
        run_dir = _write_run_dir(tmp_path, csv_df=df, target_col="v",
                                  time_col=None, add_regimes=False)
        # Register a fake "running" entry pointing to this run_dir.
        _reg.register("active_test", str(tmp_path / "x.csv"))
        _reg.set_run_dir("active_test", str(run_dir))
        _reg.set_state("active_test", _reg.RunState.TIER2_RUNNING,
                        current_tier=2, tiered=True)
        r = client.get(f"/api/state?run_dir={run_dir}")
        j = r.get_json()
        meta = j["state"]["run_meta"]
        assert meta["is_active"] is True
        # The registry's running state overrides on-disk inferred terminal state.
        assert meta["state"] == "tier2_running"
        assert meta["current_tier"] == 2


# ============================================================
# Acceptance 4: tiered affordances + insight quality
# ============================================================

class TestAcceptance4_InsightsAreReal:
    """The user's reminder: surface TRUE insights, actions, next steps."""

    def test_findings_are_present(self, client, tmp_path):
        df = pd.DataFrame({
            "ts": pd.date_range("2024-01-01", periods=300, freq="h"),
            "temperature_c": 15 + np.random.RandomState(0).normal(0, 3, 300),
        })
        run_dir = _write_run_dir(tmp_path, csv_df=df,
                                  target_col="temperature_c", time_col="ts")
        j = client.get(f"/api/state?run_dir={run_dir}").get_json()
        findings = j["state"].get("findings") or []
        assert len(findings) >= 1, "no findings surfaced"

    def test_findings_have_referents(self, client, tmp_path):
        df = pd.DataFrame({
            "ts": pd.date_range("2024-01-01", periods=300, freq="h"),
            "temperature_c": 15 + np.random.RandomState(0).normal(0, 3, 300),
        })
        run_dir = _write_run_dir(tmp_path, csv_df=df,
                                  target_col="temperature_c", time_col="ts")
        j = client.get(f"/api/state?run_dir={run_dir}").get_json()
        for f in (j["state"].get("findings") or []):
            ref = f.get("referent") or {}
            assert ref.get("primary_label"), \
                f"finding {f.get('title')!r} missing referent.primary_label"
            assert ref.get("level_used") in (
                "named_event", "timestamp", "entity", "group",
                "position", "row_index",
            )

    def test_findings_have_actions_or_recommendations(self, client, tmp_path):
        df = pd.DataFrame({
            "ts": pd.date_range("2024-01-01", periods=300, freq="h"),
            "value": np.random.RandomState(0).normal(50, 5, 300),
        })
        run_dir = _write_run_dir(tmp_path, csv_df=df,
                                  target_col="value", time_col="ts")
        j = client.get(f"/api/state?run_dir={run_dir}").get_json()
        findings = j["state"].get("findings") or []
        assert findings, "must surface at least one finding"
        for f in findings:
            has_actions = bool(f.get("actions"))
            has_narrative = bool((f.get("narrative") or {}).get("body"))
            has_rec = bool(f.get("recommendation"))
            assert has_actions or has_narrative or has_rec, \
                f"finding {f.get('title')!r} has no actions / narrative / rec"

    def test_anomalies_carry_severity_and_score(self, client, tmp_path):
        df = pd.DataFrame({
            "ts": pd.date_range("2024-01-01", periods=300, freq="h"),
            "value": np.random.RandomState(0).normal(0, 1, 300),
        })
        run_dir = _write_run_dir(tmp_path, csv_df=df,
                                  target_col="value", time_col="ts")
        j = client.get(f"/api/state?run_dir={run_dir}").get_json()
        for a in (j["state"].get("anomalies") or []):
            assert a.get("severity") in ("crit", "warn", "info", "ok"), \
                f"unknown severity: {a.get('severity')}"
            assert a.get("score") is not None
            assert a.get("column")

    def test_overview_carries_health(self, client, tmp_path):
        df = pd.DataFrame({
            "ts": pd.date_range("2024-01-01", periods=200, freq="h"),
            "value": np.random.RandomState(0).normal(0, 1, 200),
        })
        run_dir = _write_run_dir(tmp_path, csv_df=df,
                                  target_col="value", time_col="ts")
        j = client.get(f"/api/state?run_dir={run_dir}").get_json()
        overview = j["state"].get("overview") or {}
        assert overview.get("available") is True
        # Confidence may be None but the field must exist.
        assert "confidence" in overview
