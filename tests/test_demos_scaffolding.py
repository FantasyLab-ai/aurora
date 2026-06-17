"""
Audit tests for the `demos/` scaffolding.

These tests guard the structural pieces every demo video depends on:

  * The relay's 5 adapter modules are importable and have the
    documented surface (post / append / trigger).
  * The relay's Flask app exposes /aurora/fire, /aurora/events,
    /aurora/status, and /overlay/.
  * The overlay's HTML / CSS / JS files exist and reference the
    right SSE endpoint.
  * The replay harness CLI parses + runs against a small synthetic
    CSV without raising.
  * Every contract JSON parses + matches the Decision Contract
    schema (the engine's Contract.from_dict accepts it).
  * The two new generated datasets produce valid CSVs.
  * The Demo-5 extractor returns a meaningful payload from a
    synthetic deep_math.json.

Flask is required for the relay route test; skipped cleanly when
flask isn't installed (CI installs it; some local envs may not).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEMOS = ROOT / "demos"


# ----------------------------------------------------------------------
# Relay adapters
# ----------------------------------------------------------------------

class TestRelayAdapters:

    def test_all_five_adapters_importable(self):
        from demos.relay.adapters import (
            discord_adapter, slack_adapter, log_adapter,
            sse_adapter, device_adapter,
        )
        # Public functions on each.
        assert callable(getattr(discord_adapter, "post"))
        assert callable(getattr(slack_adapter, "post"))
        assert callable(getattr(log_adapter, "append"))
        assert callable(getattr(device_adapter, "trigger"))
        assert callable(getattr(sse_adapter, "envelope_to_card"))

    def test_discord_format_includes_citation_fields(self):
        from demos.relay.adapters.discord_adapter import _format
        env = {
            "contract_id": "test", "contract_name": "test-contract",
            "trigger_field": "findings.crit_count",
            "trigger_value": 4, "severity": "crit",
            "method": "hampel-z", "row": 1247, "z_score": 5.73,
            "fabricated": 0, "bundle_run_id": "20260522_test",
            "bundle_hash": "abcdef1234567890" * 4,
        }
        payload = _format(env)
        assert "embeds" in payload
        assert payload["embeds"]
        e = payload["embeds"][0]
        # Citation fields the brand demands.
        field_names = {f["name"].lower() for f in e["fields"]}
        for needed in ("method", "row", "fabricated", "severity"):
            assert needed in field_names
        # 0 fabricated chip surfaces with a checkmark.
        fab_field = next(f for f in e["fields"]
                          if f["name"].lower() == "fabricated")
        assert "✓" in fab_field["value"]

    def test_slack_format_returns_blocks_payload(self):
        from demos.relay.adapters.slack_adapter import _format
        env = {
            "contract_name": "the-save",
            "trigger_field": "findings.crit_count",
            "trigger_value": 4, "severity": "warn",
            "method": "iso-forest", "row": 250, "z_score": 3.1,
            "fabricated": 0, "bundle_run_id": "run-x",
        }
        payload = _format(env)
        assert "blocks" in payload
        # Header + section blocks present.
        block_types = [b["type"] for b in payload["blocks"]]
        assert "header" in block_types
        assert "section" in block_types

    def test_log_adapter_appends_jsonl(self, tmp_path):
        from demos.relay.adapters.log_adapter import append
        f = tmp_path / "fires.jsonl"
        r = append({"a": 1, "b": "x"}, str(f))
        assert r["ok"]
        assert f.exists()
        line = f.read_text(encoding="utf-8").strip()
        decoded = json.loads(line)
        assert decoded == {"a": 1, "b": "x"}
        # Multiple appends produce multiple lines.
        append({"a": 2}, str(f))
        lines = f.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

    def test_log_adapter_swallows_errors(self, monkeypatch):
        """log_adapter must never raise — it's on the fire-path."""
        from demos.relay.adapters import log_adapter
        # Point at a path whose parent can't be created.
        bad = "\x00/bad/place/fires.jsonl"
        r = log_adapter.append({"x": 1}, bad)
        assert r["ok"] is False
        assert "error" in r

    def test_device_adapter_refuses_non_http(self):
        from demos.relay.adapters.device_adapter import trigger
        r = trigger({"severity": "crit"}, "ftp://my-plug", "esp32")
        assert r["ok"] is False
        assert "http" in r["error"].lower()

    def test_device_adapter_returns_clean_error_on_unreachable_host(self):
        from demos.relay.adapters.device_adapter import trigger
        # localhost on a port nothing is listening to.
        r = trigger({"severity": "crit"}, "http://127.0.0.1:1/fire", "esp32")
        assert r["ok"] is False
        # Never raises across the boundary.
        assert "target" in r


