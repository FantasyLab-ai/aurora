"""Manual runner for v5 tests (export + KB)."""
import sys, json, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fantasyai.aurora.export import (
    export_findings_markdown, export_methods_markdown,
    export_reproducibility_bundle, export_all,
    _hash_priors_set,
)
from fantasyai.aurora.kb import match_finding_to_kb, list_kb_sources
from fantasyai.aurora.kb.local_kb import _CATALOGUE

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


def _write_minimal_run(tmp: Path) -> Path:
    """Build a minimal but valid run dir for export tests."""
    rd = tmp / "20260503_test_run"
    rd.mkdir()
    # deep_math.json with a fitted physics + anomaly + causal + forecast.
    deep = {
        "version": "deep_math_v3",
        "rows": 365, "cols": 2,
        "target_col": "temperature_c",
        "time_col": None,
        "forecasting": {
            "method": "ar1", "horizon": 24, "alpha": 0.05,
            "phi": 0.96, "sigma": 1.99,
            "forecast": [12.1, 11.7, 11.2, 10.8, 10.4, 10.0, 9.6, 9.2,
                         8.8, 8.5, 8.2, 7.9, 7.7, 7.4, 7.2, 7.0,
                         6.8, 6.6, 6.5, 6.3, 6.2, 6.1, 5.9, 5.8],
            "lower": [8.2]*24, "upper": [16.0]*24, "note": "ok",
        },
        "causal_what_if": {
            "method": "WHAT_IF_LINEAR_OLS", "n": 365,
            "target_col": "temperature_c", "treatment_col": "precip_mm",
            "covariates_used": [], "delta": 1.0,
            "beta_treatment": 1.35, "estimated_effect": 1.35, "note": "ok",
        },
        "physics_discovery": {
            "best": {"name": "linear_ode", "model": "dy/dt = a*y + b",
                     "params": {"a": -0.05, "b": 1.0, "dt": 1.0},
                     "rmse_test": 0.5, "aic": 200, "k": 2},
            "runner_ups": [],
            "target_col": "temperature_c",
            "physics_consistency_score": 0.95,
        },
        "regimes": {"method": "ruptures_pelt_rbf", "target_col": "temperature_c",
                    "n": 365, "penalty": 17.7, "change_points": [175],
                    "regime_count": 2,
                    "stats": [
                        {"start": 0, "end": 175, "n": 175, "mean": 21.6, "std": 3.0, "min": 13, "max": 27},
                        {"start": 175, "end": 365, "n": 190, "mean": 8.8, "std": 3.6, "min": 2, "max": 18},
                    ], "note": "ok"},
        "extensions": {"anomaly_attribution": {"top_k": 10, "points": [
            {"rank": 1, "row_id": "47", "score": 5.67,
             "top_feature_drivers": [
                 {"feature": "precip_mm", "robust_z": 10.86, "direction": "high"},
             ]},
        ]}},
    }
    (rd / "deep_math.json").write_text(json.dumps(deep))
    # Structure contract.
    structure = {
        "version": "structure_contract_v1",
        "shape": {"rows": 365, "cols": 2},
        "eligible": {"math_eligible": True, "deep_math_eligible": True,
                     "time_series_eligible": False, "geo_eligible": False},
        "columns": [
            {"name": "temperature_c", "role": "numeric", "dtype": "float64", "missing_rate": 0.0},
            {"name": "precip_mm", "role": "numeric", "dtype": "float64", "missing_rate": 0.0},
        ],
        "time": {"best_time_col": None},
        "time_col": None, "target_col": "temperature_c",
        "n_rows": 365, "n_cols": 2, "has_time_axis": False,
    }
    (rd / "structure_contract.json").write_text(json.dumps(structure))
    # _RUN_META.json
    (rd / "_RUN_META.json").write_text(json.dumps({
        "time_col": None, "target_col": "temperature_c", "dt_seconds": None,
        "n_rows": 365, "n_cols": 2, "numeric_cols": ["temperature_c", "precip_mm"],
    }))
    # _RESOLVED_CONTRACT.json
    (rd / "_RESOLVED_CONTRACT.json").write_text(json.dumps({
        "version": "resolved_contract_v1", "run_dir": str(rd),
        "dataset_key": "synthetic_test", "time_col": None,
        "target_col": "temperature_c", "dt_seconds": None,
        "n_rows": 365, "n_cols": 2, "has_time_axis": False,
    }))
    # _CACHE_KEY.json (so the bundle export picks up seed + flags + input hash)
    (rd / "_CACHE_KEY.json").write_text(json.dumps({
        "input_hash": "sha256:test_input_hash",
        "seed": 42,
        "flags": ["--also-math", "--deep-math", "--also-llm"],
        "cache_key": "sha256:test_cache_key",
        "created_at": "2026-05-03T00:00:00Z",
    }))
    # motif.json + report.json (empty but valid)
    (rd / "motif.json").write_text(json.dumps({"version": "motif_v1", "motifs": [], "summary": {}}))
    (rd / "report.json").write_text(json.dumps({
        "ok": True, "plan_id": "test", "score": {"run_quality": 0.5, "confidence": 0.8},
        "events": [], "results": [], "artifacts_present": [],
        "run_dir": str(rd), "inputs": {},
    }))
    return rd


print("=== export ===")

