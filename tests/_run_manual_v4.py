"""Manual runner for v4 tests (artifact cache + domain modes)."""
import sys, json, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fantasyai.aurora.cache import (
    CacheKey, compute_cache_key, write_cache_key, find_cached_run,
    normalize_flags, list_cache_entries, CACHE_KEY_FILENAME,
)
from fantasyai.aurora.domain_config import (
    list_domains, get_domain_config, apply_domain_to_state,
    GENERAL, ENVIRO, RESEARCH, OPS, INDUSTRIAL, FINANCE,
)

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

print("=== artifact cache ===")

def c1():
    """normalize_flags returns a sorted tuple."""
    flags = normalize_flags(also_math=True, math_v2=True, deep_math=True,
                            also_llm=False, also_motif=True, also_orchestrator=False)
    assert isinstance(flags, tuple)
    assert flags == tuple(sorted(flags))
    assert "--also-math" in flags and "--math-v2" in flags
    assert "--also-llm" not in flags
run("normalize_flags is sorted tuple", c1)

def c2():
    """Same inputs → same hash."""
    k1 = CacheKey(input_hash="sha256:abc", seed=42, flags=("--also-math", "--deep-math"))
    k2 = CacheKey(input_hash="sha256:abc", seed=42, flags=("--also-math", "--deep-math"))
    assert k1.compute_hash() == k2.compute_hash()
run("same inputs → same hash", c2)

def c3():
    """Different seed → different hash."""
    k1 = CacheKey(input_hash="sha256:abc", seed=42, flags=("--deep-math",))
    k2 = CacheKey(input_hash="sha256:abc", seed=43, flags=("--deep-math",))
    assert k1.compute_hash() != k2.compute_hash()
run("different seed → different hash", c3)

def c4():
    """Different flags → different hash."""
    k1 = CacheKey(input_hash="sha256:abc", seed=42, flags=("--deep-math",))
    k2 = CacheKey(input_hash="sha256:abc", seed=42, flags=("--deep-math", "--also-llm"))
    assert k1.compute_hash() != k2.compute_hash()
run("different flags → different hash", c4)