# ----------------------------------------------------------------------
# Relay Flask app — requires flask. Only this class skips when flask
# isn't installed; the rest of the tests still run.
# ----------------------------------------------------------------------

try:
    import flask as _flask_check
    _HAS_FLASK = True
except ImportError:
    _HAS_FLASK = False


@pytest.mark.skipif(not _HAS_FLASK, reason="flask not installed")
class TestRelayApp:

    def test_index_lists_endpoints(self):
        from demos.relay.app import app
        c = app.test_client()
        r = c.get("/")
        body = r.get_json()
        assert r.status_code == 200
        assert body["service"] == "aurora-demo-relay"
        # All four endpoints documented.
        for key in ("fire", "events", "status", "overlay"):
            assert key in body["endpoints"]

    def test_status_reports_subscriber_count(self):
        from demos.relay.app import app
        c = app.test_client()
        r = c.get("/aurora/status")
        body = r.get_json()
        assert body["ok"] is True
        assert "subscribers" in body
        assert "enabled_adapters" in body

    def test_fire_envelope_normalises_minimal_payload(self):
        from demos.relay.app import app
        c = app.test_client()
        # Disable all the network-y adapters via env so the test stays
        # hermetic. Only sse + log will run.
        old = os.environ.get("AURORA_DEMO_ADAPTERS")
        os.environ["AURORA_DEMO_ADAPTERS"] = "sse,log"
        try:
            r = c.post("/aurora/fire", json={
                "contract_id": "x", "contract_name": "test",
                "trigger_field": "findings.crit_count",
                "trigger_value": 3,
                "metadata": {"method": "hampel-z",
                              "row": 712, "z_score": 5.1},
            })
            assert r.status_code == 200
            body = r.get_json()
            env = body["envelope"]
            assert env["contract_id"] == "x"
            assert env["method"] == "hampel-z"
            assert env["row"] == 712
            assert abs(env["z_score"] - 5.1) < 1e-9
            assert env["severity"] == "crit"
            # Adapter results dict surfaces what fired.
            assert "adapter_results" in body
        finally:
            if old is None:
                os.environ.pop("AURORA_DEMO_ADAPTERS", None)
            else:
                os.environ["AURORA_DEMO_ADAPTERS"] = old

    def test_fire_rejects_non_object_body(self):
        from demos.relay.app import app
        c = app.test_client()
        r = c.post("/aurora/fire", json=["not", "a", "dict"])
        assert r.status_code == 400


# ----------------------------------------------------------------------
# Overlay
# ----------------------------------------------------------------------

class TestOverlay:

    def test_html_loads_app_js_and_style(self):
        html = (DEMOS / "overlay" / "index.html").read_text(encoding="utf-8")
        assert 'href="style.css"' in html
        assert 'src="app.js"' in html
        # Reads from the relay's SSE endpoint by default.
        # (See app.js for the configurable RELAY constant.)

    def test_js_references_sse_endpoint(self):
        js = (DEMOS / "overlay" / "app.js").read_text(encoding="utf-8")
        assert "/aurora/events" in js
        # Uses EventSource (the canonical SSE client).
        assert "EventSource" in js
        # Supports the corner / hold / sound URL params.
        for p in ("corner", "hold", "sound", "demo"):
            assert f"'{p}'" in js or f'"{p}"' in js

    def test_css_uses_oklch_palette(self):
        css = (DEMOS / "overlay" / "style.css").read_text(encoding="utf-8")
        # Brand palette + chrome font.
        assert "Departure Mono" in css
        # Severity tiers each have their own card-accent rule.
        assert 'data-severity="crit"' in css
        assert 'data-severity="warn"' in css