def e1():
    with tempfile.TemporaryDirectory() as d:
        rd = _write_minimal_run(Path(d))
        out = export_findings_markdown(rd)
        assert out.exists()
        text = out.read_text(encoding="utf-8")
        # Must have findings header + at least one finding section.
        assert "# Aurora Findings" in text
        assert "## Findings" in text or "## Top anomalies" in text
        # Must mention the actual variable names from the synthetic data.
        assert "precip_mm" in text or "temperature_c" in text
run("findings.md generated", e1)

def e2():
    with tempfile.TemporaryDirectory() as d:
        rd = _write_minimal_run(Path(d))
        out = export_methods_markdown(rd)
        assert out.exists()
        text = out.read_text(encoding="utf-8")
        # Methods doc should mention each statistical procedure used.
        assert "Isolation Forest" in text  # anomaly section
        assert "AR(1)" in text  # forecasting section
        assert "PELT" in text  # regime section
        assert "Aurora v" in text  # version stamp
run("Methods.md generated", e2)

def e3():
    with tempfile.TemporaryDirectory() as d:
        rd = _write_minimal_run(Path(d))
        out = export_reproducibility_bundle(rd)
        assert out.exists()
        bundle = json.loads(out.read_text(encoding="utf-8"))
        assert bundle["aurora_version"]
        assert bundle["bundle_format"] == "1.0"
        assert bundle["seed"] == 42
        assert bundle["input_data_hash"] == "sha256:test_input_hash"
        assert bundle["priors_set_hash"].startswith("sha256:") or bundle["priors_set_hash"] == "unknown"
        # artifact_hashes should include all the JSONs we wrote.
        assert "deep_math.json" in bundle["artifact_hashes"]
        assert "structure_contract.json" in bundle["artifact_hashes"]
        # The bundle file itself shouldn't be in its own hash list (no self-reference).
        assert "aurora.bundle.json" not in bundle["artifact_hashes"]
run("aurora.bundle.json generated", e3)

def e4():
    with tempfile.TemporaryDirectory() as d:
        rd = _write_minimal_run(Path(d))
        result = export_all(rd)
        assert "findings_md" in result
        assert "methods_md" in result
        assert "bundle_json" in result
        for path_str in result.values():
            assert Path(path_str).exists()
run("export_all wraps all three", e4)

def e5():
    """priors set hash is deterministic and stable."""
    h1 = _hash_priors_set()
    h2 = _hash_priors_set()
    assert h1 == h2
    assert h1.startswith("sha256:")
run("priors_set_hash deterministic", e5)


print("\n=== KB ===")

def k1():
    """Catalogue is at least 20 entries."""
    assert len(_CATALOGUE) >= 20
run("KB catalogue has 20+ entries", k1)

def k2():
    """Newton cooling prior_id matches finding."""
    finding = {
        "title": "Matches known law: Newton's law of cooling",
        "method": "physics_prior_matcher",
        "severity": "ok",
    }
    cit = match_finding_to_kb(finding)
    assert cit is not None
    assert "Newton" in cit["title"] or "Newton" in cit["authors"]
    assert cit["domain"] == "thermal"
run("Newton cooling finding gets Newton citation", k2)

def k3():
    """Anomaly finding gets some kind of anomaly-detection citation."""
    finding = {
        "title": "Anomaly in precip_mm at row 47",
        "method": "iso-forest + robust-Z",
        "severity": "crit",
    }
    cit = match_finding_to_kb(finding)
    assert cit is not None
    # Should be the isolation forest paper or anomaly survey.
    assert "Isolation" in cit["title"] or "Anomaly" in cit["title"]
run("anomaly finding gets anomaly citation", k3)

def k4():
    """Regime finding gets the PELT or HMM citation."""
    finding = {
        "title": "2 regimes detected; latest = LOW",
        "method": "ruptures_pelt_rbf",
        "severity": "info",
    }
    cit = match_finding_to_kb(finding)
    assert cit is not None
    assert "Killick" in cit["authors"] or "Hamilton" in cit["authors"] or "Truong" in cit["authors"]
run("regime finding gets regime-detection citation", k4)

def k5():
    """Causal finding gets Pearl or Hernán citation."""
    finding = {
        "title": "Causal effect: precip_mm increases by 1.0",
        "method": "WHAT_IF_LINEAR_OLS",
        "severity": "info",
    }
    cit = match_finding_to_kb(finding)
    assert cit is not None
    # OLS method substring matches Pearl/Hernán entries.
    assert "Pearl" in cit["authors"] or "Hernán" in cit["authors"]
run("causal finding gets causal citation", k5)

def k6():
    """Random uninstrumented title returns None gracefully."""
    finding = {"title": "Some unrelated weird thing", "method": "unknown_method"}
    cit = match_finding_to_kb(finding)
    assert cit is None
run("unmatched finding returns None", k6)

def k7():
    """Malformed input returns None, doesn't crash."""
    assert match_finding_to_kb(None) is None
    assert match_finding_to_kb({}) is None
    assert match_finding_to_kb("not a dict") is None
run("KB tolerates malformed input", k7)

def k8():
    sources = list_kb_sources()
    assert len(sources) >= 1
    for s in sources:
        assert "source" in s
        assert "entry_count" in s
        assert s["entry_count"] > 0
run("list_kb_sources returns metadata", k8)

print(f"\n=== {passed} passed, {failed} failed ===")
sys.exit(0 if failed == 0 else 1)
