# Causal Inference — do-calculus + counterfactual queries

Aurora's v1 pipeline detects causal **relationships** (Granger,
mutual information, multivariate FE) and surfaces them on the
system graph. v2.0 adds the **interventional** layer: what happens
to Y if we set X=x by fiat, instead of just observing X correlating
with Y?

This is **observational** causal inference — Pearl's do-calculus
applied to the DAG Aurora already builds from your data.

## Quickstart

In the Studio, click the `v2.0` chip in the top toolbar → **Causal**
tab. Fill in:

- **treatment column** — the variable you'd intervene on
- **outcome column** — the variable you care about
- **intervention value** (optional) — the value of X under do()

Click **identify only** for a cheap "is this effect even
identifiable from my DAG?" answer, or **estimate do()** for the
full backdoor-adjusted OLS estimate, or **counterfactual** for
"what would Y have been if X had been x' instead?"

## What runs under the hood

```python
from fantasyai.aurora.causal import (
    load_dag_from_system_model,
    do_query, counterfactual_query, backdoor_set,
    identification_status,
)
```

### DAG construction

`load_dag_from_system_model(state["system_model"])` builds a
`CausalDAG`: nodes = your dataset columns, edges = directions Aurora
discovered. Cycles get broken by dropping the weakest-confidence
edge with a `DagCycleWarning`.

### Backdoor identification

`backdoor_set(dag, "T", "Y")` returns a minimal adjustment set Z
that:
1. Contains no descendant of T (no post-treatment variable contamination).
2. Blocks every backdoor path from T to Y (no open confounder paths).

The heuristic prefers `parents(T)` then expands to `ancestors(T) - descendants(T)`. If neither works, it returns `None` — the effect is **not** identifiable from the observed DAG. The Studio surfaces this honestly rather than returning a fake number.

### do() estimation

`do_query(df, dag, "T", "Y", intervention_value=x)` fits an OLS
adjustment regression `Y ~ T + Z` for adjustment set Z. The
coefficient on T is the causal effect estimate. We use pure numpy
normal-equations OLS (no statsmodels dep).

Returned `DoResult`:

```python
{
  "ok":               True,
  "identifiable":     True,
  "treatment":        "T",
  "outcome":          "Y",
  "adjustment_set":   ["Z"],
  "effect_estimate":  0.412,
  "standard_error":   0.045,
  "n_obs":            800,
  "method":           "backdoor_adjustment_ols",
  "assumptions":      [
    "Causal Markov + faithfulness on the system_model DAG",
    "No unmeasured confounders beyond adjustment set",
    "Linear additive effect of treatment given adjustments",
  ],
}
```

When `intervention_value` is supplied, the Studio also predicts the
counterfactual outcome at that value (holding adjustments at their
sample mean).

### Counterfactual queries

`counterfactual_query(df, dag, "T", "Y", counterfactual_treatment=x')`
estimates "what would Y have been at this row if T had been x'?"
Linear SCM recipe: `Y_cf = Y_obs + β(x' - T_obs)`.

You can target a specific row (`row_index=N`) or contrast against
the sample mean (default).

## Endpoints

| Method | Path | What it does |
|---|---|---|
| `GET`  | `/api/causal/dag`            | The current run's DAG (nodes + edges + cycles broken) |
| `GET`  | `/api/causal/identify`       | Identifiability check — cheap, no estimation |
| `POST` | `/api/causal/do`             | Full do() estimation + optional predicted outcome |
| `POST` | `/api/causal/counterfactual` | Counterfactual outcome contrast |

Multi-tenant deployments: all endpoints honour the workspace context
from the Stream 2.4 Phase 2 auth middleware.

## When the effect is NOT identifiable

The Studio explicitly says so. Common reasons:

- Treatment or outcome isn't in the system_model DAG yet (run Aurora
  on the dataset first).
- The DAG has unmeasured confounders the relationships pipeline
  couldn't infer. Add them as edges in a hand-curated overlay (the
  composable-findings flow lets you inherit a richer prior from a
  previous run on the same line).

## What this isn't

- **Not a non-parametric estimator** — we use linear adjustment OLS.
  For deep ANMs, normalising flows, or structural VAEs, post-process
  via your favourite causal-ML library.
- **Not a substitute for randomisation** — observational causal
  inference makes assumptions (causal Markov, faithfulness, no
  unmeasured confounders beyond adjustment set). The Studio prints
  these assumptions on every estimate so they stay visible.
- **Not a DAG editor yet** — the DAG comes from Aurora's
  relationships pipeline. A drag-and-drop DAG editor lands in a
  future stream once the engine has proven out on real workflows.

## See also

- [docs/methods.md](methods.md) — the relationships pipeline that
  feeds the DAG
- [docs/composable-findings.md](runs-library.md) — inherit a richer
  DAG from a previous run
- `fantasyai/aurora/causal/` — source of truth
