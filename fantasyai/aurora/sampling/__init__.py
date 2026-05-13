"""
Tiered analysis: T0 structure → T1 quick sample → T2 confirmation → T3 full.

Public API for the rest of Aurora:

    from fantasyai.aurora.sampling import (
        SamplingState, infer_structure,
        start_tiered_run, start_tier_3, cancel_run, is_cancelled,
        attach_replication, run_sampler, select_sampling_method,
    )

The orchestrator owns the state machine. ``state_builder`` reads the per-tier
child run dirs (recorded in _TIER_RUNS.json) and assembles a unified state
with replication_status attached at every level.
"""
from __future__ import annotations

from .state_machine import (
    SamplingState, TierStatus,
    SAMPLING_STATE_FILE, TIER_RUNS_FILE,
    write_tier_runs, read_tier_runs, update_tier_run,
)
from .shape_detector import (
    StructureProfile, ColumnProfile, infer_structure,
    SMALL_DATASET_ROW_THRESHOLD, SMALL_DATASET_BYTE_THRESHOLD,
    VERY_WIDE_COL_THRESHOLD,
)
from .samplers import (
    SampleManifest, run_sampler, select_sampling_method,
    make_seed, resolve_always_include, select_columns_by_variance,
)
from .replication import attach_replication
from .orchestrator import (
    start_tiered_run, start_tier_3, cancel_run, is_cancelled,
    TIER_BUDGETS,
)

__all__ = [
    # state machine
    "SamplingState", "TierStatus",
    "SAMPLING_STATE_FILE", "TIER_RUNS_FILE",
    "write_tier_runs", "read_tier_runs", "update_tier_run",
    # shape detector
    "StructureProfile", "ColumnProfile", "infer_structure",
    "SMALL_DATASET_ROW_THRESHOLD", "SMALL_DATASET_BYTE_THRESHOLD",
    "VERY_WIDE_COL_THRESHOLD",
    # samplers
    "SampleManifest", "run_sampler", "select_sampling_method",
    "make_seed", "resolve_always_include", "select_columns_by_variance",
    # replication
    "attach_replication",
    # orchestrator
    "start_tiered_run", "start_tier_3", "cancel_run", "is_cancelled",
    "TIER_BUDGETS",
]
