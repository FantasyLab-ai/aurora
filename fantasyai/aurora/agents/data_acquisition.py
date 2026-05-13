"""
Data acquisition agents — wake up, find new data, validate, queue for analysis.

Three concrete agents shipped:

* ``NOAAAcquisitionAgent``  — wraps the NOAA CDO connector
* ``FREDAcquisitionAgent``  — wraps the FRED connector
* ``CSVURLAcquisitionAgent`` — generic: takes a URL, fetches the CSV, validates

Each agent's contract is identical and minimal:

  task = {
      "query": str,            # what to look for (e.g. "GDP", "TMAX")
      "limit": int,            # how many results to consider this tick
      "outdir": str | None,    # where to drop the validated CSV (default: cache)
      "dry_run": bool,         # if True, only search; don't fetch
  }

The agent never analyzes the data. It only:
  1. Searches the underlying connector
  2. Picks the top result that passes structure validation
  3. Fetches it (or finds it in cache)
  4. Appends a "ready_for_analysis" decision to the run-log with full provenance

Failure modes are benign:
  - missing API key             → status = "blocked"
  - network error               → status = "failed" with the trace
  - search returns nothing      → status = "no_op"
  - structure validation fails  → status = "blocked" with reason
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import BaseAgent, AgentResult


# ---------------------------------------------------------------------------
# Helpers shared by agents
# ---------------------------------------------------------------------------

def _validate_csv_minimal(csv_path: Path) -> Dict[str, Any]:
    """Cheap structural sanity check: file exists, ≥ 2 rows, ≥ 1 numeric column.

    Returns ``{ok: bool, reason?, rows?, cols?, numeric_cols?}``.
    """
    try:
        import pandas as pd
        df = pd.read_csv(csv_path, low_memory=False, nrows=5000)
    except Exception as e:
        return {"ok": False, "reason": f"read_failed: {e}"}
    if len(df) < 2:
        return {"ok": False, "reason": "too_few_rows", "rows": int(len(df))}
    numeric_cols = [c for c in df.columns
                    if str(df[c].dtype) in ("int64", "float64", "Int64", "Float64")]
    if not numeric_cols:
        # Fall back: try to coerce — connector outputs are sometimes strings.
        import pandas as _pd
        coercible = [c for c in df.columns
                     if _pd.to_numeric(df[c], errors="coerce").notna().sum() >= 0.5 * len(df)]
        if not coercible:
            return {"ok": False, "reason": "no_numeric_columns",
                    "rows": int(len(df)), "cols": int(len(df.columns))}
        numeric_cols = coercible
    return {
        "ok": True,
        "rows": int(len(df)),
        "cols": int(len(df.columns)),
        "numeric_cols": numeric_cols[:10],
    }


# ---------------------------------------------------------------------------
# NOAA agent
# ---------------------------------------------------------------------------

class NOAAAcquisitionAgent(BaseAgent):
    agent_id = "agent_noaa"
    display_name = "NOAA acquisition agent"
    description = "Searches and fetches NOAA Climate Data Online station summaries."
    bounded_task_description = (
        "Find weather station data matching the given query and validate it."
    )

    def _do_run(self, task: Dict[str, Any]) -> AgentResult:
        from ..connectors import get_connector
        conn = get_connector("noaa_cdo")
        if conn is None:
            return AgentResult(
                agent_id=self.agent_id, task_id="", ts="",
                status="blocked",
                summary="NOAA connector not loaded — check connectors/registry.py",
            )
        if not conn.has_api_key():
            return AgentResult(
                agent_id=self.agent_id, task_id="", ts="",
                status="blocked",
                summary=f"Missing API key — set {conn.api_key_env_var} to enable.",
                decisions=[{"decision": "skip", "why": "no api key",
                            "confidence": 1.0, "alternatives_considered": []}],
            )
        query = (task or {}).get("query", "")
        limit = int((task or {}).get("limit", 5))
        dry = bool((task or {}).get("dry_run", False))

        try:
            previews = conn.search(query, limit=limit)
        except Exception as e:
            return AgentResult(
                agent_id=self.agent_id, task_id="", ts="", status="failed",
                summary=f"search failed: {e!s}", error=str(e),
            )
        if not previews:
            return AgentResult(
                agent_id=self.agent_id, task_id="", ts="", status="no_op",
                summary=f"NOAA returned 0 stations matching {query!r}",
            )
        if dry:
            return AgentResult(
                agent_id=self.agent_id, task_id="", ts="", status="ok",
                summary=f"DRY-RUN — would fetch {previews[0].title}",
                artifacts=[],
                decisions=[{"decision": "selected_preview",
                            "why": f"top match for {query!r}",
                            "confidence": 0.9,
                            "alternatives_considered":
                                [p.title for p in previews[1:3]]}],
            )
        # Fetch the top match.
        item = previews[0]
        try:
            res = conn.fetch(item.item_id)
        except Exception as e:
            return AgentResult(
                agent_id=self.agent_id, task_id="", ts="", status="failed",
                summary=f"fetch {item.item_id} failed: {e!s}", error=str(e),
            )
        v = _validate_csv_minimal(Path(res.csv_path))
        if not v["ok"]:
            return AgentResult(
                agent_id=self.agent_id, task_id="", ts="", status="blocked",
                summary=f"fetched but validation failed: {v.get('reason')}",
                artifacts=[{"kind": "csv", "path": res.csv_path,
                            "hash": res.response_hash}],
            )
        return AgentResult(
            agent_id=self.agent_id, task_id="", ts="", status="ok",
            summary=(f"NOAA: fetched {item.title} → "
                     f"{v['rows']} rows × {v['cols']} cols, "
                     f"numeric: {len(v['numeric_cols'])}"),
            artifacts=[{"kind": "csv", "path": res.csv_path,
                        "hash": res.response_hash,
                        "rows": v["rows"], "cols": v["cols"],
                        "cache_hit": res.cache_hit}],
            decisions=[{"decision": "ready_for_analysis",
                        "why": (f"top NOAA match for {query!r} passed "
                                f"structure validation"),
                        "confidence": 0.85,
                        "alternatives_considered":
                            [p.title for p in previews[1:3]]}],
        )


# ---------------------------------------------------------------------------
# FRED agent
# ---------------------------------------------------------------------------

class FREDAcquisitionAgent(BaseAgent):
    agent_id = "agent_fred"
    display_name = "FRED acquisition agent"
    description = "Searches and fetches Federal Reserve economic time series."
    bounded_task_description = (
        "Find an economic series matching the query and validate it."
    )

    def _do_run(self, task: Dict[str, Any]) -> AgentResult:
        from ..connectors import get_connector
        conn = get_connector("fred")
        if conn is None:
            return AgentResult(agent_id=self.agent_id, task_id="", ts="",
                               status="blocked",
                               summary="FRED connector not loaded")
        if not conn.has_api_key():
            return AgentResult(
                agent_id=self.agent_id, task_id="", ts="", status="blocked",
                summary=f"Missing API key — set {conn.api_key_env_var}.",
            )
        query = (task or {}).get("query", "GDP")
        limit = int((task or {}).get("limit", 5))
        dry = bool((task or {}).get("dry_run", False))
        try:
            previews = conn.search(query, limit=limit)
        except Exception as e:
            return AgentResult(agent_id=self.agent_id, task_id="", ts="",
                               status="failed",
                               summary=f"search failed: {e!s}", error=str(e))
        if not previews:
            return AgentResult(agent_id=self.agent_id, task_id="", ts="",
                               status="no_op",
                               summary=f"FRED returned 0 series for {query!r}")
        if dry:
            return AgentResult(
                agent_id=self.agent_id, task_id="", ts="", status="ok",
                summary=f"DRY-RUN — would fetch {previews[0].title}",
                decisions=[{"decision": "selected_preview",
                            "why": f"top FRED match for {query!r}",
                            "confidence": 0.9,
                            "alternatives_considered":
                                [p.title for p in previews[1:3]]}],
            )
        item = previews[0]
        try:
            res = conn.fetch(item.item_id)
        except Exception as e:
            return AgentResult(agent_id=self.agent_id, task_id="", ts="",
                               status="failed",
                               summary=f"fetch failed: {e!s}", error=str(e))
        v = _validate_csv_minimal(Path(res.csv_path))
        if not v["ok"]:
            return AgentResult(agent_id=self.agent_id, task_id="", ts="",
                               status="blocked",
                               summary=f"validation failed: {v.get('reason')}")
        return AgentResult(
            agent_id=self.agent_id, task_id="", ts="", status="ok",
            summary=(f"FRED: fetched {item.title} → "
                     f"{v['rows']} rows; numeric={len(v['numeric_cols'])}"),
            artifacts=[{"kind": "csv", "path": res.csv_path,
                        "hash": res.response_hash,
                        "rows": v["rows"], "cols": v["cols"],
                        "cache_hit": res.cache_hit}],
            decisions=[{"decision": "ready_for_analysis",
                        "why": f"FRED top match for {query!r} validated",
                        "confidence": 0.85,
                        "alternatives_considered":
                            [p.title for p in previews[1:3]]}],
        )


# ---------------------------------------------------------------------------
# Generic CSV-from-URL agent
# ---------------------------------------------------------------------------

class CSVURLAcquisitionAgent(BaseAgent):
    """Fetch a CSV from a user-supplied URL and validate it.

    Bounded task: takes a URL, downloads, validates, queues. No search,
    no domain knowledge, no analysis. Useful for any open dataset that
    isn't behind one of the named connectors.
    """

    agent_id = "agent_csv_url"
    display_name = "CSV-from-URL acquisition agent"
    description = "Downloads and validates a CSV from a user-supplied URL."
    bounded_task_description = "Download CSV from URL and validate structure."

    def __init__(self, cache_dir: Optional[Path] = None,
                 log_path: Optional[Path] = None):
        super().__init__(log_path=log_path)
        if cache_dir is None:
            repo_root = Path(__file__).resolve().parents[3]
            cache_dir = repo_root / "outputs" / "_data_cache" / "csv_url"
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _do_run(self, task: Dict[str, Any]) -> AgentResult:
        url = (task or {}).get("url")
        if not url:
            return AgentResult(agent_id=self.agent_id, task_id="", ts="",
                               status="no_op",
                               summary="task missing 'url'")
        # Cache key from URL hash.
        import hashlib, time as _time, os as _os
        h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        out = self._cache_dir / f"csv_url__{h}.csv"
        # Check cache freshness.
        ttl_days = int((task or {}).get("cache_ttl_days", 7))
        if out.exists():
            age_days = (_time.time() - out.stat().st_mtime) / 86400.0
            if age_days < ttl_days:
                v = _validate_csv_minimal(out)
                if v["ok"]:
                    return AgentResult(
                        agent_id=self.agent_id, task_id="", ts="", status="ok",
                        summary=(f"CSV-URL: cache hit ({age_days:.1f}d old) → "
                                 f"{v['rows']} rows × {v['cols']} cols"),
                        artifacts=[{"kind": "csv", "path": str(out),
                                    "rows": v["rows"], "cols": v["cols"],
                                    "cache_hit": True, "url": url}],
                        decisions=[{"decision": "ready_for_analysis",
                                    "why": "cache hit + structure validated",
                                    "confidence": 0.9,
                                    "alternatives_considered": []}],
                    )
        # Live fetch — but only if the user explicitly enables network.
        if (task or {}).get("offline", False):
            return AgentResult(agent_id=self.agent_id, task_id="", ts="",
                               status="blocked",
                               summary="offline mode + cache miss; cannot fetch")
        try:
            import requests
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            out.write_bytes(r.content)
        except Exception as e:
            return AgentResult(agent_id=self.agent_id, task_id="", ts="",
                               status="failed",
                               summary=f"download failed: {e!s}",
                               error=str(e))
        v = _validate_csv_minimal(out)
        if not v["ok"]:
            return AgentResult(agent_id=self.agent_id, task_id="", ts="",
                               status="blocked",
                               summary=f"validation failed: {v.get('reason')}",
                               artifacts=[{"kind": "csv", "path": str(out),
                                           "url": url}])
        return AgentResult(
            agent_id=self.agent_id, task_id="", ts="", status="ok",
            summary=(f"CSV-URL: downloaded {url} → "
                     f"{v['rows']} rows × {v['cols']} cols"),
            artifacts=[{"kind": "csv", "path": str(out),
                        "rows": v["rows"], "cols": v["cols"],
                        "url": url, "cache_hit": False}],
            decisions=[{"decision": "ready_for_analysis",
                        "why": "fresh download passed structure validation",
                        "confidence": 0.85,
                        "alternatives_considered": []}],
        )