def c5():
    """compute_cache_key handles a real file."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "data.csv"
        p.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
        k = compute_cache_key(p, seed=42, flags=("--deep-math",))
        assert k.input_hash.startswith("sha256:")
        # Same file → same hash; modified → different hash
        k2 = compute_cache_key(p, seed=42, flags=("--deep-math",))
        assert k.compute_hash() == k2.compute_hash()
        p.write_text("a,b\n5,6\n", encoding="utf-8")
        k3 = compute_cache_key(p, seed=42, flags=("--deep-math",))
        assert k.input_hash != k3.input_hash
run("compute_cache_key from real file", c5)

def c6():
    """write_cache_key + find_cached_run roundtrip."""
    with tempfile.TemporaryDirectory() as d:
        outputs = Path(d)
        run_dir = outputs / "20260502_run_a"
        run_dir.mkdir()
        key = CacheKey(input_hash="sha256:abc", seed=42, flags=("--deep-math",))
        write_cache_key(run_dir, key)
        assert (run_dir / CACHE_KEY_FILENAME).exists()
        # Find by same key
        found = find_cached_run(outputs, key)
        assert found == run_dir
        # Different key → not found
        other = CacheKey(input_hash="sha256:def", seed=42, flags=("--deep-math",))
        assert find_cached_run(outputs, other) is None
run("write + find cache roundtrip", c6)

def c7():
    """find_cached_run picks the most recent matching dir."""
    with tempfile.TemporaryDirectory() as d:
        outputs = Path(d)
        key = CacheKey(input_hash="sha256:abc", seed=42, flags=())
        # Create two run dirs with the same key, different mtimes.
        old_dir = outputs / "20260101_old"
        old_dir.mkdir()
        write_cache_key(old_dir, key)
        # Set old mtime
        old_time = time.time() - 86400
        import os
        os.utime(old_dir / CACHE_KEY_FILENAME, (old_time, old_time))
        os.utime(old_dir, (old_time, old_time))

        new_dir = outputs / "20260102_new"
        new_dir.mkdir()
        write_cache_key(new_dir, key)

        found = find_cached_run(outputs, key)
        assert found == new_dir, f"Expected {new_dir}, got {found}"
run("find_cached_run prefers newest", c7)

def c8():
    """find_cached_run skips run dirs older than max_age_days."""
    with tempfile.TemporaryDirectory() as d:
        outputs = Path(d)
        key = CacheKey(input_hash="sha256:abc", seed=42, flags=())
        run_dir = outputs / "ancient"
        run_dir.mkdir()
        write_cache_key(run_dir, key)
        # Age it out (60 days old).
        old_time = time.time() - 60 * 86400
        import os
        os.utime(run_dir / CACHE_KEY_FILENAME, (old_time, old_time))
        os.utime(run_dir, (old_time, old_time))
        # max_age_days=30 should exclude it.
        assert find_cached_run(outputs, key, max_age_days=30) is None
        # max_age_days=120 should find it.
        assert find_cached_run(outputs, key, max_age_days=120) == run_dir
run("find_cached_run respects max_age_days", c8)

def c9():
    """list_cache_entries lists all cached runs newest first."""
    with tempfile.TemporaryDirectory() as d:
        outputs = Path(d)
        for i, h in enumerate(("a", "b", "c")):
            rd = outputs / f"run_{i}"
            rd.mkdir()
            write_cache_key(rd, CacheKey(input_hash=f"sha256:{h}", seed=42, flags=()))
        entries = list_cache_entries(outputs)
        assert len(entries) == 3
        for e in entries:
            assert "cache_key" in e
            assert "run_dir" in e
run("list_cache_entries all runs", c9)

print("\n=== domain modes ===")

def d1():
    domains = list_domains()
    ids = {d["id"] for d in domains}
    assert {"general", "enviro", "research", "ops", "industrial", "finance", "custom"}.issubset(ids)
run("7 domains registered", d1)

def d2():
    cfg = get_domain_config("ops")
    assert cfg.id == "ops"
    assert cfg.terminology["Anomaly"] == "Incident"
    cfg = get_domain_config(None)
    assert cfg.id == "general"
    cfg = get_domain_config("UNKNOWN")
    assert cfg.id == "general"  # unknown → general
run("get_domain_config defaults to general", d2)

def d3():
    """Apply ops domain — anomaly → incident in finding titles."""
    state = {
        "findings": [
            {"title": "Anomaly in precip_mm at row 47", "description": "z=10.9"},
        ],
        "anomalies": [{"description": "Anomaly detected on precip_mm"}],
        "regimes": [{"label": "STABLE"}],
        "physics": {"prior_matches": [
            {"prior_id": "newton_cooling", "domain": "thermal"},
            {"prior_id": "rlc_circuit", "domain": "industrial"},
        ]},
        "copilot": {"suggestions": ["Existing Q1", "Existing Q2"]},
    }
    apply_domain_to_state(state, "ops")
    assert "Incident" in state["findings"][0]["title"]
    assert state["domain"]["id"] == "ops"
run("ops domain rewrites Anomaly → Incident", d3)

def d4():
    """Industrial domain reorders prior_matches so industrial domain priors are first."""
    state = {
        "physics": {"prior_matches": [
            {"prior_id": "newton_cooling", "domain": "thermal"},
            {"prior_id": "rlc_circuit", "domain": "industrial"},
            {"prior_id": "exponential", "domain": "research"},
        ]},
    }
    apply_domain_to_state(state, "industrial")
    matches = state["physics"]["prior_matches"]
    # Industrial domain should be reordered to the front.
    assert matches[0]["domain"] == "industrial"
run("industrial domain reorders prior_matches", d4)

def d5():
    """General domain leaves text untouched but stamps state.domain."""
    state = {
        "findings": [{"title": "Anomaly in x", "description": "z=5"}],
        "regimes": [{"label": "STABLE"}],
    }
    apply_domain_to_state(state, "general")
    assert state["findings"][0]["title"] == "Anomaly in x"  # unchanged
    assert state["regimes"][0]["label"] == "STABLE"  # unchanged
    assert state["domain"]["id"] == "general"
run("general domain leaves text untouched", d5)

def d6():
    """Domain suggested_questions get merged with existing copilot suggestions."""
    state = {
        "copilot": {"suggestions": ["Existing Q1", "Existing Q2", "Existing Q3", "Existing Q4"]},
    }
    apply_domain_to_state(state, "research")
    sugs = state["copilot"]["suggestions"]
    # Existing first 2 + research domain canned questions, capped at 5.
    assert len(sugs) <= 5
    assert sugs[0] == "Existing Q1"
    assert sugs[1] == "Existing Q2"
    # Research suggestions should appear.
    assert any("multiple-comparison" in s.lower() or "Michaelis" in s for s in sugs)
run("domain suggestions merge with existing", d6)

def d7():
    """Enviro domain rewrites regime → phase in finding titles."""
    state = {
        "findings": [
            {"title": "2 regimes detected; latest = LOW", "description": "regime shift"},
        ],
    }
    apply_domain_to_state(state, "enviro")
    # 'regime' → 'phase' in the description (lowercase rewrite)
    assert "phase shift" in state["findings"][0]["description"]
run("enviro rewrites regime → phase", d7)

def d8():
    """apply_domain_to_state never raises on missing keys."""
    apply_domain_to_state({}, "ops")  # empty state
    apply_domain_to_state({"findings": "not a list"}, "ops")  # malformed
    apply_domain_to_state({"physics": None}, "ops")  # null nested
run("never raises on malformed state", d8)

print(f"\n=== {passed} passed, {failed} failed ===")
sys.exit(0 if failed == 0 else 1)
