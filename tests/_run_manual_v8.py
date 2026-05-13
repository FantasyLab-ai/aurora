"""Manual runner for v8 tests (connector framework + cache).

Network-dependent tests are kept minimal and use safe defaults; the framework
itself is tested with mocked subclasses to keep the suite runnable offline.
"""
import sys, json, tempfile, time, hashlib
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fantasyai.aurora.connectors import (
    list_connectors, get_connector, BaseConnector,
    ConnectorResult, ConnectorSearchResult,
)
from fantasyai.aurora.connectors.base import (
    FetchPayload, hash_bytes, _cache_path_for, _provenance_path,
)
from fantasyai.aurora.connectors.usgs_earthquake import _build_query
from fantasyai.aurora.connectors.openml import _parse_arff


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


print("=== registry ===")

def r1():
    """All 3 connectors registered with valid metadata."""
    conns = list_connectors()
    assert len(conns) == 3
    ids = {c["id"] for c in conns}
    assert ids == {"world_bank", "usgs_earthquake", "openml"}
run("3 connectors registered", r1)

def r2():
    """get_connector returns instances."""
    wb = get_connector("world_bank")
    assert wb is not None
    assert wb.id == "world_bank"
    assert wb.requires_api_key is False
    assert get_connector("nonexistent") is None
run("get_connector by id", r2)

def r3():
    """All v1 connectors are keyless (the whole point)."""
    for c in list_connectors():
        assert c["requires_api_key"] is False, f"{c['id']} should be keyless in v1"
        assert c["has_api_key"] is True, f"{c['id']} should report has_api_key=True"
run("v1 connectors are keyless", r3)


print("\n=== caching ===")

class _FakeConnector(BaseConnector):
    """Subclass for testing the cache layer without network calls."""
    id = "fake_test"
    display_name = "Fake test"
    domain = "research"
    requires_api_key = False
    cache_ttl_days = 7

    def __init__(self):
        self.fetch_count = 0

    def search(self, query, limit=10):
        return [ConnectorSearchResult(
            item_id="abc", title="fake", description="x",
            rows_estimate=10, cols_estimate=2, domain="research", source=self.id,
        )]

    def _do_fetch(self, item_id):
        self.fetch_count += 1
        body = b"col1,col2\n1,2\n3,4\n5,6\n"
        return FetchPayload(
            csv_bytes=body, rows=3, cols=2,
            response_hash=hash_bytes(body),
            extras={"item": item_id},
        )


def c1():
    """First fetch creates CSV + provenance sidecar."""
    fc = _FakeConnector()
    res = fc.fetch("test_item")
    assert res.cache_hit is False
    assert Path(res.csv_path).exists()
    prov_path = _provenance_path(Path(res.csv_path))
    assert prov_path.exists()
    prov = json.loads(prov_path.read_text())
    assert prov["source"] == "fake_test"
    assert prov["item_id"] == "test_item"
    assert prov["rows"] == 3
    # Cleanup
    Path(res.csv_path).unlink(missing_ok=True)
    prov_path.unlink(missing_ok=True)
run("fetch creates CSV + provenance", c1)


def c2():
    """Second identical fetch hits cache (no re-download)."""
    fc = _FakeConnector()
    r1 = fc.fetch("test_item_2")
    assert fc.fetch_count == 1
    r2 = fc.fetch("test_item_2")
    assert fc.fetch_count == 1   # didn't increment
    assert r2.cache_hit is True
    assert r2.csv_path == r1.csv_path
    assert r2.rows == r1.rows
    # Cleanup
    Path(r1.csv_path).unlink(missing_ok=True)
    _provenance_path(Path(r1.csv_path)).unlink(missing_ok=True)
run("second fetch is cached", c2)


def c3():
    """force=True bypasses cache."""
    fc = _FakeConnector()
    fc.fetch("test_item_3")
    fc.fetch("test_item_3", force=True)
    assert fc.fetch_count == 2
    # Cleanup
    p = _cache_path_for("fake_test", "test_item_3")
    p.unlink(missing_ok=True)
    _provenance_path(p).unlink(missing_ok=True)
run("force=True bypasses cache", c3)


print("\n=== USGS query parser ===")

def u1():
    """Default search adds a 30-day window."""
    p = _build_query("")
    assert "starttime" in p
run("default query has time window", u1)

def u2():
    """'mag>5' parses to minmagnitude=5."""
    p = _build_query("mag>5")
    assert p.get("minmagnitude") == 5.0
run("mag>5 parses", u2)

def u3():
    """'california mag>4' adds bounding box AND magnitude."""
    p = _build_query("california mag>4")
    assert p.get("minmagnitude") == 4.0
    assert "minlatitude" in p and "maxlatitude" in p
    assert p["minlatitude"] == 32 and p["maxlatitude"] == 42
run("california bbox + magnitude", u3)

def u4():
    """'year:2024 japan' sets time window AND japan bbox."""
    p = _build_query("year:2024 japan")
    assert p.get("starttime", "").startswith("2024")
    assert p.get("endtime", "").startswith("2024")
    assert p.get("minlatitude") == 24
run("year:2024 japan", u4)


print("\n=== OpenML ARFF parser ===")

def o1():
    arff = """% A comment
@RELATION test
@ATTRIBUTE name STRING
@ATTRIBUTE age NUMERIC
@ATTRIBUTE class {a, b, c}
@DATA
'Alice', 30, a
'Bob', 25, b
'Carol', 40, c
"""
    headers, rows = _parse_arff(arff)
    assert headers == ["name", "age", "class"]
    assert len(rows) == 3
    assert rows[0][0] in ("Alice", "'Alice'")
run("ARFF parser handles basic format", o1)

def o2():
    """Empty / commented ARFF returns empty rows."""
    headers, rows = _parse_arff("% just comments\n@RELATION test\n@DATA\n")
    assert rows == []
run("ARFF parser handles empty data", o2)


print("\n=== world_bank default search ===")

def wb1():
    """No query → curated default indicators (no network call needed)."""
    wb = get_connector("world_bank")
    results = wb.search("")
    assert len(results) > 0
    # First result should be GDP (the canonical default).
    assert any("GDP" in r.title for r in results)
run("world_bank default returns curated list", wb1)


print("\n=== openml default search ===")

def om1():
    """No query → curated default datasets (no network call needed)."""
    om = get_connector("openml")
    results = om.search("")
    assert len(results) > 0
    # Iris should be in the default list.
    assert any(r.title == "iris" for r in results)
run("openml default returns curated list", om1)


print(f"\n=== {passed} passed, {failed} failed ===")
sys.exit(0 if failed == 0 else 1)