# ----------------------------------------------------------------------
# Replay harness
# ----------------------------------------------------------------------

class TestReplay:

    def test_replay_appends_rows_to_target(self, tmp_path):
        from demos.replay import run
        src = tmp_path / "src.csv"
        dst = tmp_path / "dst.csv"
        # Tiny synthetic source.
        src.write_text(
            "t,y\n0,1\n1,2\n2,3\n3,4\n4,5\n",
            encoding="utf-8",
        )
        rc = run(src, target_path=dst, speed=1000.0,
                  start_rows=2, dt_seconds=0.01)
        assert rc == 0
        # Target now has header + 5 data rows.
        lines = dst.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "t,y"
        assert len(lines) == 1 + 5

    def test_replay_post_url_path_no_target(self, tmp_path, monkeypatch):
        """Mock urlopen so the --post path doesn't hit the network."""
        import urllib.request as urlreq
        from demos import replay as _replay

        calls = []
        class _FakeResp:
            status = 200
            def read(self, n=None): return b"{}"
            def __enter__(self): return self
            def __exit__(self, *a): return False
        def _fake(req, timeout=None):
            calls.append({"url": getattr(req, "full_url", str(req)),
                          "body": req.data})
            return _FakeResp()
        monkeypatch.setattr(urlreq, "urlopen", _fake)

        src = tmp_path / "src.csv"
        src.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
        rc = _replay.run(src,
                          post_url="http://example.invalid/ingest",
                          speed=1000.0, dt_seconds=0.01)
        assert rc == 0
        # Two rows → two POSTs.
        assert len(calls) == 2
        decoded = json.loads(calls[0]["body"])
        assert decoded == {"a": "1", "b": "2"}

    def test_replay_refuses_missing_source(self, tmp_path):
        from demos.replay import run
        rc = run(tmp_path / "does-not-exist.csv",
                  target_path=tmp_path / "out.csv", speed=1000.0)
        assert rc == 2

    def test_replay_requires_target_or_post(self, tmp_path):
        from demos.replay import run
        src = tmp_path / "src.csv"
        src.write_text("a\n1\n", encoding="utf-8")
        rc = run(src, target_path=None, post_url=None)
        assert rc == 2


# ----------------------------------------------------------------------
# Contracts
# ----------------------------------------------------------------------

class TestDemoContracts:

    @pytest.mark.parametrize("filename", [
        "aurora-alarm.json",
        "community-sentinel.json",
        "the-save.json",
    ])
    def test_contract_parses_via_engine(self, filename):
        from fantasyai.aurora.decision_contracts.engine import Contract
        path = DEMOS / "contracts" / filename
        assert path.exists(), f"{path} should ship with the demo scaffolding"
        doc = json.loads(path.read_text(encoding="utf-8"))
        c = Contract.from_dict(doc)
        # Every contract must point at the relay's /aurora/fire endpoint.
        urls = []
        for action in c.actions:
            if hasattr(action, "url"):
                urls.append(action.url)
        assert any("/aurora/fire" in u for u in urls), (
            f"{filename} should webhook into the relay's /aurora/fire endpoint"
        )

    def test_aurora_alarm_uses_crit_count_trigger(self):
        path = DEMOS / "contracts" / "aurora-alarm.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["trigger"]["field"] == "findings.crit_count"
        assert doc["trigger"]["op"] == ">="

    def test_save_demo_caps_per_hour(self):
        """The 'Save' demo should be rate-limited so a flapping
        regime doesn't spam Slack."""
        path = DEMOS / "contracts" / "the-save.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        rl = doc.get("rate_limit") or {}
        assert "max_per_hour" in rl


