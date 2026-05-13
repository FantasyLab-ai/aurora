"""Manual runner — works without pytest."""
import sys, json, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fantasyai.aurora.provenance import (
    ProvenanceEntry, write_ledger, read_ledger, load_entry, iter_ledger,
    build_provenance_for_run, LEDGER_FILENAME,
)
from fantasyai.aurora.provenance.ledger import hash_payload, hash_file
from fantasyai.aurora.explainers import explain_anomaly, explain_regime

passed = 0
failed = 0
def run(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  PASS  {name}")
        passed += 1
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        failed += 1
    except Exception as e:
        print(f"  ERR   {name}: {type(e).__name__}: {e}")
        failed += 1

print("=== provenance ledger ===")

def t1():
    e = ProvenanceEntry(claim_id="anom-0001", claim_type="anomaly",
                        claim_summary="x", method="m", artifact_ref="deep_math.json:foo")
    d = e.to_dict()
    assert d["claim_id"] == "anom-0001"
    assert d["timestamp"]; assert "Z" in d["timestamp"]
run("entry to_dict", t1)

def t2():
    p = {"b": 1, "a": [1, 2, {"x": 3}]}
    h1 = hash_payload(p); h2 = hash_payload(p)
    assert h1 == h2; assert h1.startswith("sha256:")
    assert hash_payload({"a":[1,2,{"x":4}],"b":1}) != h1
run("hash_payload deterministic", t2)

def t3():
    with tempfile.TemporaryDirectory() as d:
        rd = Path(d)
        entries = [
            ProvenanceEntry(claim_id="a-1", claim_type="anomaly", claim_summary="x", method="m", artifact_ref="a"),
            ProvenanceEntry(claim_id="r-1", claim_type="regime", claim_summary="y", method="n", artifact_ref="b"),
        ]
        write_ledger(rd, entries)
        rows = read_ledger(rd)
        assert len(rows) == 2
        assert load_entry(rd, "a-1")["claim_id"] == "a-1"
        assert load_entry(rd, "missing") is None
run("write/read/load roundtrip", t3)

def t4():
    with tempfile.TemporaryDirectory() as d:
        rd = Path(d)
        assert list(iter_ledger(rd)) == []
        (rd / LEDGER_FILENAME).write_text("", encoding="utf-8")
        assert list(iter_ledger(rd)) == []
run("iter_ledger empty", t4)

def t5():
    with tempfile.TemporaryDirectory() as d:
        rd = Path(d)
        (rd / LEDGER_FILENAME).write_text(
            '{"claim_id":"good-1","claim_type":"anomaly","claim_summary":"ok","method":"m","artifact_ref":"a"}\n'
            'NOT JSON AT ALL\n'
            '{"claim_id":"good-2","claim_type":"regime","claim_summary":"ok","method":"m","artifact_ref":"b"}\n',
            encoding="utf-8")
        items = list(iter_ledger(rd))
        assert len(items) == 2
        assert {i["claim_id"] for i in items} == {"good-1", "good-2"}
run("malformed lines skipped", t5)

def t6():
    with tempfile.TemporaryDirectory() as d:
        rd = Path(d) / "run_x"; rd.mkdir()
        deep = {"extensions": {"anomaly_attribution": {"top_k": 10, "points": [
            {"rank":1,"row_id":"47","score":5.67,"top_feature_drivers":[
                {"feature":"precip_mm","robust_z":10.86,"direction":"high"},
                {"feature":"temp_c","robust_z":0.48,"direction":"high"}]},
            {"rank":2,"row_id":"12","score":5.18,"top_feature_drivers":[
                {"feature":"precip_mm","robust_z":10.12,"direction":"high"}]}]}}}
        (rd / "deep_math.json").write_text(json.dumps(deep))
        entries = build_provenance_for_run(rd, write=True)
        anom = [e for e in entries if e["claim_type"] == "anomaly"]
        assert len(anom) == 2
        assert anom[0]["claim_id"] == "anom-0000"
        assert "row 47" in anom[0]["claim_summary"]
        assert "precip_mm" in anom[0]["claim_summary"]
        assert (rd / LEDGER_FILENAME).exists()
        assert load_entry(rd, "anom-0001")["claim_type"] == "anomaly"
run("synthesizer anomalies", t6)

def t7():
    with tempfile.TemporaryDirectory() as d:
        rd = Path(d) / "run_y"; rd.mkdir()
        deep = {"target_col":"temperature_c",
                "forecasting":{"method":"ar1","horizon":24,"phi":0.96,"sigma":1.99,
                               "forecast":[12.1,11.7,11.2],"lower":[8.2,6.2,4.7],
                               "upper":[16.0,17.1,17.7],"note":"ok"},
                "physics_discovery":{
                    "best":{"name":"linear_ode","model":"dy/dt = a*y + b","params":{"a":-0.001},
                            "rmse_test":5.83,"aic":15168,"k":2},
                    "runner_ups":[{"name":"logistic","rmse_test":6.79,"k":2},
                                  {"name":"damped_oscillator","rmse_test":9.95,"k":2}],
                    "target_col":"temperature_c","time_col":None}}
        (rd / "deep_math.json").write_text(json.dumps(deep))
        entries = build_provenance_for_run(rd, write=True)
        types = {e["claim_type"] for e in entries}
        assert "forecast" in types and "physics" in types
        physics = [e for e in entries if e["claim_type"] == "physics"]
        assert any(e["claim_id"] == "physics-best" for e in physics)
        assert sum(1 for e in physics if e["claim_id"].startswith("physics-ru-")) == 2
run("synthesizer forecast+physics+runner-ups", t7)

def t8():
    with tempfile.TemporaryDirectory() as d:
        rd = Path(d) / "run_empty"; rd.mkdir()
        entries = build_provenance_for_run(rd, write=True)
        assert entries == []
        assert not (rd / LEDGER_FILENAME).exists()
run("synthesizer empty artifacts", t8)

def t9():
    with tempfile.TemporaryDirectory() as d:
        rd = Path(d) / "run_failed"; rd.mkdir()
        deep = {"forecasting":{"note":"failed"},"causal_what_if":{"note":"skipped"},
                "physics_discovery":{},"regimes":{"note":"failed"}}
        (rd / "deep_math.json").write_text(json.dumps(deep))
        entries = build_provenance_for_run(rd, write=True)
        assert all(e["claim_type"] not in ("forecast","causal","regime") for e in entries)
run("synthesizer skips failed blocks", t9)

print("\n=== anomaly explainer ===")

def a1():
    point = {"rank":1,"row_id":"47","score":5.67,"top_feature_drivers":[
        {"feature":"precip_mm","robust_z":10.86,"direction":"high"},
        {"feature":"temp_c","robust_z":0.48,"direction":"high"}]}
    ex = explain_anomaly(point)
    assert ex["available"] is True
    assert "row 47" in ex["headline"]
    assert "precip_mm" in ex["headline"]
    assert ex["score"]["raw"] == 5.67
    pcts = [d["contribution_pct"] for d in ex["drivers"]]
    assert abs(sum(pcts) - 100.0) < 0.5
    assert ex["drivers"][0]["feature"] == "precip_mm"
    assert ex["drivers"][0]["contribution_pct"] > ex["drivers"][1]["contribution_pct"]
run("anomaly basic", a1)

def a2():
    ex = explain_anomaly({"rank":1,"row_id":"5","score":3.1,"top_feature_drivers":[]})
    assert ex["available"] is False
run("anomaly no drivers", a2)

def a3():
    assert explain_anomaly(None)["available"] is False
    assert explain_anomaly("garbage")["available"] is False
run("anomaly invalid input", a3)

def a4():
    points = [
        {"rank":1,"row_id":"47","score":5.0,"top_feature_drivers":[{"feature":"x","robust_z":5.0,"direction":"high"}]},
        {"rank":2,"row_id":"50","score":4.0,"top_feature_drivers":[{"feature":"x","robust_z":4.0,"direction":"high"}]},
        {"rank":3,"row_id":"200","score":3.5,"top_feature_drivers":[{"feature":"x","robust_z":3.5,"direction":"high"}]}]
    ex = explain_anomaly(points[0], all_points=points)
    assert any("co-occurrence" in v for v in ex["alternative_views"])
run("anomaly co-occurrence", a4)

print("\n=== regime explainer ===")

REG = {"method":"ruptures_pelt_rbf","target_col":"temperature_c","n":365,"penalty":17.7,
       "change_points":[175],"regime_count":2,
       "stats":[{"start":0,"end":175,"n":175,"mean":21.57,"std":3.05,"min":13.08,"max":27.47},
                {"start":175,"end":365,"n":190,"mean":8.81,"std":3.62,"min":2.28,"max":18.66}],
       "note":"ok"}

def r1():
    ex = explain_regime(REG, segment_index=0)
    assert ex["available"]
    assert ex["segment"]["start"] == 0
    assert ex["deltas"] == []
    assert ex["falsification"]
run("regime initial segment", r1)

def r2():
    ex = explain_regime(REG, segment_index=1)
    assert ex["available"]
    mean_delta = next(d for d in ex["deltas"] if d["stat"] == "mean")
    assert abs(mean_delta["before"] - 21.57) < 0.01
    assert abs(mean_delta["after"] - 8.81) < 0.01
    assert abs(mean_delta["delta"] - (-12.76)) < 0.01
    assert "dropped" in ex["headline"]
    assert abs(mean_delta["delta_normalized"]) > 2.0
run("regime delta computation", r2)

def r3():
    ex = explain_regime(REG, segment_index=99)
    assert not ex["available"]
    assert "out of range" in ex["reason"]
run("regime out of range", r3)

def r4():
    assert not explain_regime(None, 0)["available"]
    assert not explain_regime({"stats": "not a list"}, 0)["available"]
run("regime invalid input", r4)

def r5():
    block = {"method":"ruptures_pelt_rbf","target_col":"x","n":200,"penalty":10.0,
             "change_points":[100],"regime_count":2,
             "stats":[{"start":0,"end":100,"n":100,"mean":0.0,"std":1.0,"min":-3,"max":3},
                      {"start":100,"end":200,"n":100,"mean":0.0,"std":2.0,"min":-6,"max":6}],
             "note":"ok"}
    ex = explain_regime(block, segment_index=1)
    std_delta = next(d for d in ex["deltas"] if d["stat"] == "std")
    assert abs(std_delta["ratio"] - 2.0) < 0.001
    assert "expanded" in std_delta["note"]
run("regime std ratio interpretation", r5)

print(f"\n=== {passed} passed, {failed} failed ===")
sys.exit(0 if failed == 0 else 1)
