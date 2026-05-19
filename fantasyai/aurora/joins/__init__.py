"""
Aurora multi-dataset joins (Q3 Stream B).

A single Aurora run analyses one dataset in isolation. When you have
two related datasets (e.g., a sensor stream + the maintenance log
that references the same equipment, or a market dataset + the
fundamentals for the same tickers), running them separately misses
the cross-dataset structure.

This module composes two finished runs into a **join report**:

  * **Shared keys** — columns that exist (by name match or semantic
    inference) in both datasets, ranked by overlap quality.

  * **Cross-correlations** — for every shared key, the column-level
    correlation between A's and B's numeric columns when joined on
    that key.

  * **Finding inheritance** — A's headline findings (physics laws,
    regimes) become candidate priors for B's columns, and vice
    versa. The state_builder won't apply them automatically (that
    needs human review), but the report surfaces "A discovered
    logistic on its target; B might too" as a hint.

  * **Schema compatibility** — flags datasets whose row counts /
    cadences differ enough that join semantics matter (e.g.,
    one is daily, the other minutely — caller needs a window strategy).

The join report is a pure read-only computation over two run dirs;
it never modifies either source run. Frontend renders it in a
dedicated "JOINS" panel.
"""
from __future__ import annotations

from .joiner import (
    compute_join_report,
    JoinReport,
    JoinKey,
    CrossCorrelation,
)

__all__ = [
    "compute_join_report",
    "JoinReport",
    "JoinKey",
    "CrossCorrelation",
]