# ----------------------------------------------------------------------
# Datasets — falling-ball + server-metrics
# ----------------------------------------------------------------------

class TestDemoDatasets:

    def test_falling_ball_generates_and_has_quadratic_motion(self, tmp_path):
        from demos.datasets.falling_ball import generate as fb
        out = tmp_path / "fb.csv"
        path = fb.generate(seed=42, out_path=out)
        assert path == out
        assert out.exists()
        # Read + sanity check.
        import csv
        with open(out, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        ts = [float(r["t"]) for r in rows]
        ys = [float(r["y"]) for r in rows]
        # First sample is at t=0 with y near y0=5.0.
        assert abs(ts[0]) < 1e-6
        assert abs(ys[0] - 5.0) < 0.1
        # Motion is decreasing (a ball falls).
        assert ys[-1] < ys[0]
        # Symmetry check: a free-fall trajectory has y'' < 0 across the run.
        # Compute discrete second difference.
        n = len(ys)
        if n >= 3:
            second_diffs = [ys[i+1] - 2*ys[i] + ys[i-1] for i in range(1, n-1)]
            # On a quadratic with negative leading coefficient, the second
            # difference is roughly a · dt² < 0. Tolerate noise.
            mean_sd = sum(second_diffs) / len(second_diffs)
            assert mean_sd < 0

    def test_server_metrics_drift_exists_at_end(self, tmp_path):
        from demos.datasets.server_metrics import generate as sm
        out = tmp_path / "sm.csv"
        sm.generate(seed=7, out_path=out)
        import csv
        with open(out, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        # Baseline window — first 200 minutes.
        baseline_p99 = [float(r["p99_ms"]) for r in rows[:200]]
        baseline_mean = sum(baseline_p99) / len(baseline_p99)
        # End window — last 30 minutes.
        end_p99 = [float(r["p99_ms"]) for r in rows[-30:]]
        end_mean = sum(end_p99) / len(end_p99)
        # End should be at least 2× baseline — that's the drift.
        assert end_mean > 2.0 * baseline_mean, (
            f"server_metrics drift not detectable: baseline p99 ~{baseline_mean:.1f}ms, "
            f"end p99 ~{end_mean:.1f}ms"
        )


# ----------------------------------------------------------------------
# Demo 5 — physics extractor
# ----------------------------------------------------------------------

class TestPhysicsExtractor:

    def test_extract_returns_meaningful_payload_when_deep_math_has_best(self, tmp_path):
        from demos.datasets.falling_ball.extract_physics import extract
        rd = tmp_path / "fake_run"
        rd.mkdir()
        # Synthesise a deep_math.json with a physics_discovery.best block
        # that matches what Aurora actually emits.
        (rd / "deep_math.json").write_text(json.dumps({
            "physics_discovery": {
                "best": {
                    "name": "polynomial",
                    "params": {"c0": 5.0, "c1": 0.01, "c2": -4.905},
                    "rmse_test": 0.013,
                    "aic": -28.5,
                },
                "runner_ups": [],
            },
        }), encoding="utf-8")
        result = extract(rd)
        assert result["ok"] is True
        assert abs(result["a_recovered"] - (-9.81)) < 0.05
        assert "SINDy" in result["cited"]
        assert result["fabricated"] == 0
        # Overlay text has at least 2 beats.
        overlay = result["headline_overlay"]
        assert isinstance(overlay, list) and len(overlay) >= 2
        # Last beat should reference 'a' (the gravitational acceleration).
        assert any("a =" in line for line in overlay)

    def test_extract_returns_error_when_no_best(self, tmp_path):
        from demos.datasets.falling_ball.extract_physics import extract
        rd = tmp_path / "fake_run"
        rd.mkdir()
        (rd / "deep_math.json").write_text("{}", encoding="utf-8")
        result = extract(rd)
        assert result["ok"] is False
        assert "Physics" in result["error"] or "physics" in result["error"]
