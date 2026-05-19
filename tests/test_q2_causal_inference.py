"""
Q2.0 Stream A — Causal inference tests.

Covers DAG construction (cycle breaking, d-separation), backdoor-set
identification, do_query estimation accuracy on a known SCM, and
counterfactual queries.
"""
from __future__ import annotations

import warnings
import pytest


def _confounded_data(n=600, true_effect=0.4, seed=42):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    Z = rng.standard_normal(n)
    T = 0.7 * Z + rng.standard_normal(n) * 0.5
    Y = true_effect * T + 0.6 * Z + rng.standard_normal(n) * 0.4
    return pd.DataFrame({"Z": Z, "T": T, "Y": Y}), true_effect


def _sm_with_confounder():
    return {
        "nodes": [{"name": "Z"}, {"name": "T"}, {"name": "Y"}],
        "edges": [
            {"source": "Z", "target": "T", "weight": 0.9},
            {"source": "Z", "target": "Y", "weight": 0.9},
            {"source": "T", "target": "Y", "weight": 0.7},
        ],
    }


# ----------------------------------------------------------------------
# DAG primitives
# ----------------------------------------------------------------------

class TestCausalDAG:

    def test_load_dag_from_empty_system_model(self):
        from fantasyai.aurora.causal import load_dag_from_system_model
        dag = load_dag_from_system_model(None)
        assert len(dag.nodes) == 0
        dag = load_dag_from_system_model({})
        assert len(dag.nodes) == 0

    def test_load_dag_basic(self):
        from fantasyai.aurora.causal import load_dag_from_system_model
        dag = load_dag_from_system_model(_sm_with_confounder())
        assert dag.nodes == frozenset({"Z", "T", "Y"})
        assert dag.children("Z") == {"T", "Y"}
        assert dag.children("T") == {"Y"}
        assert dag.parents("Y") == {"Z", "T"}
        assert dag.is_acyclic()

    def test_cycle_breaking_drops_weakest_edge(self):
        from fantasyai.aurora.causal import (
            load_dag_from_system_model, DagCycleWarning,
        )
        sm = {
            "nodes": [{"name": "A"}, {"name": "B"}],
            "edges": [
                {"source": "A", "target": "B", "weight": 0.9},
                {"source": "B", "target": "A", "weight": 0.2},
            ],
        }
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always", DagCycleWarning)
            dag = load_dag_from_system_model(sm)
        assert dag.is_acyclic()
        # The weaker B→A edge should be the one dropped.
        assert ("B", "A") in dag.dropped_edges
        assert dag.children("A") == {"B"}

    def test_d_separation_for_confounder(self):
        from fantasyai.aurora.causal import load_dag_from_system_model
        dag = load_dag_from_system_model(_sm_with_confounder())
        # T and Y are NOT d-separated marginally.
        assert dag.d_separated("T", "Y") is False
        # Conditioning on Z does NOT d-separate (T has direct effect on Y).
        assert dag.d_separated("T", "Y", given={"Z"}) is False
        # But Z d-separates T from itself's own ancestors via collider-blocking:
        # actually, Z is d-connected to T marginally.
        assert dag.d_separated("Z", "T") is False


# ----------------------------------------------------------------------
# Backdoor identification
# ----------------------------------------------------------------------

class TestBackdoor:

    def test_backdoor_set_finds_confounder(self):
        from fantasyai.aurora.causal import (
            load_dag_from_system_model, backdoor_set,
        )
        dag = load_dag_from_system_model(_sm_with_confounder())
        z = backdoor_set(dag, "T", "Y")
        assert z == {"Z"}

    def test_backdoor_set_returns_none_when_unknown_node(self):
        from fantasyai.aurora.causal import (
            load_dag_from_system_model, backdoor_set,
        )
        dag = load_dag_from_system_model(_sm_with_confounder())
        assert backdoor_set(dag, "missing", "Y") is None
        assert backdoor_set(dag, "T", "ghost") is None

    def test_backdoor_set_empty_when_no_parents(self):
        from fantasyai.aurora.causal import (
            load_dag_from_system_model, backdoor_set,
        )
        # T has no parents → empty adjustment set is admissible.
        sm = {
            "nodes": [{"name": "T"}, {"name": "Y"}],
            "edges": [{"source": "T", "target": "Y", "weight": 0.9}],
        }
        dag = load_dag_from_system_model(sm)
        z = backdoor_set(dag, "T", "Y")
        assert z == set()


