"""
Regression guard for the v0.10.2 time-axis-vs-id-like fix.

The bug: ``_infer_col_role`` first set `role="datetime"` for a column
whose name contained "date"/"time"/etc. and whose values parsed as
datetimes. Then a later block overrode `role="id_like"` whenever the
column had near-unique values — which is exactly what a properly-
behaved time column looks like (one timestamp per row).

Downstream effect: the structure contract's ``id_like_cols`` listed
the date column; methods that filter on ``role == "datetime"`` missed
it; the Studio cube banner read "no time axis detected" even though
the rescue path in ``_pick_best_time_column`` saved the
``time.best_time_col`` field.

The fix protects ``role="datetime"`` from the id_like override.
This test pins the protection.
"""
from __future__ import annotations

from pathlib import Path
import runpy

import pandas as pd
import pytest


def _load_runner_module():
    """Load the runner script as a module — its dataclasses need a
    real module context to work, so we use runpy.run_path."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_aurora_dataset_runner.py"
    return runpy.run_path(str(script), run_name="runner_mod_test")


@pytest.fixture(scope="module")
def runner():
    return _load_runner_module()


# ----------------------------------------------------------------------
# A column with a datetime name + every-row-unique values must keep
# role="datetime" — NOT be reclassified as id_like.
# ----------------------------------------------------------------------

class TestDatetimeRoleProtection:

    def test_unique_per_row_date_column_keeps_datetime_role(self, runner):
        """A date column where every row is a different day (uniq_ratio=1)
        should still come back as role=datetime — the canonical NBA
        game-log shape that triggered the bug."""
        _infer_col_role = runner["_infer_col_role"]
        df = pd.DataFrame({
            "GAME_DATE": pd.date_range("2022-10-18", periods=200, freq="D")
                            .strftime("%Y-%m-%d").tolist(),
            "PTS":       [25] * 200,
        })
        p = _infer_col_role(df, "GAME_DATE")
        assert p["role"] == "datetime", (
            f"unique-per-row date column should be role=datetime, "
            f"got {p['role']!r} (hints={p['hints']})"
        )
        assert "id_like" not in p["hints"]

    def test_unique_per_row_timestamp_column_keeps_datetime_role(self, runner):
        """Same protection for a column named 'timestamp' with ISO values."""
        _infer_col_role = runner["_infer_col_role"]
        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=100, freq="h")
                            .strftime("%Y-%m-%dT%H:%M:%S").tolist(),
            "value":     [1.0] * 100,
        })
        p = _infer_col_role(df, "timestamp")
        assert p["role"] == "datetime"

    def test_actual_id_column_still_classified_as_id_like(self, runner):
        """Don't over-correct: a genuine id column (no datetime parse
        rate) still gets role=id_like."""
        _infer_col_role = runner["_infer_col_role"]
        df = pd.DataFrame({
            "user_id": [f"u-{i:06d}" for i in range(200)],
            "value":   [1.0] * 200,
        })
        p = _infer_col_role(df, "user_id")
        # Should be id_like via name pattern (_id suffix) + uniqueness.
        assert p["role"] == "id_like"

    def test_unique_numeric_id_still_classified_as_id_like(self, runner):
        """A numeric column with monotonic-near-unique values + an
        id-like name still falls through to id_like."""
        _infer_col_role = runner["_infer_col_role"]
        df = pd.DataFrame({
            "incident_id": list(range(1000, 1200)),
            "value":       [1.0] * 200,
        })
        p = _infer_col_role(df, "incident_id")
        assert p["role"] == "id_like"


# ----------------------------------------------------------------------
# End-to-end: the structure contract correctly surfaces the time axis.
# ----------------------------------------------------------------------

class TestStructureContractEndToEnd:

    def test_curry_shape_dataframe_picks_game_date(self, runner):
        """The exact dataset shape from fetch_curry_data.py: 200 rows,
        GAME_DATE + numeric stats. Was the original repro."""
        build_structure_contract = runner["build_structure_contract"]
        n = 200
        df = pd.DataFrame({
            "GAME_DATE":  pd.date_range("2022-10-18", periods=n, freq="D").strftime("%Y-%m-%d"),
            "PTS":        list(range(20, 20 + n)),
            "FG_PCT":     [0.45 + (i % 10) * 0.01 for i in range(n)],
            "FG3_PCT":    [0.40 + (i % 7) * 0.01 for i in range(n)],
            "MIN":        [32 + (i % 5) for i in range(n)],
            "PLUS_MINUS": [(-10 if i % 3 == 0 else 8) for i in range(n)],
        })
        c = build_structure_contract(df, dataset_key="curry_test")

        # The time column is correctly identified.
        assert c["time"]["best_time_col"] == "GAME_DATE"
        # The contract flags time-series eligibility.
        assert c["eligible"]["time_series_eligible"] is True
        # GAME_DATE is NOT in id_like_cols (the bug we fixed).
        assert "GAME_DATE" not in c["id_like_cols"]
        # GAME_DATE column profile shows role=datetime.
        game_date_profile = next(
            (p for p in c["columns"] if p["name"] == "GAME_DATE"), None
        )
        assert game_date_profile is not None
        assert game_date_profile["role"] == "datetime"

    def test_main_feeds_structure_contract_a_time_aware_view(self):
        """The runner's _main() must pass a frame INCLUDING the date
        column to build_structure_contract. Earlier versions fed
        df_numeric (numeric-only view) which silently stripped the
        time column — the structure contract then had time.best_time_col=None
        and the Studio cube displayed "no valid time axis" even though
        a real datetime column existed.

        We grep the script source for the right pattern rather than
        running _main (which spawns subprocesses + needs many deps).
        """
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1]
               / "scripts" / "run_aurora_dataset_runner.py").read_text(encoding="utf-8")
        # The fix: build_structure_contract receives a time-aware frame.
        assert "df_for_structure" in src, (
            "_main should compute a separate df_for_structure that "
            "preserves datetime columns for build_structure_contract"
        )
        # Confirm the dangerous prior pattern (feeding df_for_engine,
        # which is df_numeric) is no longer the call argument to
        # build_structure_contract.
        # Find the build_structure_contract call site.
        bsc_call_lines = [
            line for line in src.splitlines()
            if "build_structure_contract(" in line and "def " not in line
        ]
        assert bsc_call_lines, "build_structure_contract call site not found"
        # The call should be: build_structure_contract(df_for_structure, ...)
        assert any("build_structure_contract(df_for_structure" in line
                    for line in bsc_call_lines), (
            f"build_structure_contract should be called with df_for_structure; "
            f"found: {bsc_call_lines}"
        )

    def test_numeric_cols_excludes_time_column(self, runner):
        """numeric_cols should NOT include the date column even when
        the date column happens to be parseable as numeric (epoch /
        year etc.) — the role check filters it out."""
        build_structure_contract = runner["build_structure_contract"]
        # Use a value column with REPEATED values so uniq_ratio is low
        # enough that it doesn't get flipped to id_like by the
        # high-uniqueness override (which is the correct fallback for
        # non-datetime monotonic-unique columns).
        df = pd.DataFrame({
            "date":   pd.date_range("2024-01-01", periods=200, freq="D").strftime("%Y-%m-%d"),
            "amount": [round((i % 10) * 1.5, 2) for i in range(200)],
        })
        c = build_structure_contract(df)
        assert "date" not in c["numeric_cols"], (
            "date column should not appear in numeric_cols"
        )
        assert "amount" in c["numeric_cols"]
