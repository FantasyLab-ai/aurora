"""
Atom 2.2: Network-shape detection in shape_detector.

Pinned to three real public-data fixtures:
  - NYC TLC trip-style: pu_location_id + do_location_id (edge list, signature a)
  - BTS T-100 air-segment-style: origin + destination
  - Explicit edge column: "edge" with "from->to" values (signature b)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")


# ============================================================
# Fixture builders
# ============================================================

def _write_csv(tmp_path: Path, df: pd.DataFrame) -> Path:
    p = tmp_path / "edges.csv"
    df.to_csv(p, index=False)
    return p


# ============================================================
# Edge-list signature (a): NYC TLC style
# ============================================================

class TestNYCTaxiEdgeList:

    def test_pickup_dropoff_columns_match_network(self, tmp_path):
        from fantasyai.aurora.sampling import infer_structure
        n = 600
        rng = np.random.RandomState(0)
        df = pd.DataFrame({
            "trip_id":          range(n),
            "pu_location_id":   rng.randint(1, 65, size=n),
            "do_location_id":   rng.randint(1, 65, size=n),
            "fare_amount":      rng.uniform(5, 50, size=n),
            "trip_distance":    rng.uniform(0.5, 12, size=n),
        })
        # Make pu/do categorical-ish: zone IDs as strings.
        df["pu_location_id"] = df["pu_location_id"].astype(str)
        df["do_location_id"] = df["do_location_id"].astype(str)
        # Force smaller threshold so 600 rows still triggers shape detection.
        from fantasyai.aurora.sampling import shape_detector
        orig = shape_detector.SMALL_DATASET_ROW_THRESHOLD
        shape_detector.SMALL_DATASET_ROW_THRESHOLD = 100
        try:
            csv = _write_csv(tmp_path, df)
            profile = infer_structure(csv)
        finally:
            shape_detector.SMALL_DATASET_ROW_THRESHOLD = orig
        assert profile.shape == "network", \
            f"NYC TLC style should be network; got {profile.shape}"


# ============================================================
# Edge-list signature (a): BTS T-100 air-segment style
# ============================================================

class TestBTSAirSegments:

    def test_origin_destination_match_network(self, tmp_path):
        from fantasyai.aurora.sampling import infer_structure
        n = 800
        rng = np.random.RandomState(1)
        airports = ["ATL", "ORD", "DFW", "DEN", "JFK", "LAX", "LGA", "MIA",
                    "SEA", "SFO", "PHX", "BOS"]
        df = pd.DataFrame({
            "year":        rng.choice([2022, 2023, 2024], size=n),
            "month":       rng.choice(range(1, 13), size=n),
            "origin":      rng.choice(airports, size=n),
            "destination": rng.choice(airports, size=n),
            "passengers":  rng.randint(50, 5000, size=n),
            "freight_kg":  rng.uniform(0, 30000, size=n),
        })
        from fantasyai.aurora.sampling import shape_detector
        orig = shape_detector.SMALL_DATASET_ROW_THRESHOLD
        shape_detector.SMALL_DATASET_ROW_THRESHOLD = 100
        try:
            csv = _write_csv(tmp_path, df)
            profile = infer_structure(csv)
        finally:
            shape_detector.SMALL_DATASET_ROW_THRESHOLD = orig
        assert profile.shape == "network"


# ============================================================
# Explicit edge column (signature b)
# ============================================================

class TestExplicitEdgeColumn:

    def test_edge_column_with_arrow_values(self, tmp_path):
        from fantasyai.aurora.sampling import infer_structure
        n = 500
        rng = np.random.RandomState(2)
        nodes = [f"N{i}" for i in range(20)]
        edges = [f"{rng.choice(nodes)}->{rng.choice(nodes)}" for _ in range(n)]
        df = pd.DataFrame({
            "edge":   edges,
            "weight": rng.uniform(0.1, 10, size=n),
            "ts":     pd.date_range("2024-01-01", periods=n, freq="h"),
        })
        from fantasyai.aurora.sampling import shape_detector
        orig = shape_detector.SMALL_DATASET_ROW_THRESHOLD
        shape_detector.SMALL_DATASET_ROW_THRESHOLD = 100
        try:
            csv = _write_csv(tmp_path, df)
            profile = infer_structure(csv)
        finally:
            shape_detector.SMALL_DATASET_ROW_THRESHOLD = orig
        assert profile.shape == "network"


# ============================================================
# Negative cases — must NOT trigger network
# ============================================================

class TestNegativeCases:

    def test_pure_time_series_not_network(self, tmp_path):
        from fantasyai.aurora.sampling import infer_structure
        n = 600
        df = pd.DataFrame({
            "ts":          pd.date_range("2024-01-01", periods=n, freq="h"),
            "temperature": np.random.RandomState(0).normal(20, 3, n),
            "humidity":    np.random.RandomState(1).uniform(40, 80, n),
        })
        from fantasyai.aurora.sampling import shape_detector
        orig = shape_detector.SMALL_DATASET_ROW_THRESHOLD
        shape_detector.SMALL_DATASET_ROW_THRESHOLD = 100
        try:
            csv = _write_csv(tmp_path, df)
            profile = infer_structure(csv)
        finally:
            shape_detector.SMALL_DATASET_ROW_THRESHOLD = orig
        assert profile.shape != "network"

    def test_panel_with_only_one_id_col_not_network(self, tmp_path):
        from fantasyai.aurora.sampling import infer_structure
        n = 600
        rng = np.random.RandomState(3)
        df = pd.DataFrame({
            "ts":         pd.date_range("2024-01-01", periods=n, freq="h"),
            "station_id": rng.choice(["KPHL", "KJFK", "KLAX"], size=n),
            "temp":       rng.normal(20, 3, n),
        })
        from fantasyai.aurora.sampling import shape_detector
        orig = shape_detector.SMALL_DATASET_ROW_THRESHOLD
        shape_detector.SMALL_DATASET_ROW_THRESHOLD = 100
        try:
            csv = _write_csv(tmp_path, df)
            profile = infer_structure(csv)
        finally:
            shape_detector.SMALL_DATASET_ROW_THRESHOLD = orig
        # Single id column + time = panel, not network.
        assert profile.shape != "network"


# ============================================================
# Sampler dispatch
# ============================================================

class TestNetworkSamplerDispatch:

    def test_network_routes_to_random_iid_for_now(self):
        from fantasyai.aurora.sampling import select_sampling_method
        # Edge-aware sampler is a future-WS item; for v1 we route network
        # to random_iid which preserves edge-frequency distribution.
        assert select_sampling_method("network") == "random_iid"

    def test_unknown_shape_still_falls_back(self):
        from fantasyai.aurora.sampling import select_sampling_method
        assert select_sampling_method("brand_new_shape") == "random_iid"