# ----------------------------------------------------------------------
# do_query estimation
# ----------------------------------------------------------------------

class TestDoQuery:

    def test_recovers_true_effect_with_backdoor_adjustment(self):
        from fantasyai.aurora.causal import (
            load_dag_from_system_model, do_query,
        )
        df, true_eff = _confounded_data(n=800, true_effect=0.4)
        dag = load_dag_from_system_model(_sm_with_confounder())
        res = do_query(df, dag, "T", "Y", intervention_value=1.0)
        assert res.ok is True
        assert res.identifiable is True
        assert sorted(res.adjustment_set) == ["Z"]
        # Within ~0.1 of the true effect (4× standard error).
        assert abs(res.effect_estimate - true_eff) < 0.1, (
            f"recovered {res.effect_estimate:.3f} vs true {true_eff}"
        )
        assert res.n_obs == 800
        assert res.standard_error is not None

    def test_unidentifiable_when_treatment_missing_from_dag(self):
        from fantasyai.aurora.causal import (
            load_dag_from_system_model, do_query,
        )
        import pandas as pd
        df = pd.DataFrame({"X": [1, 2, 3], "Y": [1, 2, 3]})
        dag = load_dag_from_system_model({
            "nodes": [{"name": "A"}], "edges": [],
        })
        res = do_query(df, dag, "X", "Y")
        assert res.ok is False
        assert "not in DAG" in (res.unidentifiable_reason or "")

    def test_insufficient_data_returns_error(self):
        from fantasyai.aurora.causal import (
            load_dag_from_system_model, do_query,
        )
        import pandas as pd
        df = pd.DataFrame({"Z": [1, 2], "T": [3, 4], "Y": [5, 6]})  # n=2
        dag = load_dag_from_system_model(_sm_with_confounder())
        res = do_query(df, dag, "T", "Y")
        assert res.ok is False
        assert "complete-case rows" in (res.unidentifiable_reason or "")


# ----------------------------------------------------------------------
# Counterfactual
# ----------------------------------------------------------------------

class TestCounterfactual:

    def test_counterfactual_outcome_uses_linear_recipe(self):
        from fantasyai.aurora.causal import (
            load_dag_from_system_model, counterfactual_query,
        )
        df, _ = _confounded_data(n=500, true_effect=0.4)
        dag = load_dag_from_system_model(_sm_with_confounder())
        # Pick a specific row; counterfactual T = mean + 1.
        row_idx = 10
        T_obs = float(df.iloc[row_idx]["T"])
        cf = counterfactual_query(df, dag, "T", "Y",
                                   counterfactual_treatment=T_obs + 1.0,
                                   row_index=row_idx)
        assert cf.ok is True
        assert cf.observed_treatment == pytest.approx(T_obs, rel=1e-6)
        assert cf.counterfactual_outcome is not None
        # Delta ≈ β * 1.0 ≈ 0.4 (recovered effect).
        assert abs(cf.delta - 0.4) < 0.15

    def test_counterfactual_falls_back_to_mean_row(self):
        from fantasyai.aurora.causal import (
            load_dag_from_system_model, counterfactual_query,
        )
        df, _ = _confounded_data(n=300, true_effect=0.4)
        dag = load_dag_from_system_model(_sm_with_confounder())
        cf = counterfactual_query(df, dag, "T", "Y",
                                   counterfactual_treatment=2.0)
        assert cf.ok is True
        assert cf.observed_treatment == pytest.approx(
            float(df["T"].mean()), rel=1e-6,
        )
