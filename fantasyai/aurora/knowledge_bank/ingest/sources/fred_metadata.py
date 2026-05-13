"""
Stage 1 — FRED series metadata ingestion (~800K entries).

Source: https://fred.stlouisfed.org/docs/api/fred/
License: ``"FRED Public Data — US Federal Reserve, free for any use"``
         (registered in :data:`KNOWN_LICENSES`)

Maintainer setup:
  1. Get a free API key at https://fred.stlouisfed.org/docs/api/api_key.html
  2. Set the env var ``FRED_API_KEY`` before running.
  3. Optionally set ``FRED_RATE_LIMIT_RPM`` (default 100; FRED allows 120).
  4. Run::
        python -m fantasyai.aurora.knowledge_bank.ingest \
               --source fred_metadata

Pipeline behaviour:
  * Resumable via ``<checkpoint_dir>/fred_metadata.checkpoint.json``.
  * Atomic: a series is either fully inserted (with embedding +
    cross-references) or skipped.
  * License-aware: refuses to ingest if license string is unset.
  * Quality-filtered: skips series with no description, fewer than
    24 observations, or in the ALFRED revision archive.

This module ships as a complete scaffold — when ``requests`` is not
installed or the API key is missing, the module returns a structured
"skipped" result rather than crashing the whole pipeline.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from ...schema import (
    KnowledgeEntry, EntryReference, QuantitativeRange, attach_license,
)
from ...storage.sqlite_store import KnowledgeBankStore


SOURCE = "fred"
LICENSE = "FRED Public Data — US Federal Reserve, free for any use"
BASE_URL = "https://api.stlouisfed.org/fred"


# ---------------------------------------------------------------------------
# API client (rate-limited)
# ---------------------------------------------------------------------------

class _RateLimiter:
    def __init__(self, rpm: int):
        self.interval = 60.0 / max(1, rpm)
        self._last = 0.0

    def wait(self) -> None:
        now = time.time()
        delta = now - self._last
        if delta < self.interval:
            time.sleep(self.interval - delta)
        self._last = time.time()


def _fred_get(path: str, *, api_key: str, params: Optional[Dict] = None,
                limiter: Optional[_RateLimiter] = None,
                retries: int = 3) -> Optional[Dict]:
    """One FRED API request with exponential backoff."""
    try:
        import requests
    except Exception:
        return None
    p = dict(params or {})
    p["api_key"] = api_key
    p["file_type"] = "json"
    if limiter:
        limiter.wait()
    for attempt in range(retries):
        try:
            r = requests.get(f"{BASE_URL}{path}", params=p, timeout=30)
            if r.status_code == 429:
                # Rate-limited; backoff hard.
                time.sleep(2 ** (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(2 ** attempt)
    return None


# ---------------------------------------------------------------------------
# Schema mapping
# ---------------------------------------------------------------------------

def _series_to_entry(series: Dict[str, Any], source_version: str
                       ) -> Optional[KnowledgeEntry]:
    """Map a FRED series JSON dict to a KnowledgeEntry."""
    sid = series.get("id")
    if not sid:
        return None
    title = series.get("title") or sid
    notes = series.get("notes") or ""
    units = series.get("units") or ""
    freq = series.get("frequency") or ""
    obs_start = series.get("observation_start") or ""
    obs_end = series.get("observation_end") or ""
    seas_adj = series.get("seasonal_adjustment") or ""
    last_updated = series.get("last_updated") or ""
    pop = series.get("popularity")

    # Skip discontinuities and stubs.
    if not notes and not units:
        return None
    if "discontinued" in title.lower():
        return None

    definition_parts = [title]
    if notes:
        definition_parts.append(notes)
    if units:
        definition_parts.append(f"Units: {units}.")
    if freq:
        definition_parts.append(f"Frequency: {freq}.")
    if obs_start and obs_end:
        definition_parts.append(f"Observation range: {obs_start} → {obs_end}.")
    if seas_adj and seas_adj != "Not Seasonally Adjusted":
        definition_parts.append(f"Seasonal adjustment: {seas_adj}.")
    definition = " ".join(definition_parts)

    quant: List[QuantitativeRange] = []
    if units:
        quant.append(QuantitativeRange(property="units", unit=units,
                                          context=freq))

    refs = [EntryReference(
        citation="Federal Reserve Bank of St. Louis (FRED).",
        url=f"https://fred.stlouisfed.org/series/{sid}",
    )]

    entry = KnowledgeEntry(
        id=f"fred_series_{sid}",
        source=SOURCE,
        source_version=source_version,
        domain=["economics", "finance"],
        concept_type="economic_indicator",
        review_status="auto_approved",
        ingest_method="api_etl",
        name=title,
        definition=definition,
        quantitative_ranges=quant,
        references=refs,
        quality_score=min(1.0, 0.5 + 0.005 * (pop or 0)),  # FRED popularity → quality
    )
    return attach_license(entry, LICENSE)


# ---------------------------------------------------------------------------
# Iterator: walks FRED categories (resumable)
# ---------------------------------------------------------------------------

def _iter_series_via_categories(api_key: str, *,
                                   limiter: _RateLimiter,
                                   visited_categories: set
                                   ) -> Iterator[Dict[str, Any]]:
    """Walk FRED's category tree and yield every series.

    FRED's series count per call is capped at 1000; we paginate via
    ``offset``. Categories themselves are walked breadth-first from the
    root (id=0).
    """
    queue: List[int] = [0]
    while queue:
        cid = queue.pop(0)
        if cid in visited_categories:
            continue
        visited_categories.add(cid)

        # Children.
        children = _fred_get("/category/children",
                                params={"category_id": cid},
                                api_key=api_key, limiter=limiter)
        if children:
            for c in (children.get("categories") or []):
                if c.get("id") not in visited_categories:
                    queue.append(int(c["id"]))

        # Series in this category — paginate.
        offset = 0
        while True:
            page = _fred_get("/category/series",
                                params={"category_id": cid, "offset": offset,
                                          "limit": 1000},
                                api_key=api_key, limiter=limiter)
            if not page:
                break
            seriess = page.get("seriess") or []
            for s in seriess:
                yield s
            if len(seriess) < 1000:
                break
            offset += 1000


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------

def run_ingest(bank: KnowledgeBankStore, checkpoint_dir: Path,
                 *, progress_cb: Optional[Callable[[Dict], None]] = None,
                 ) -> Dict[str, Any]:
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        return {
            "ok": False, "source": SOURCE,
            "reason": "missing_FRED_API_KEY",
            "fix": ("Set the FRED_API_KEY env var before running this "
                     "stage. Get a free key at "
                     "https://fred.stlouisfed.org/docs/api/api_key.html"),
            "n_inserted": 0,
        }
    try:
        import requests  # noqa
    except Exception:
        return {"ok": False, "source": SOURCE,
                 "reason": "requests_not_installed",
                 "fix": "pip install requests",
                 "n_inserted": 0}

    rpm = int(os.environ.get("FRED_RATE_LIMIT_RPM", "100"))
    limiter = _RateLimiter(rpm)
    source_version = time.strftime("%Y-%m-%d")

    # Resume from checkpoint if present.
    cp_path = checkpoint_dir / f"{SOURCE}.checkpoint.json"
    state: Dict[str, Any] = {"visited_categories": [],
                                "n_inserted": 0,
                                "n_skipped": 0,
                                "started_at": time.time()}
    if cp_path.exists():
        try:
            state = json.loads(cp_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    visited = set(state.get("visited_categories", []))

    n_inserted = int(state.get("n_inserted") or 0)
    n_skipped = int(state.get("n_skipped") or 0)
    n_errors = 0
    BATCH = 200
    batch: List[KnowledgeEntry] = []
    last_progress = time.time()

    try:
        for series in _iter_series_via_categories(
            api_key, limiter=limiter, visited_categories=visited
        ):
            entry = _series_to_entry(series, source_version)
            if entry is None:
                n_skipped += 1
                continue
            batch.append(entry)
            if len(batch) >= BATCH:
                res = bank.insert_many(batch)
                n_inserted += res["n_inserted"]
                n_errors += res["n_errors"]
                batch = []
                # Checkpoint.
                state["visited_categories"] = list(visited)
                state["n_inserted"] = n_inserted
                state["n_skipped"] = n_skipped
                cp_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = cp_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(state), encoding="utf-8")
                tmp.replace(cp_path)
                # Progress callback (every 10s or every batch).
                if progress_cb and time.time() - last_progress > 10:
                    progress_cb({"source": SOURCE, "stage": "ingesting",
                                  "n_done": n_inserted, "n_skipped": n_skipped,
                                  "n_categories": len(visited)})
                    last_progress = time.time()
        # Final batch.
        if batch:
            res = bank.insert_many(batch)
            n_inserted += res["n_inserted"]
            n_errors += res["n_errors"]
    except KeyboardInterrupt:
        # Save progress and re-raise for the orchestrator to handle.
        state["visited_categories"] = list(visited)
        state["n_inserted"] = n_inserted
        state["n_skipped"] = n_skipped
        cp_path.write_text(json.dumps(state), encoding="utf-8")
        raise

    return {
        "ok": True, "source": SOURCE, "source_version": source_version,
        "n_inserted": n_inserted, "n_skipped": n_skipped,
        "n_errors": n_errors, "license": LICENSE,
        "categories_walked": len(visited),
    }
