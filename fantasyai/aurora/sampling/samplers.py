"""
Sampling implementations.

For each tier, given a StructureProfile + budget, produce a sampled CSV at a
deterministic temp path. The runner then runs on that CSV.

Methods (selected automatically by orchestrator from shape):
    stratified_temporal      — bucket by time, balance rows per bucket
    stratified_panel         — bucket by (entity, time)
    random_iid               — uniform random sampling
    variance_topk_columns    — column subsample for very-wide
    completeness_biased      — row-sample favoring dense rows for tall-sparse

Always-include rules: target column, time column, panel-id column,
prior-matched columns. Honoured at the column-selection step.

Determinism: every sample is seeded by SHA256(file_hash + tier + size)[:8].
Same file + tier + size → same sample, every time.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .shape_detector import StructureProfile


@dataclass
class SampleManifest:
    """Records exactly what was sampled and how. Travels with the sampled CSV."""
    schema: str = "sample_manifest_v1"
    tier: int = 1
    method: str = ""
    rows_sampled: int = 0
    cols_sampled: int = 0
    rows_total: int = 0
    cols_total: int = 0
    rows_fraction: float = 0.0
    cols_fraction: float = 1.0
    seed: str = ""
    n_strata: Optional[int] = None
    rows_per_stratum_mean: Optional[float] = None
    stratification_axis: Optional[str] = None
    columns_kept: List[str] = field(default_factory=list)
    columns_dropped: List[str] = field(default_factory=list)
    always_include: List[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Deterministic seed
# ---------------------------------------------------------------------------

def make_seed(file_path: Path, tier: int, size: int) -> str:
    """SHA256(first 1MB of file + tier + size)[:8] — deterministic per-file."""
    h = hashlib.sha256()
    try:
        with open(file_path, "rb") as fh:
            h.update(fh.read(1024 * 1024))
    except Exception:
        h.update(str(file_path).encode("utf-8"))
    h.update(f"::{tier}::{size}".encode("utf-8"))
    return h.hexdigest()[:8]


def seed_to_int(s: str) -> int:
    return int(s, 16)


# ---------------------------------------------------------------------------
# Always-include logic (column selection)
# ---------------------------------------------------------------------------

def resolve_always_include(profile: StructureProfile,
                            user_hints: Optional[List[str]] = None
                            ) -> Set[str]:
    """Columns that MUST survive any column-subsampling."""
    keep: Set[str] = set()
    if profile.time_col:
        keep.add(profile.time_col)
    if profile.panel_id_col:
        keep.add(profile.panel_id_col)
    # Highest-variance numeric column = likely target (we don't know yet).
    nums = [c for c in profile.columns if c.is_numeric and c.std]
    if nums:
        nums.sort(key=lambda c: -(c.std or 0))
        keep.add(nums[0].name)
    if user_hints:
        for h in user_hints:
            if any(c.name == h for c in profile.columns):
                keep.add(h)
    return keep


def select_columns_by_variance(profile: StructureProfile,
                               max_cols: int,
                               must_include: Set[str]) -> List[str]:
    """Pick top-k columns by variance × completeness, plus must-include.

    Returns the column-name list in original-file order so the produced CSV
    is readable/diffable.
    """
    # Score = (std or 0) * completeness; constant cols get 0; high-variance/dense wins.
    scored = []
    for c in profile.columns:
        score = float(c.std or 0.0) * float(c.completeness or 0.0)
        # Boost numeric over object.
        if c.is_numeric:
            score *= 1.5
        # Drop near-constants and near-empty.
        if c.completeness < 0.05:
            score = -1
        elif c.is_numeric and (c.std or 0) < 1e-9:
            score = -0.5
        scored.append((c.name, score))
    # Pin must-include with score=∞.
    must_set = set(must_include)
    final = {n for n in must_set}
    remaining = [s for s in scored if s[0] not in must_set and s[1] > 0]
    remaining.sort(key=lambda kv: -kv[1])
    for name, _ in remaining:
        if len(final) >= max_cols:
            break
        final.add(name)
    # Preserve original file column order.
    return [c.name for c in profile.columns if c.name in final]


# ---------------------------------------------------------------------------
# Sampling implementations — each returns (out_path, manifest)
# ---------------------------------------------------------------------------

def sample_stratified_temporal(file_path: Path, *, profile: StructureProfile,
                                rows_target: int, max_cols: int,
                                tier: int, out_path: Path,
                                user_hints: Optional[List[str]] = None,
                                ) -> SampleManifest:
    """Bucket by time, take ⌈rows_target/N⌉ rows per bucket."""
    import pandas as pd
    import numpy as np
    t0 = time.time()
    seed = make_seed(file_path, tier, rows_target)
    rng = np.random.default_rng(seed_to_int(seed))

    must_include = resolve_always_include(profile, user_hints)
    cols_keep = (select_columns_by_variance(profile, max_cols, must_include)
                 if profile.cols_total > max_cols
                 else [c.name for c in profile.columns])
    cols_dropped = [c.name for c in profile.columns if c.name not in cols_keep]

    n_strata = max(50, min(500, rows_target // 200))
    rows_per_stratum = max(1, rows_target // n_strata)

    # Read with usecols to save memory.
    time_col = profile.time_col
    chunks: List[pd.DataFrame] = []
    rows_seen = 0
    for chunk in pd.read_csv(file_path, usecols=cols_keep,
                              chunksize=200_000, low_memory=False,
                              on_bad_lines="skip"):
        chunks.append(chunk)
        rows_seen += len(chunk)
        # Cap memory: stop reading once we have ~3× rows_target lined up to
        # stratify from. Larger files just use the head N×rows_target.
        if rows_seen > rows_target * 3:
            break
    if not chunks:
        raise RuntimeError("no data could be read for stratified_temporal")
    df = pd.concat(chunks, ignore_index=True)
    # Parse time.
    ts = pd.to_datetime(df[time_col], errors="coerce")
    df = df.assign(_t=ts).dropna(subset=["_t"])
    df = df.sort_values("_t")
    # Stratify: pd.qcut into n_strata; sample each.
    try:
        df["_b"] = pd.qcut(df["_t"].astype("int64"), q=n_strata,
                            labels=False, duplicates="drop")
    except Exception:
        df["_b"] = pd.cut(df["_t"].astype("int64"), bins=n_strata,
                           labels=False, include_lowest=True)
    sampled = (df.groupby("_b", group_keys=False)
                  .apply(lambda g: g.sample(min(len(g), rows_per_stratum),
                                              random_state=seed_to_int(seed) % (2**32))))
    sampled = sampled.drop(columns=["_t", "_b"]).head(rows_target).reset_index(drop=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sampled.to_csv(out_path, index=False)

    return SampleManifest(
        tier=tier, method="stratified_temporal",
        rows_sampled=int(len(sampled)), cols_sampled=int(len(cols_keep)),
        rows_total=int(profile.rows_total), cols_total=int(profile.cols_total),
        rows_fraction=(len(sampled) / profile.rows_total
                        if profile.rows_total else 1.0),
        cols_fraction=(len(cols_keep) / profile.cols_total
                        if profile.cols_total else 1.0),
        seed=seed, n_strata=int(n_strata),
        rows_per_stratum_mean=float(rows_per_stratum),
        stratification_axis="time",
        columns_kept=cols_keep, columns_dropped=cols_dropped,
        always_include=sorted(must_include),
        elapsed_s=round(time.time() - t0, 2),
    )


def sample_stratified_panel(file_path: Path, *, profile: StructureProfile,
                             rows_target: int, max_cols: int,
                             tier: int, out_path: Path,
                             user_hints: Optional[List[str]] = None,
                             ) -> SampleManifest:
    """Bucket by (entity, time)."""
    import pandas as pd
    import numpy as np
    t0 = time.time()
    seed = make_seed(file_path, tier, rows_target)

    must_include = resolve_always_include(profile, user_hints)
    cols_keep = (select_columns_by_variance(profile, max_cols, must_include)
                 if profile.cols_total > max_cols
                 else [c.name for c in profile.columns])
    cols_dropped = [c.name for c in profile.columns if c.name not in cols_keep]

    chunks = []
    rows_seen = 0
    for chunk in pd.read_csv(file_path, usecols=cols_keep, chunksize=200_000,
                              low_memory=False, on_bad_lines="skip"):
        chunks.append(chunk)
        rows_seen += len(chunk)
        if rows_seen > rows_target * 3:
            break
    df = pd.concat(chunks, ignore_index=True)

    pid_col = profile.panel_id_col
    time_col = profile.time_col
    df = df.assign(_t=pd.to_datetime(df[time_col], errors="coerce"))
    df = df.dropna(subset=["_t"])

    n_entities = df[pid_col].nunique()
    n_strata_per_entity = max(20, rows_target // max(1, n_entities) // 50)
    rows_per_cell = max(1, rows_target // (n_entities * n_strata_per_entity))

    df["_b"] = pd.cut(df["_t"].astype("int64"), bins=n_strata_per_entity,
                       labels=False, include_lowest=True)
    sampled = (df.groupby([pid_col, "_b"], group_keys=False)
                  .apply(lambda g: g.sample(min(len(g), rows_per_cell),
                                              random_state=seed_to_int(seed) % (2**32))))
    sampled = sampled.drop(columns=["_t", "_b"]).head(rows_target).reset_index(drop=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sampled.to_csv(out_path, index=False)

    return SampleManifest(
        tier=tier, method="stratified_panel",
        rows_sampled=int(len(sampled)), cols_sampled=int(len(cols_keep)),
        rows_total=int(profile.rows_total), cols_total=int(profile.cols_total),
        rows_fraction=(len(sampled) / profile.rows_total
                        if profile.rows_total else 1.0),
        cols_fraction=(len(cols_keep) / profile.cols_total
                        if profile.cols_total else 1.0),
        seed=seed, n_strata=int(n_strata_per_entity),
        stratification_axis="entity_time",
        columns_kept=cols_keep, columns_dropped=cols_dropped,
        always_include=sorted(must_include),
        elapsed_s=round(time.time() - t0, 2),
    )


def sample_random_iid(file_path: Path, *, profile: StructureProfile,
                       rows_target: int, max_cols: int,
                       tier: int, out_path: Path,
                       user_hints: Optional[List[str]] = None,
                       ) -> SampleManifest:
    """Uniform random sampling without replacement."""
    import pandas as pd
    import numpy as np
    t0 = time.time()
    seed = make_seed(file_path, tier, rows_target)
    rng = np.random.default_rng(seed_to_int(seed))

    must_include = resolve_always_include(profile, user_hints)
    cols_keep = (select_columns_by_variance(profile, max_cols, must_include)
                 if profile.cols_total > max_cols
                 else [c.name for c in profile.columns])
    cols_dropped = [c.name for c in profile.columns if c.name not in cols_keep]

    n_total = profile.rows_total
    if rows_target >= n_total:
        df = pd.read_csv(file_path, usecols=cols_keep, low_memory=False,
                          on_bad_lines="skip")
    else:
        # Reservoir-style: read in chunks, keep rows w/ probability rows_target/n_total.
        keep_prob = rows_target / max(1, n_total) * 1.5  # over-sample then trim
        rows: List[pd.DataFrame] = []
        for chunk in pd.read_csv(file_path, usecols=cols_keep,
                                  chunksize=200_000, low_memory=False,
                                  on_bad_lines="skip"):
            mask = rng.random(len(chunk)) < keep_prob
            rows.append(chunk[mask])
            if sum(len(r) for r in rows) >= rows_target * 1.5:
                break
        df = pd.concat(rows, ignore_index=True).head(rows_target)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)

    return SampleManifest(
        tier=tier, method="random_iid",
        rows_sampled=int(len(df)), cols_sampled=int(len(cols_keep)),
        rows_total=int(profile.rows_total), cols_total=int(profile.cols_total),
        rows_fraction=(len(df) / profile.rows_total
                        if profile.rows_total else 1.0),
        cols_fraction=(len(cols_keep) / profile.cols_total
                        if profile.cols_total else 1.0),
        seed=seed,
        columns_kept=cols_keep, columns_dropped=cols_dropped,
        always_include=sorted(must_include),
        elapsed_s=round(time.time() - t0, 2),
    )


def sample_completeness_biased(file_path: Path, *, profile: StructureProfile,
                                rows_target: int, max_cols: int,
                                tier: int, out_path: Path,
                                user_hints: Optional[List[str]] = None,
                                ) -> SampleManifest:
    """For tall-sparse data: keep the rows with more non-null values."""
    import pandas as pd
    import numpy as np
    t0 = time.time()
    seed = make_seed(file_path, tier, rows_target)

    must_include = resolve_always_include(profile, user_hints)
    cols_keep = (select_columns_by_variance(profile, max_cols, must_include)
                 if profile.cols_total > max_cols
                 else [c.name for c in profile.columns])
    cols_dropped = [c.name for c in profile.columns if c.name not in cols_keep]

    rows: List[pd.DataFrame] = []
    for chunk in pd.read_csv(file_path, usecols=cols_keep, chunksize=200_000,
                              low_memory=False, on_bad_lines="skip"):
        chunk["_density"] = chunk.notna().sum(axis=1)
        rows.append(chunk)
    df = pd.concat(rows, ignore_index=True)
    # Take top rows_target by density (ties broken at random for fairness).
    rng = np.random.default_rng(seed_to_int(seed))
    df["_jitter"] = rng.random(len(df))
    df = df.sort_values(["_density", "_jitter"], ascending=[False, False])
    df = df.head(rows_target).drop(columns=["_density", "_jitter"]).reset_index(drop=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)

    return SampleManifest(
        tier=tier, method="completeness_biased",
        rows_sampled=int(len(df)), cols_sampled=int(len(cols_keep)),
        rows_total=int(profile.rows_total), cols_total=int(profile.cols_total),
        rows_fraction=(len(df) / profile.rows_total
                        if profile.rows_total else 1.0),
        cols_fraction=(len(cols_keep) / profile.cols_total
                        if profile.cols_total else 1.0),
        seed=seed,
        columns_kept=cols_keep, columns_dropped=cols_dropped,
        always_include=sorted(must_include),
        elapsed_s=round(time.time() - t0, 2),
        note="favours dense rows for tall-sparse data",
    )


# ---------------------------------------------------------------------------
# Dispatcher: pick method by shape, run it
# ---------------------------------------------------------------------------

def select_sampling_method(shape: str) -> str:
    """Map detected shape → sampler method id."""
    return {
        "time_series": "stratified_temporal",
        "panel":       "stratified_panel",
        "wide":        "random_iid",
        "very_wide":   "random_iid",        # cols sub-sampled separately
        "tall_sparse": "completeness_biased",
        "small":       "random_iid",        # n/a but harmless
        # Atom 2.2: network (edge list / OD) — edge-aware sampling lives in
        # WS-future; for now, random_iid preserves the relative frequency of
        # high-volume edges, which is what most logistics analyses care about.
        "network":     "random_iid",
    }.get(shape, "random_iid")


_DISPATCH = {
    "stratified_temporal": sample_stratified_temporal,
    "stratified_panel":    sample_stratified_panel,
    "random_iid":          sample_random_iid,
    "completeness_biased": sample_completeness_biased,
}


def run_sampler(method: str, file_path: Path, *, profile: StructureProfile,
                rows_target: int, max_cols: int, tier: int, out_path: Path,
                user_hints: Optional[List[str]] = None,
                ) -> SampleManifest:
    fn = _DISPATCH.get(method)
    if fn is None:
        raise ValueError(f"unknown sampling method: {method!r}")
    return fn(file_path, profile=profile, rows_target=rows_target,
               max_cols=max_cols, tier=tier, out_path=out_path,
               user_hints=user_hints)
